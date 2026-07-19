"""Production startup guard: auth_provider must be 'authentik'.

Firebase auth was retired, so 'authentik' is the only provider that can serve requests.
A non-authentik provider in production would leave every session resolver inert and lock
out all users, so the lifespan must fail fast on boot rather than serve a broken auth
surface.
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.main import app, lifespan


class _StubEngine:
    async def dispose(self):
        return None


@pytest.fixture
def _prod_env(monkeypatch):
    """A production-like config that passes the other two lifespan guards, so a raise
    can only come from the auth_provider guard."""
    monkeypatch.setattr(settings, "environment", "prod")
    monkeypatch.setattr(settings, "dev_auth_bypass", False)
    monkeypatch.setattr(settings, "maintenance_database_url", "postgresql+asyncpg://x/y")
    # Don't dispose the real engine on __aexit__ (would break other DB tests).
    monkeypatch.setattr("app.main.engine", _StubEngine())


async def test_prod_boot_fails_when_provider_not_authentik(_prod_env, monkeypatch):
    monkeypatch.setattr(settings, "auth_provider", "firebase")
    with pytest.raises(RuntimeError, match="COMPANION_AUTH_PROVIDER must be 'authentik'"):
        async with lifespan(app):
            pass  # pragma: no cover — the guard raises before yield


async def test_prod_boot_fails_when_provider_disabled(_prod_env, monkeypatch):
    monkeypatch.setattr(settings, "auth_provider", "disabled")
    with pytest.raises(RuntimeError, match="Firebase authentication has been removed"):
        async with lifespan(app):
            pass  # pragma: no cover


async def test_prod_boot_ok_when_authentik(_prod_env, monkeypatch):
    monkeypatch.setattr(settings, "auth_provider", "authentik")
    entered = False
    async with lifespan(app):
        entered = True
    assert entered


async def test_non_prod_does_not_enforce_provider(monkeypatch):
    """Outside prod the guard is not enforced (dev/test may run without it)."""
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "dev_auth_bypass", False)
    monkeypatch.setattr(settings, "auth_provider", "disabled")
    monkeypatch.setattr("app.main.engine", _StubEngine())
    async with lifespan(app):
        pass  # no raise
