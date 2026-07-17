"""Pre-PHI gate #3: a password reset must kill every live BFF session of that account.

A reset that only changes the Authentik credential leaves a stolen session cookie/bearer
working for the rest of its sliding TTL — so the person resetting BECAUSE they suspect
compromise never actually evicts the attacker. These tests are the load-bearing proof
that redeeming ``/api/v1/activation/set-password`` evicts prior sessions, that it evicts
ONLY that subject's, and that a session minted after the reset survives.

The store is the ``InMemorySessionStore`` double (it mirrors RedisSessionStore's encode /
fail-closed-parse / epoch semantics exactly), installed over ``session._store`` the same
way the dual-run tests do it. The Authentik admin HTTP seam is monkeypatched.
"""

from __future__ import annotations

import uuid

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.auth import session as session_module
from app.auth.session import InMemorySessionStore
from app.config import settings
from app.db import session as db_module
from app.models.activation_token import ActivationToken
from app.models.admin_user import AdminUser
from app.models.audit import AccountAuditLog
from app.models.enums import AccessTier, AccountStatus, RelationshipType
from app.models.trusted_contact import TrustedContact
from app.models.user import User
from app.services import activation_service
from tests.conftest import requires_db

PASSWORD = "sunny-meadow-lake-42"


def _client() -> AsyncClient:
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _install_store(monkeypatch) -> InMemorySessionStore:
    store = InMemorySessionStore()
    monkeypatch.setattr(session_module, "_store", store)
    return store


def _spy_idp(monkeypatch) -> list[tuple[str, str]]:
    """Stub the Authentik admin seam; returns the list of set-password calls."""
    calls: list[tuple[str, str]] = []

    async def _provision(email: str, name: str) -> None:
        return None

    async def _set_password(email: str, password: str) -> None:
        calls.append((email, password))

    monkeypatch.setattr("app.api.v1.activation.provision_authentik_account", _provision)
    monkeypatch.setattr("app.api.v1.activation.set_authentik_password", _set_password)
    return calls


async def _cleanup(*emails: str) -> None:
    async with db_module.async_session_factory() as s:
        for email in emails:
            await s.execute(
                delete(TrustedContact).where(TrustedContact.contact_email == email)
            )
            await s.execute(delete(User).where(User.email == email))
            await s.execute(delete(AdminUser).where(AdminUser.email == email))
            await s.execute(
                delete(ActivationToken).where(ActivationToken.email == email)
            )
            await s.execute(delete(AccountAuditLog).where(AccountAuditLog.email == email))
        await s.commit()


async def _seed_member(email: str, *, sub: str | None) -> uuid.UUID:
    async with db_module.async_session_factory() as s:
        u = User(
            email=email,
            preferred_name="P",
            display_name="P",
            account_status=AccountStatus.ACTIVE,
            external_subject_id=sub,
        )
        s.add(u)
        await s.commit()
        return u.id


async def _audit_events(email: str) -> list[str]:
    async with db_module.async_session_factory() as s:
        rows = (
            await s.execute(
                AccountAuditLog.__table__.select().where(
                    AccountAuditLog.email == email
                )
            )
        ).all()
    return [r.event for r in rows]


# ── 1. a reset kills every live session of that subject ─────────────────────────


@requires_db
async def test_set_password_revokes_all_sessions_for_subject(monkeypatch):
    """Two live sessions (e.g. phone + laptop) for the account → reset → both dead.

    This is the gate: without the epoch check in the store + the revoke hook in
    set-password, both sids keep resolving after the password changed."""
    monkeypatch.setattr(settings, "auth_provider", "authentik")
    store = _install_store(monkeypatch)
    _spy_idp(monkeypatch)
    email = f"rev-both-{uuid.uuid4()}@t.io"
    sub = f"sub-{uuid.uuid4().hex}"
    await _cleanup(email)
    await _seed_member(email, sub=sub)

    sid_phone = await store.create(sub)
    sid_laptop = await store.create(sub)
    assert await store.get(sid_phone) == sub  # both live before the reset
    assert await store.get(sid_laptop) == sub

    token = await activation_service.issue_activation_token(email)
    async with _client() as ac:
        r = await ac.post(
            "/api/v1/activation/set-password",
            json={"token": token, "password": PASSWORD},
        )
    assert r.status_code == 200, r.text

    assert await store.get(sid_phone) is None
    assert await store.get(sid_laptop) is None
    # The eviction is durably audited.
    assert "sessions_revoked" in await _audit_events(email)
    await _cleanup(email)


# ── 2. blast radius: a DIFFERENT subject's session is untouched ──────────────────


@requires_db
async def test_reset_does_not_revoke_other_subjects(monkeypatch):
    """Revocation is scoped to the resetting account — everyone else stays logged in."""
    monkeypatch.setattr(settings, "auth_provider", "authentik")
    store = _install_store(monkeypatch)
    _spy_idp(monkeypatch)
    email = f"rev-mine-{uuid.uuid4()}@t.io"
    other_email = f"rev-other-{uuid.uuid4()}@t.io"
    sub = f"sub-{uuid.uuid4().hex}"
    other_sub = f"sub-{uuid.uuid4().hex}"
    await _cleanup(email, other_email)
    await _seed_member(email, sub=sub)
    await _seed_member(other_email, sub=other_sub)

    mine = await store.create(sub)
    theirs = await store.create(other_sub)

    token = await activation_service.issue_activation_token(email)
    async with _client() as ac:
        r = await ac.post(
            "/api/v1/activation/set-password",
            json={"token": token, "password": PASSWORD},
        )
    assert r.status_code == 200, r.text

    assert await store.get(mine) is None
    assert await store.get(theirs) == other_sub  # untouched
    await _cleanup(email, other_email)


# ── 3. the epoch is a watermark, not a ban: a NEW session still works ────────────


@requires_db
async def test_session_minted_after_reset_still_works(monkeypatch):
    """The user logs back in with the new password right after resetting — that session
    must survive. A revoke implemented as "block this subject" would break login."""
    monkeypatch.setattr(settings, "auth_provider", "authentik")
    store = _install_store(monkeypatch)
    _spy_idp(monkeypatch)
    email = f"rev-new-{uuid.uuid4()}@t.io"
    sub = f"sub-{uuid.uuid4().hex}"
    await _cleanup(email)
    await _seed_member(email, sub=sub)

    old_sid = await store.create(sub)
    token = await activation_service.issue_activation_token(email)
    async with _client() as ac:
        r = await ac.post(
            "/api/v1/activation/set-password",
            json={"token": token, "password": PASSWORD},
        )
    assert r.status_code == 200, r.text

    new_sid = await store.create(sub)  # the post-reset login
    assert await store.get(old_sid) is None
    assert await store.get(new_sid) == sub
    await _cleanup(email)


# ── 4. a caregiver's sessions are revoked too (subject on trusted_contacts) ──────


@requires_db
async def test_reset_revokes_caregiver_sessions(monkeypatch):
    """Caregivers hold BFF sessions but have no ``users`` row of their own — their
    subject lives on the ACTIVE trusted_contacts row. A member-shaped-only lookup would
    silently skip them (they can reset their password, so they must be revocable)."""
    monkeypatch.setattr(settings, "auth_provider", "authentik")
    store = _install_store(monkeypatch)
    _spy_idp(monkeypatch)
    member_email = f"rev-cg-member-{uuid.uuid4()}@t.io"
    cg_email = f"rev-cg-{uuid.uuid4()}@t.io"
    cg_sub = f"sub-{uuid.uuid4().hex}"
    await _cleanup(member_email, cg_email)
    member_id = await _seed_member(member_email, sub=None)
    async with db_module.async_session_factory() as s:
        s.add(
            TrustedContact(
                user_id=member_id,
                contact_name="Care Giver",
                contact_email=cg_email,
                relationship_type=RelationshipType.FAMILY,
                access_tier=AccessTier.TIER_2,
                is_active=True,
                external_subject_id=cg_sub,
            )
        )
        await s.commit()

    sid = await store.create(cg_sub)
    token = await activation_service.issue_activation_token(cg_email)
    async with _client() as ac:
        r = await ac.post(
            "/api/v1/activation/set-password",
            json={"token": token, "password": PASSWORD},
        )
    assert r.status_code == 200, r.text
    assert await store.get(sid) is None
    await _cleanup(member_email, cg_email)


# ── 5. no sessions / no subject → the activation still succeeds ──────────────────


@requires_db
async def test_activation_with_no_subject_is_a_noop(monkeypatch):
    """First-time activation: the account has never logged in via Authentik, so
    external_subject_id is NULL and there is nothing to revoke. Must not error — the
    hook is unconditional (the ``reset=1`` marker is untrusted client input)."""
    monkeypatch.setattr(settings, "auth_provider", "authentik")
    store = _install_store(monkeypatch)
    idp_calls = _spy_idp(monkeypatch)
    email = f"rev-nosub-{uuid.uuid4()}@t.io"
    await _cleanup(email)
    await _seed_member(email, sub=None)

    token = await activation_service.issue_activation_token(email)
    async with _client() as ac:
        r = await ac.post(
            "/api/v1/activation/set-password",
            json={"token": token, "password": PASSWORD},
        )
    assert r.status_code == 200, r.text
    assert idp_calls == [(email, PASSWORD)]
    assert store._epochs == {}  # nothing to revoke
    assert "sessions_revoked" not in await _audit_events(email)
    await _cleanup(email)


# ── 6. a revocation failure must NOT fail the password set ───────────────────────


@requires_db
async def test_revocation_failure_does_not_fail_password_set(monkeypatch):
    """The password is already changed by the time we revoke, so a Redis hiccup must not
    500 the request and strand the caller unsure of their own credential. Best-effort."""
    monkeypatch.setattr(settings, "auth_provider", "authentik")
    store = _install_store(monkeypatch)
    idp_calls = _spy_idp(monkeypatch)
    email = f"rev-fail-{uuid.uuid4()}@t.io"
    sub = f"sub-{uuid.uuid4().hex}"
    await _cleanup(email)
    await _seed_member(email, sub=sub)

    async def _boom(subject: str) -> None:
        raise RuntimeError("redis unreachable")

    monkeypatch.setattr(store, "revoke_all_for_subject", _boom)

    token = await activation_service.issue_activation_token(email)
    async with _client() as ac:
        r = await ac.post(
            "/api/v1/activation/set-password",
            json={"token": token, "password": PASSWORD},
        )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "email": email}
    assert idp_calls == [(email, PASSWORD)]  # the password DID change
    await _cleanup(email)


# ── 7. store unit: legacy bare-string values fail CLOSED ─────────────────────────


async def test_legacy_bare_string_session_fails_closed():
    """A session written by the pre-epoch code is a bare subject with no ``iat``, so it
    cannot be compared against a revocation epoch. Honouring it would be exactly the hole
    this gate closes (a session that survives a reset) — so ``get`` refuses and drops it.

    Cost: sessions minted before the deploy are logged out once. That is the correct
    trade for a security control, and the same reason a null/corrupt value fails closed.
    """
    store = InMemorySessionStore()
    store._d["legacy-sid"] = "sub-legacy"  # what the old code wrote

    assert await store.get("legacy-sid") is None
    assert "legacy-sid" not in store._d  # and it's cleaned up


async def test_corrupt_session_value_fails_closed():
    """Anything unparseable (truncated write, wrong shape) is treated as no session."""
    store = InMemorySessionStore()
    store._d["bad-json"] = "{not json"
    store._d["no-iat"] = '{"sub": "sub-x"}'
    store._d["empty-sub"] = '{"sub": "", "iat": 1.0}'

    assert await store.get("bad-json") is None
    assert await store.get("no-iat") is None
    assert await store.get("empty-sub") is None


async def test_revoke_all_for_subject_unit():
    """Store-level: revoke kills pre-existing sids for that subject only, and a sid
    minted afterwards is unaffected."""
    store = InMemorySessionStore()
    a1 = await store.create("sub-a")
    a2 = await store.create("sub-a")
    b1 = await store.create("sub-b")

    await store.revoke_all_for_subject("sub-a")

    assert await store.get(a1) is None
    assert await store.get(a2) is None
    assert await store.get(b1) == "sub-b"
    a3 = await store.create("sub-a")
    assert await store.get(a3) == "sub-a"
