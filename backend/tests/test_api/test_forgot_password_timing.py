"""/auth/forgot-password must not leak account existence through RESPONSE TIME.

A byte-identical body (asserted in test_forgot_password.py) is not sufficient on its
own. When the account lookup + token write + SMTP round-trip ran inline, the
account-exists path was measurably slower than the no-account path — measured at
91.4ms vs 56.7ms, a 34.7ms / 1.6x oracle — which re-leaks exactly what the identical
body exists to hide. The work now runs in a BackgroundTask, after the response.

These tests measure what an ATTACKER measures: the moment the response body is
written. Starlette sends the response and only THEN awaits background tasks, so timing
the whole ASGI call (what httpx's ASGITransport does) would wrongly include the
background work and let a regression pass. We drive the ASGI app directly and timestamp
`http.response.body` instead.

The margins are deliberately enormous (a 2s send vs a 250ms budget) so this guards the
property without being timing-flaky on a loaded CI box.
"""

from __future__ import annotations

import asyncio
import json
import time

from app.config import settings
from app.main import app
from app.models.enums import AccountStatus
from tests.conftest import requires_db
from tests.test_api.test_forgot_password import (
    _delete_user,
    _enable_authentik,
    _seed_user,
)

pytestmark = requires_db

# Far larger than any real relay round-trip, so an inline send is unmistakable.
_SLOW_SEND_S = 2.0
# Generous enough for a loaded CI box, tiny next to _SLOW_SEND_S.
_RESPONSE_BUDGET_MS = 250.0
# Max allowed exists-vs-unknown difference. Deliberately well BELOW the 35ms oracle this
# file was written to kill, so a re-introduced (even partial) inline path fails here.
_MAX_DELTA_MS = 20.0


async def _ms_until_response_sent(email: str) -> float:
    """Milliseconds until the response body is written — excludes background tasks."""
    body = json.dumps({"email": email}).encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.1"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/auth/forgot-password",
        "raw_path": b"/auth/forgot-password",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"test"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        "client": ("127.0.0.1", 5555),
        "server": ("test", 80),
    }
    sent_at: dict[str, float] = {}

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        if message["type"] == "http.response.body" and not message.get("more_body"):
            sent_at.setdefault("t", time.perf_counter())

    started = time.perf_counter()
    await app(scope, receive, send)
    return (sent_at["t"] - started) * 1000


def _patch_slow_send(monkeypatch) -> None:
    async def _slow(to_email: str, to_name: str, reset_url: str) -> bool:
        await asyncio.sleep(_SLOW_SEND_S)
        return True

    monkeypatch.setattr("app.api.auth_authentik.send_password_reset_email", _slow)


@requires_db
async def test_response_does_not_wait_on_the_reset_send(monkeypatch):
    """An existing account must not pay for the token write + SMTP round-trip."""
    _enable_authentik(monkeypatch)
    monkeypatch.setattr(settings, "reset_max_attempts", 10**6)
    monkeypatch.setattr(settings, "reset_email_max_per_window", 10**6)
    _patch_slow_send(monkeypatch)

    email = "timing-exists@example.invalid"
    await _delete_user(email)
    await _seed_user(email, status=AccountStatus.ACTIVE)
    try:
        elapsed = await _ms_until_response_sent(email)
    finally:
        await _delete_user(email)

    assert elapsed < _RESPONSE_BUDGET_MS, (
        f"response took {elapsed:.0f}ms with a {_SLOW_SEND_S}s send — the reset work is "
        "back on the request path, which re-opens the timing oracle"
    )


@requires_db
async def test_existing_and_unknown_addresses_are_timing_indistinguishable(monkeypatch):
    """The whole point: both paths must cost the same from the client's view."""
    _enable_authentik(monkeypatch)
    monkeypatch.setattr(settings, "reset_max_attempts", 10**6)
    monkeypatch.setattr(settings, "reset_email_max_per_window", 10**6)
    _patch_slow_send(monkeypatch)

    existing = "timing-exists@example.invalid"
    missing = "timing-nobody@example.invalid"
    await _delete_user(existing)
    await _seed_user(existing, status=AccountStatus.ACTIVE)
    try:
        for _ in range(2):  # warm up
            await _ms_until_response_sent(missing)
            await _ms_until_response_sent(existing)
        # NB: list comprehensions, not generators — `min(await ... for ...)` builds an
        # async generator that min() cannot iterate.
        exists_ms = min([await _ms_until_response_sent(existing) for _ in range(5)])
        missing_ms = min([await _ms_until_response_sent(missing) for _ in range(5)])
    finally:
        await _delete_user(existing)

    # Both must be far below the send delay; if the exists-path awaited the send it
    # would be ~_SLOW_SEND_S while the unknown path stayed near zero.
    assert exists_ms < _RESPONSE_BUDGET_MS
    assert missing_ms < _RESPONSE_BUDGET_MS

    # ...and, per the test's name, they must be indistinguishable FROM EACH OTHER.
    # The budget check alone is not enough: a future regression that put, say, only the
    # token write back inline would add tens of ms to the exists-path, stay under the
    # budget, and slip through — yet still be a usable oracle (the bug this file exists
    # for measured just 35ms). Comparing min-of-N samples strips scheduler noise, so the
    # real delta is sub-millisecond and this ceiling is ~1000x headroom over noise while
    # still catching an oracle smaller than the original.
    delta_ms = abs(exists_ms - missing_ms)
    assert delta_ms < _MAX_DELTA_MS, (
        f"exists={exists_ms:.2f}ms vs unknown={missing_ms:.2f}ms (delta {delta_ms:.2f}ms) "
        "— the request path is doing existence-dependent work again, which re-opens the "
        "timing oracle even though both are under the budget"
    )
