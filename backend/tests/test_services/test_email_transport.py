"""email_service transport guards: bounded timeout + never blocking the event loop.

These are REGRESSION tests for a live availability bug, not style checks.

`_send_smtp` uses stdlib smtplib, which is BLOCKING and — critically — defaults to
NO socket timeout. Two things must therefore hold, forever:

1. Every SMTP conversation is bounded by `smtp_timeout_seconds`. Without it, a
   hung/blackholed relay blocks the caller indefinitely.
2. `send_email` hands that blocking call to a worker thread. If it is ever awaited
   inline again, one slow relay stalls the event loop and every other request on
   that worker stalls with it.

Both were true in prod the moment email started actually sending (before that the
send short-circuited to log-only, which is why it went unnoticed).
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock, patch

from app.config import settings
from app.integrations import email_service


def test_smtp_conversation_is_bounded_by_a_timeout(monkeypatch):
    """smtplib defaults to no timeout — assert we always pass one."""
    monkeypatch.setattr(settings, "smtp_host", "mail-relay.test")
    monkeypatch.setattr(settings, "smtp_port", 25)
    monkeypatch.setattr(settings, "smtp_timeout_seconds", 7.5)

    with patch.object(email_service.smtplib, "SMTP") as smtp:
        smtp.return_value.__enter__.return_value = MagicMock()
        email_service._send_smtp("to@example.invalid", "N", "subj", "body", None)

    assert smtp.call_args.kwargs.get("timeout") == 7.5, (
        "smtplib.SMTP must be given an explicit timeout; without one a hung relay "
        "blocks forever"
    )


async def test_send_email_does_not_block_the_event_loop():
    """A slow relay must not stall the loop.

    If the blocking send were awaited inline, these would serialize (~5x the delay).
    Run in threads, they overlap.
    """
    delay = 0.4

    def slow_blocking_send(*_args, **_kwargs):
        time.sleep(delay)  # a slow relay, blocking
        return True

    with patch.object(email_service, "_send_smtp", slow_blocking_send):
        started = time.perf_counter()
        results = await asyncio.gather(
            *[
                email_service.send_email(f"a{i}@example.invalid", "N", "s", "b")
                for i in range(5)
            ]
        )
        elapsed = time.perf_counter() - started

    assert all(results)
    # Serialized would be ~5 * delay. Allow generous headroom for slow CI while still
    # failing loudly if the send goes back on the loop.
    assert elapsed < delay * 3, (
        f"5 concurrent sends took {elapsed:.2f}s (~{delay * 5:.1f}s serialized) — the "
        "blocking SMTP call is back on the event loop"
    )


async def test_event_loop_keeps_running_during_a_send():
    """Direct proof the loop is live while a blocking send is in flight."""
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    def slow_blocking_send(*_args, **_kwargs):
        time.sleep(0.3)
        return True

    with patch.object(email_service, "_send_smtp", slow_blocking_send):
        task = asyncio.create_task(ticker())
        await email_service.send_email("x@example.invalid", "N", "s", "b")
        task.cancel()

    assert ticks > 0, "the event loop was stalled for the whole send"
