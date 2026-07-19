"""Authentik invite-accept flow: an invitee authenticates with their Authentik BFF
session to accept/decline THEIR OWN invitation.

``/accept`` + ``/decline`` in ``app/api/v1/invitations.py`` resolve the session holder's
IdP-verified email via ``resolve_caregiver_session``. There is NO login-admission change —
an invitee already gets a session because ``create_member_invitation`` seeds an INVITED
``users`` stub for their email, which ``/auth/login`` admits (INVITED is not an inactive
status) and binds to their subject; ``_email_for_subject`` then recovers the email from
that stub. The real authorization stays the invitation token + ``contact_email`` match
inside the service.

Covers:
  * ``/accept`` + ``/decline`` under an Authentik session (subject bound to the stub).
  * the full production cascade against the REAL ``/auth/login`` endpoint.

IdP flow + Redis session store are mocked; a real Postgres session is used (mirrors
tests/test_api/test_authentik_login.py). ``authentik_login_enabled`` is derived from
``auth_provider`` — set explicitly via monkeypatch rather than trusting env.
"""

from __future__ import annotations

import uuid

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

import app.auth.session as session_module
from app.auth.session import InMemorySessionStore
from app.config import settings
from app.db import session as db_module
from app.models.audit import AccountAuditLog
from app.models.enums import (
    AccessTier,
    AccountStatus,
    InvitationStatus,
    RelationshipType,
)
from app.models.trusted_contact import TrustedContact
from app.models.user import User
from tests.conftest import requires_db


async def _delete_user(email: str) -> None:
    async with db_module.async_session_factory() as s:
        await s.execute(delete(User).where(User.email == email))
        await s.execute(delete(AccountAuditLog).where(AccountAuditLog.email == email))
        await s.commit()


async def _seed_member_with_pending_invitee(
    member_email: str,
    cg_email: str,
    *,
    stub_subject: str | None = None,
    token: str | None = None,
) -> uuid.UUID:
    """Create an active member + a PENDING (is_active=False) TrustedContact for cg_email +
    an INVITED stub user for the caregiver — exactly what ``create_member_invitation``
    produces. ``stub_subject`` backfills the stub's ``external_subject_id`` to simulate the
    post-``/auth/login`` state (login binds the subject to the users stub). Returns the
    member id."""
    async with db_module.async_session_factory() as s:
        # Defensive: clear residue from a prior failed run.
        await s.execute(
            delete(TrustedContact).where(TrustedContact.contact_email == cg_email)
        )
        await s.commit()
    async with db_module.async_session_factory() as s:
        member = User(
            email=member_email,
            preferred_name="M",
            display_name="M",
            account_status=AccountStatus.ACTIVE,
        )
        s.add(member)
        # The invited caregiver stub (account_status INVITED). Its subject is backfilled
        # by /auth/login in production; stub_subject simulates that post-login state.
        s.add(
            User(
                email=cg_email,
                preferred_name="C",
                display_name="C",
                account_status=AccountStatus.INVITED,
                external_subject_id=stub_subject,
            )
        )
        await s.flush()
        s.add(
            TrustedContact(
                user_id=member.id,
                contact_name="Care Giver",
                contact_email=cg_email,
                relationship_type=RelationshipType.FAMILY,
                access_tier=AccessTier.TIER_2,
                is_active=False,
                invitation_status=InvitationStatus.PENDING,
                invitation_token=token,
            )
        )
        await s.commit()
        return member.id


def _client() -> AsyncClient:
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _mute_accept_side_effects(monkeypatch) -> None:
    """Keep the accept/decline member-notification side effects inert + offline."""

    async def _noop_email(**kwargs):  # noqa: ARG001
        return True

    async def _noop_push(*args, **kwargs):  # noqa: ARG002
        return None

    monkeypatch.setattr(
        "app.api.v1.invitations.send_invitation_accepted_notification", _noop_email
    )
    monkeypatch.setattr(
        "app.services.push_notification_service.notify_caregiver_status_change",
        _noop_push,
    )


# ── /invitations/accept + /decline under an Authentik BFF session ──


@requires_db
async def test_accept_endpoint_authentik_session_invitee(monkeypatch):
    """Under Authentik: an invitee with a BFF cookie session (no bearer) accepts their own
    invite. resolve_caregiver_session recovers their IdP-verified email from the subject
    bound to their INVITED users stub at login."""
    monkeypatch.setattr(settings, "auth_provider", "authentik")
    member_email = f"accs-member-{uuid.uuid4()}@t.io"
    cg_email = f"accs-cg-{uuid.uuid4()}@t.io"
    token = f"tok-{uuid.uuid4().hex}"
    sub = f"sub-accs-{uuid.uuid4()}"
    await _delete_user(member_email)
    await _delete_user(cg_email)
    # Post-login state: the subject is backfilled on the INVITED stub (as /auth/login does).
    await _seed_member_with_pending_invitee(
        member_email, cg_email, stub_subject=sub, token=token
    )
    store = InMemorySessionStore()
    monkeypatch.setattr(session_module, "_store", store)
    sid = await store.create(sub)
    _mute_accept_side_effects(monkeypatch)

    async with _client() as ac:
        r = await ac.post(
            "/api/v1/invitations/accept",
            json={"token": token},
            # Cookie session + double-submit CSRF (POST is state-changing).
            cookies={settings.session_cookie_name: sid, settings.csrf_cookie_name: "t"},
            headers={"X-CSRF-Token": "t"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["accepted"] is True
    async with db_module.async_session_factory() as s:
        tc = (
            await s.execute(
                select(TrustedContact).where(TrustedContact.contact_email == cg_email)
            )
        ).scalar_one()
        assert tc.invitation_status == InvitationStatus.ACCEPTED
        assert tc.is_active is True
    await _delete_user(member_email)
    await _delete_user(cg_email)


@requires_db
async def test_accept_endpoint_authentik_session_missing_csrf_403(monkeypatch):
    """A cookie session on a state-changing POST without the double-submit X-CSRF-Token is
    refused (403) — the CSRF enforcement in resolve_session_subject applies to this endpoint
    like every other session-authenticated unsafe method."""
    monkeypatch.setattr(settings, "auth_provider", "authentik")
    member_email = f"csrf-member-{uuid.uuid4()}@t.io"
    cg_email = f"csrf-cg-{uuid.uuid4()}@t.io"
    token = f"tok-{uuid.uuid4().hex}"
    sub = f"sub-csrf-{uuid.uuid4()}"
    await _delete_user(member_email)
    await _delete_user(cg_email)
    await _seed_member_with_pending_invitee(
        member_email, cg_email, stub_subject=sub, token=token
    )
    store = InMemorySessionStore()
    monkeypatch.setattr(session_module, "_store", store)
    sid = await store.create(sub)
    _mute_accept_side_effects(monkeypatch)

    async with _client() as ac:
        r = await ac.post(
            "/api/v1/invitations/accept",
            json={"token": token},
            cookies={settings.session_cookie_name: sid, settings.csrf_cookie_name: "t"},
            # No X-CSRF-Token header.
        )
    assert r.status_code == 403
    # The invitation was NOT accepted.
    async with db_module.async_session_factory() as s:
        tc = (
            await s.execute(
                select(TrustedContact).where(TrustedContact.contact_email == cg_email)
            )
        ).scalar_one()
        assert tc.invitation_status == InvitationStatus.PENDING
        assert tc.is_active is False
    await _delete_user(member_email)
    await _delete_user(cg_email)


@requires_db
async def test_decline_endpoint_authentik_session_invitee(monkeypatch):
    """Mirror of accept: an invitee with a BFF session declines their own invite."""
    monkeypatch.setattr(settings, "auth_provider", "authentik")
    member_email = f"dec-member-{uuid.uuid4()}@t.io"
    cg_email = f"dec-cg-{uuid.uuid4()}@t.io"
    token = f"tok-{uuid.uuid4().hex}"
    sub = f"sub-dec-{uuid.uuid4()}"
    await _delete_user(member_email)
    await _delete_user(cg_email)
    await _seed_member_with_pending_invitee(
        member_email, cg_email, stub_subject=sub, token=token
    )
    store = InMemorySessionStore()
    monkeypatch.setattr(session_module, "_store", store)
    sid = await store.create(sub)
    _mute_accept_side_effects(monkeypatch)

    async with _client() as ac:
        r = await ac.post(
            "/api/v1/invitations/decline",
            json={"token": token},
            cookies={settings.session_cookie_name: sid, settings.csrf_cookie_name: "t"},
            headers={"X-CSRF-Token": "t"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["declined"] is True
    async with db_module.async_session_factory() as s:
        tc = (
            await s.execute(
                select(TrustedContact).where(TrustedContact.contact_email == cg_email)
            )
        ).scalar_one()
        assert tc.invitation_status == InvitationStatus.DECLINED
        assert tc.is_active is False
    await _delete_user(member_email)
    await _delete_user(cg_email)


# ── 3. Full production cascade against the REAL /auth/login endpoint ──


@requires_db
async def test_real_login_then_accept_for_member_invited_pending_caregiver(monkeypatch):
    """PRODUCTION cascade: create_member_invitation creates an INVITED users STUB alongside
    the pending TrustedContact, so /auth/login resolves the pending caregiver via the
    by-email USERS branch (INVITED is not an inactive status) and backfills the subject onto
    the USERS row — it does NOT reach _try_caregiver_login. The accept then works because
    resolve_caregiver_session recovers the SAME email from the users table. Asserts the
    end-to-end path with the real login endpoint (IdP + Redis mocked)."""
    import app.auth.ratelimit as ratelimit_module
    from app.auth.oidc import VerifiedToken
    from app.auth.ratelimit import InMemoryRateLimiter

    monkeypatch.setattr("app.api.auth_authentik.settings.auth_provider", "authentik")
    monkeypatch.setattr(settings, "auth_provider", "authentik")

    member_email = f"e2e-member-{uuid.uuid4()}@t.io"
    cg_email = f"e2e-cg-{uuid.uuid4()}@t.io"
    token = f"tok-{uuid.uuid4().hex}"
    sub = f"sub-e2e-{uuid.uuid4()}"
    await _delete_user(member_email)
    await _delete_user(cg_email)
    # Exactly what create_member_invitation produces (stub subject NOT yet set — login sets it).
    await _seed_member_with_pending_invitee(member_email, cg_email, token=token)

    class _FakeAuthenticator:
        async def authenticate(self, username, password):  # noqa: ARG002
            from app.auth.authentik_flow import TokenResult

            return TokenResult(id_token="fake-id-token", access_token=None)

    class _FakeVerifier:
        def verify(self, token, *, require_issuer=True):  # noqa: ARG002
            return VerifiedToken(
                sub=sub, email=cg_email, name="C", claims={}, email_verified=True
            )

    monkeypatch.setattr(
        "app.api.auth_authentik._authenticator", lambda: _FakeAuthenticator()
    )
    monkeypatch.setattr(
        "app.api.auth_authentik.get_authentik_verifier", lambda: _FakeVerifier()
    )
    monkeypatch.setattr(ratelimit_module, "_limiter", InMemoryRateLimiter())
    store = InMemorySessionStore()
    monkeypatch.setattr(session_module, "_store", store)
    _mute_accept_side_effects(monkeypatch)

    async with _client() as ac:
        login = await ac.post(
            "/auth/login", json={"username": cg_email, "password": "pw", "mobile": True}
        )
        assert login.status_code == 200, login.text
        body = login.json()
        sid = body["session_token"]
        csrf = body["csrf_token"]
        # The subject was backfilled onto the INVITED users stub (the member branch), NOT
        # onto the pending TC (never reached _try_caregiver_login) — confirming the cascade.
        async with db_module.async_session_factory() as s:
            stub = (
                await s.execute(select(User).where(User.email == cg_email))
            ).scalar_one()
            assert stub.external_subject_id == sub
            assert stub.account_status == AccountStatus.INVITED
            tc = (
                await s.execute(
                    select(TrustedContact).where(
                        TrustedContact.contact_email == cg_email
                    )
                )
            ).scalar_one()
            assert tc.external_subject_id is None
            assert tc.invitation_status == InvitationStatus.PENDING

        # Accept via the bearer session (mobile). resolve_caregiver_session recovers the
        # email from the users table → accept_invitation matches contact_email.
        accept = await ac.post(
            "/api/v1/invitations/accept",
            json={"token": token},
            headers={"Authorization": f"Bearer {sid}", "X-CSRF-Token": csrf},
        )
    assert accept.status_code == 200, accept.text
    assert accept.json()["accepted"] is True
    async with db_module.async_session_factory() as s:
        tc = (
            await s.execute(
                select(TrustedContact).where(TrustedContact.contact_email == cg_email)
            )
        ).scalar_one()
        assert tc.invitation_status == InvitationStatus.ACCEPTED
        assert tc.is_active is True
    await _delete_user(member_email)
    await _delete_user(cg_email)
