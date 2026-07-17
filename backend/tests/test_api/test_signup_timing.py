"""/auth/signup must not leak account existence through RESPONSE TIME.

Same defect and same reasoning as test_forgot_password_timing.py — signup's is worse:
run inline, the brand-new branch pays a stub INSERT *plus* an Authentik provisioning
round-trip *plus* the SMTP send, while an already-ACTIVE address returns almost at once.
The byte-identical body (asserted in test_signup.py) does not hide that.

These measure what an ATTACKER measures: the moment the response body is written.
Starlette sends the response and only THEN awaits background tasks, so timing the whole
ASGI call (what httpx's ASGITransport does) would wrongly include the background work
and let a regression pass. We drive the ASGI app directly and timestamp
`http.response.body`.

Margins are deliberately enormous so this is not timing-flaky on a loaded CI box.
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

# Far larger than any real provisioning/SMTP round-trip, so an inline path is obvious.
_SLOW_S = 2.0
_RESPONSE_BUDGET_MS = 250.0
# Max allowed new-vs-existing difference. Well below the oracle an inline path creates.
_MAX_DELTA_MS = 20.0


async def _ms_until_response_sent(email: str, name: str = "Timing") -> float:
    """Milliseconds until the response body is written — excludes background tasks."""
    body = json.dumps({"email": email, "name": name}).encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.1"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/auth/signup",
        "raw_path": b"/auth/signup",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"test"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        "client": ("127.0.0.1", 5556),
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


def _patch_slow_seams(monkeypatch) -> None:
    """Make the brand-new branch's extra work unmistakably slow."""

    async def _slow_activation(email: str, name: str) -> None:
        await asyncio.sleep(_SLOW_S)

    async def _slow_provision(email: str, name: str) -> None:
        await asyncio.sleep(_SLOW_S)

    monkeypatch.setattr(
        "app.api.auth_authentik.send_activation_if_enabled", _slow_activation
    )
    monkeypatch.setattr(
        "app.services.invitation_service.provision_authentik_account", _slow_provision
    )


@requires_db
async def test_response_does_not_wait_on_signup_side_effects(monkeypatch):
    """A brand-new address must not pay for the stub + provision + send."""
    _enable_authentik(monkeypatch)
    monkeypatch.setattr(settings, "signup_max_attempts", 10**6)
    monkeypatch.setattr(settings, "signup_email_max_per_window", 10**6)
    _patch_slow_seams(monkeypatch)

    email = "timing-brand-new@example.invalid"
    await _delete_user(email)
    try:
        elapsed = await _ms_until_response_sent(email)
    finally:
        await _delete_user(email)

    assert elapsed < _RESPONSE_BUDGET_MS, (
        f"response took {elapsed:.0f}ms with {_SLOW_S}s seams — signup's side effects are "
        "back on the request path, which re-opens the timing oracle"
    )


@requires_db
async def test_new_and_existing_addresses_are_timing_indistinguishable(monkeypatch):
    """The whole point: a brand-new address and an already-ACTIVE one must cost the same."""
    _enable_authentik(monkeypatch)
    monkeypatch.setattr(settings, "signup_max_attempts", 10**6)
    monkeypatch.setattr(settings, "signup_email_max_per_window", 10**6)
    _patch_slow_seams(monkeypatch)

    existing = "timing-signup-active@example.invalid"
    brand_new = "timing-signup-new@example.invalid"
    await _delete_user(existing)
    await _delete_user(brand_new)
    await _seed_user(existing, status=AccountStatus.ACTIVE)
    try:
        for _ in range(2):  # warm up
            await _ms_until_response_sent(existing)
        # NB: list comprehensions, not generators — min(await ... for ...) builds an
        # async generator that min() cannot iterate.
        existing_ms = min([await _ms_until_response_sent(existing) for _ in range(5)])
        new_ms = min([await _ms_until_response_sent(brand_new) for _ in range(5)])
    finally:
        await _delete_user(existing)
        await _delete_user(brand_new)

    assert existing_ms < _RESPONSE_BUDGET_MS
    assert new_ms < _RESPONSE_BUDGET_MS

    # ...and indistinguishable FROM EACH OTHER. The budget alone is not enough: a partial
    # regression (e.g. only the lookup back inline) would stay under it yet still be a
    # usable oracle. min-of-N strips scheduler noise, so the real delta is sub-ms.
    delta_ms = abs(new_ms - existing_ms)
    assert delta_ms < _MAX_DELTA_MS, (
        f"brand-new={new_ms:.2f}ms vs existing={existing_ms:.2f}ms (delta {delta_ms:.2f}ms) "
        "— the request path is doing existence-dependent work again"
    )
