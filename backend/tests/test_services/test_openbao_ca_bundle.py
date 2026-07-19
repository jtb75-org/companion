"""OpenBao Transit CA-bundle verify wiring (TLS phase A — inert by default).

These tests are hermetic (no live OpenBao). They assert only how the *owned*
``httpx.Client`` is constructed:

- setting UNSET  → ``verify=True`` (today's default trust; a no-op override);
- setting SET    → ``verify=<ca_bundle_path>`` (verify against the internal CA
  once the OpenBao listener flips from tls_disable=1 to https).

Kept in a standalone module so the ``get_client``-patching autouse fixture in
``test_openbao_transit.py`` does not interfere with exercising the real builder.
"""

from __future__ import annotations

import httpx

from app.config import settings
from app.services import openbao_transit


def test_client_default_verify_when_ca_bundle_unset(monkeypatch):
    """Setting UNSET → owned httpx.Client uses default trust (verify=True)."""
    captured: dict = {}

    class _SpyClient:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(httpx, "Client", _SpyClient)
    openbao_transit.OpenBaoTransitClient(
        addr="http://openbao.test:8200", transit_key="k"
    )
    assert captured.get("verify") is True


def test_client_verifies_against_ca_bundle_when_set(monkeypatch):
    """Setting SET → owned httpx.Client is built with verify=<path>."""
    captured: dict = {}

    class _SpyClient:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(httpx, "Client", _SpyClient)
    openbao_transit.OpenBaoTransitClient(
        addr="https://openbao.test:8200",
        transit_key="k",
        ca_bundle_path="/etc/authentik-ca/ca.crt",
    )
    assert captured.get("verify") == "/etc/authentik-ca/ca.crt"


def test_injected_http_client_ignores_ca_bundle(monkeypatch):
    """An injected client is used as-is (no verify override; tests/DI path)."""
    sentinel = object()

    def _boom(*args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("owned client must not be built when one is injected")

    monkeypatch.setattr(httpx, "Client", _boom)
    client = openbao_transit.OpenBaoTransitClient(
        addr="https://openbao.test:8200",
        transit_key="k",
        ca_bundle_path="/etc/authentik-ca/ca.crt",
        http_client=sentinel,  # type: ignore[arg-type]
    )
    assert client._http is sentinel


def test_get_client_threads_ca_bundle_setting(monkeypatch):
    """get_client() passes settings.openbao_ca_bundle_path into the owned client."""
    captured: dict = {}

    class _SpyClient:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(settings, "openbao_addr", "https://openbao.test:8200")
    monkeypatch.setattr(
        settings, "openbao_ca_bundle_path", "/etc/authentik-ca/ca.crt"
    )
    monkeypatch.setattr(httpx, "Client", _SpyClient)
    openbao_transit.reset_client()
    try:
        openbao_transit.get_client()
        assert captured.get("verify") == "/etc/authentik-ca/ca.crt"
    finally:
        openbao_transit.reset_client()


def test_get_client_default_verify_when_setting_unset(monkeypatch):
    """get_client() with the setting unset builds a default-trust client (inert)."""
    captured: dict = {}

    class _SpyClient:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(settings, "openbao_addr", "http://openbao.test:8200")
    monkeypatch.setattr(settings, "openbao_ca_bundle_path", "")
    monkeypatch.setattr(httpx, "Client", _SpyClient)
    openbao_transit.reset_client()
    try:
        openbao_transit.get_client()
        assert captured.get("verify") is True
    finally:
        openbao_transit.reset_client()
