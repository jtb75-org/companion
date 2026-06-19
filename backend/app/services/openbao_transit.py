"""OpenBao Transit client — remote DEK wrapping (KEK never lives in the app).

This backs the per-tenant envelope encryption in ``field_crypto``. Instead of a
local KEK wrapping each user's DEK, the DEK is wrapped via OpenBao's Transit
secrets engine: the KEK material stays inside OpenBao, so the app can wrap and
unwrap DEKs but can never read or export the KEK. That enables real
break-glass (revoke/seal at OpenBao) and a Transit-side audit trail of every
DEK encrypt/decrypt.

Authentication is OpenBao **Kubernetes auth**: the pod presents its mounted
ServiceAccount JWT to ``auth/kubernetes/login``; OpenBao validates it with the
cluster's TokenReview API and issues a short-lived client token bound to a
policy that allows only ``transit/encrypt/<key>`` and
``transit/decrypt/<key>``. We cache the client token and re-login on a 403 or
when it nears expiry.

Transit operations used:
- ``transit/encrypt/<key>`` with ``plaintext`` = base64(DEK) -> ``vault:vN:...``
- ``transit/decrypt/<key>`` with that ciphertext -> base64(DEK)

The ``vault:vN:...`` token is what ``field_crypto`` stores in
``user_encryption_keys.wrapped_dek`` (utf-8 bytes). ``vN`` is the Transit key
version, so OpenBao-side key rotation (``rotate`` + ``min_decryption_version``)
works without re-wrapping app-side.

Fail-closed: when OpenBao is configured, every error here raises
``OpenBaoTransitError``. ``field_crypto`` never silently falls back to a local
KEK when OpenBao is configured.
"""

from __future__ import annotations

import logging
import threading
import time

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Path to the pod's projected ServiceAccount token (Kubernetes default mount).
_SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"  # noqa: S105

# Re-login this many seconds before the lease actually expires, so we never
# present a token that expires mid-flight.
_TOKEN_RENEW_SKEW_SECONDS = 60.0

# HTTP timeout for every OpenBao call (login, encrypt, decrypt).
_HTTP_TIMEOUT_SECONDS = 10.0


class OpenBaoTransitError(RuntimeError):
    """Any failure talking to OpenBao Transit (fail-closed signal)."""


def is_configured() -> bool:
    """True when an OpenBao address is set (prod path); False -> local KEK."""
    return bool(settings.openbao_addr)


class OpenBaoTransitClient:
    """Minimal httpx-backed OpenBao Transit client with k8s-auth + token cache.

    One instance is shared process-wide (see ``get_client``). It is safe for
    concurrent use: token state is guarded by a lock and login is idempotent.
    """

    def __init__(
        self,
        addr: str,
        transit_key: str,
        *,
        transit_mount: str = "transit",
        k8s_role: str = "companion",
        k8s_auth_mount: str = "kubernetes",
        sa_token_path: str = _SA_TOKEN_PATH,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._addr = addr.rstrip("/")
        self._transit_key = transit_key
        self._transit_mount = transit_mount.strip("/")
        self._k8s_role = k8s_role
        self._k8s_auth_mount = k8s_auth_mount.strip("/")
        self._sa_token_path = sa_token_path

        # Owned client unless one is injected (tests inject a mock-friendly one).
        self._http = http_client or httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS)

        self._lock = threading.Lock()
        self._client_token: str | None = None
        # Monotonic deadline after which the cached token must be refreshed.
        self._token_deadline: float = 0.0

    # -- ServiceAccount JWT --------------------------------------------------

    def _read_sa_jwt(self) -> str:
        try:
            with open(self._sa_token_path, encoding="utf-8") as fh:
                jwt = fh.read().strip()
        except OSError as exc:
            raise OpenBaoTransitError(
                f"cannot read ServiceAccount token at {self._sa_token_path!r} "
                "(is the pod running in-cluster with a projected SA token?)"
            ) from exc
        if not jwt:
            raise OpenBaoTransitError(
                f"ServiceAccount token at {self._sa_token_path!r} is empty"
            )
        return jwt

    # -- Kubernetes auth login ----------------------------------------------

    def _login(self) -> str:
        """POST the SA JWT to auth/kubernetes/login; cache the client token."""
        jwt = self._read_sa_jwt()
        url = f"{self._addr}/v1/auth/{self._k8s_auth_mount}/login"
        try:
            resp = self._http.post(
                url, json={"role": self._k8s_role, "jwt": jwt}
            )
        except httpx.HTTPError as exc:
            raise OpenBaoTransitError(
                f"OpenBao login transport error: {exc}"
            ) from exc
        if resp.status_code != 200:
            raise OpenBaoTransitError(
                f"OpenBao k8s login failed "
                f"(role={self._k8s_role!r}, status={resp.status_code})"
            )
        body = resp.json()
        auth = body.get("auth") or {}
        token = auth.get("client_token")
        if not token:
            raise OpenBaoTransitError(
                "OpenBao k8s login returned no client_token"
            )
        lease = float(auth.get("lease_duration") or 0)
        with self._lock:
            self._client_token = token
            # Renew a little before true expiry; lease 0 (non-renewable) -> use
            # the token once and re-login each call (rare; mainly dev quirks).
            self._token_deadline = time.monotonic() + max(
                lease - _TOKEN_RENEW_SKEW_SECONDS, 0.0
            )
        logger.info(
            "OpenBao k8s login ok (role=%s, lease=%ss)", self._k8s_role, lease
        )
        return token

    def _current_token(self) -> str:
        """Return a cached token, logging in if absent or near expiry."""
        with self._lock:
            token = self._client_token
            fresh = token is not None and time.monotonic() < self._token_deadline
        if fresh:
            return token  # type: ignore[return-value]
        return self._login()

    def _invalidate_token(self) -> None:
        with self._lock:
            self._client_token = None
            self._token_deadline = 0.0

    # -- Transit request with one 403 re-login retry ------------------------

    def _transit_request(self, op: str, payload: dict) -> dict:
        """POST to transit/<op>/<key>; on 403 re-login once and retry."""
        url = f"{self._addr}/v1/{self._transit_mount}/{op}/{self._transit_key}"
        for attempt in (1, 2):
            token = self._current_token()
            try:
                resp = self._http.post(
                    url, headers={"X-Vault-Token": token}, json=payload
                )
            except httpx.HTTPError as exc:
                raise OpenBaoTransitError(
                    f"OpenBao transit/{op} transport error: {exc}"
                ) from exc

            if resp.status_code == 403 and attempt == 1:
                # Token expired/revoked — drop it and re-login once.
                logger.warning(
                    "OpenBao transit/%s 403 — re-authenticating", op
                )
                self._invalidate_token()
                continue
            if resp.status_code != 200:
                raise OpenBaoTransitError(
                    f"OpenBao transit/{op} failed (status={resp.status_code})"
                )
            data = (resp.json() or {}).get("data") or {}
            return data
        # Unreachable: the loop either returns or raises.
        raise OpenBaoTransitError(f"OpenBao transit/{op} failed after retry")

    # -- Public encrypt / decrypt -------------------------------------------

    def encrypt(self, plaintext_b64: str) -> str:
        """Wrap ``plaintext_b64`` (already base64) -> ``vault:vN:...`` token."""
        data = self._transit_request("encrypt", {"plaintext": plaintext_b64})
        ciphertext = data.get("ciphertext")
        if not ciphertext:
            raise OpenBaoTransitError("transit/encrypt returned no ciphertext")
        return ciphertext

    def decrypt(self, ciphertext: str) -> str:
        """Unwrap a ``vault:vN:...`` token -> base64 plaintext (the DEK b64)."""
        data = self._transit_request("decrypt", {"ciphertext": ciphertext})
        plaintext_b64 = data.get("plaintext")
        if not plaintext_b64:
            raise OpenBaoTransitError("transit/decrypt returned no plaintext")
        return plaintext_b64


# ---------------------------------------------------------------------------
# Process-wide singleton (built from settings; resettable for tests)
# ---------------------------------------------------------------------------

_client_lock = threading.Lock()
_client: OpenBaoTransitClient | None = None


def get_client() -> OpenBaoTransitClient:
    """Return the shared Transit client, building it from settings on first use.

    Caller must have checked ``is_configured()`` (raises if no address).
    """
    global _client
    if not settings.openbao_addr:
        raise OpenBaoTransitError(
            "OpenBao Transit requested but COMPANION_OPENBAO_ADDR is unset"
        )
    with _client_lock:
        if _client is None:
            _client = OpenBaoTransitClient(
                addr=settings.openbao_addr,
                transit_key=settings.openbao_transit_key,
                transit_mount=settings.openbao_transit_mount,
                k8s_role=settings.openbao_k8s_role,
                k8s_auth_mount=settings.openbao_k8s_auth_mount,
            )
        return _client


def reset_client() -> None:
    """Drop the cached client (tests that patch settings call this)."""
    global _client
    with _client_lock:
        _client = None
