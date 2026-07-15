from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "COMPANION_"}

    # Database
    database_url: str = "postgresql+asyncpg://companion:companion_dev@localhost:5432/companion"
    database_echo: bool = False
    # Separate connection as the BYPASSRLS `companion_maintenance` role, used ONLY
    # by internal/worker cross-user operations (discovery scans that RLS would
    # fail-close) — see app/db/session.get_maintenance_session_factory (WS1
    # Phase 2c). Empty = not configured; using it while unset raises. Deliberately
    # a distinct role/connection so the normal `companion_app` runtime can never
    # escalate to a bypass role.
    maintenance_database_url: str = ""

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Google Cloud (still used for Gemini/Vertex generation)
    gcp_project_id: str = "companion-dev"
    pubsub_emulator_host: str | None = None
    # GCP Pub/Sub was retired in the self-hosted migration (topics no longer
    # exist). When disabled, the event publisher dispatches to in-process local
    # handlers instead of attempting a doomed Pub/Sub publish (which 404s and
    # stalls the caller ~3s per event). Set true only to use Pub/Sub/emulator.
    pubsub_enabled: bool = False
    # Pipeline-stage observability events were written to GCP Firestore, also
    # retired in the migration (the DB `documents.status` + admin pipeline-health
    # endpoint cover status now). When disabled, publish_pipeline_event is a no-op
    # instead of failing every stage write and logging a warning per stage.
    firestore_pipeline_events: bool = False

    # Field-level encryption — local AES-256-GCM key (base64 of 32 bytes:
    # `openssl rand -base64 32`), delivered via SealedSecret. Required
    # outside development/test.
    #
    # Legacy single global key. Still honored as the implicit ``k1`` KEK
    # for back-compat (decrypts pre-existing ``f1:`` ciphertext and acts as
    # the legacy single-key path). New deployments should set
    # ``field_keyring`` instead.
    field_encryption_key: str = ""

    # Versioned KEK keyring (envelope encryption). JSON of the form:
    #   {"primary": "k2", "keys": {"k1": "<b64-32>", "k2": "<b64-32>"}}
    # The ``primary`` key wraps newly-created per-user DEKs; every key id
    # in ``keys`` can still unwrap DEKs sealed under it (KEK rotation).
    # If unset, ``field_encryption_key`` is used as an implicit ``k1``.
    # Delivered via SealedSecret. Required outside development/test.
    field_keyring: str = ""

    # OpenBao Transit — remote KEK for wrapping per-tenant DEKs. When
    # ``openbao_addr`` is set, ``field_crypto`` wraps/unwraps each user's DEK
    # via OpenBao's Transit engine (the KEK never lives in the app) instead of
    # the local KEK keyring above. When empty (dev/test and the current
    # deploy until OpenBao is wired) the local KEK path is used. When set in
    # prod and OpenBao is unreachable, DEK wrap/unwrap FAILS CLOSED (raises;
    # no silent local fallback). Auth is Kubernetes auth: the api pod presents
    # its ServiceAccount JWT to ``auth/<k8s_auth_mount>/login`` under the
    # role ``openbao_k8s_role``. See services/openbao_transit.py.
    openbao_addr: str = ""  # e.g. http://openbao.openbao.svc.cluster.local:8200
    openbao_transit_key: str = "companion-kek"
    openbao_transit_mount: str = "transit"
    openbao_k8s_role: str = "companion"
    openbao_k8s_auth_mount: str = "kubernetes"
    # OpenBao's `companion` k8s-auth role requires audience=openbao, but the pod's
    # DEFAULT SA token has audience=[kube-apiserver] and is rejected (403 invalid
    # audience). Gitops mounts a projected SA token with `audience: openbao` at
    # this path; point the transit client at it. Defaults to the k8s default token
    # for dev/test/back-compat (where the role has no audience requirement).
    openbao_sa_token_path: str = "/var/run/secrets/kubernetes.io/serviceaccount/token"  # noqa: S105

    # Dedicated field-level key for high-sensitivity field TYPES (SSN, bank
    # account numbers, MRN, etc.) — a single per-field-type key, NOT
    # per-user. JSON of the form: {"primary": "fl1", "keys": {"fl1": "<b64-32>"}}.
    # Capability only today (no column uses it); see field_crypto.py §7.
    field_level_keyring: str = ""

    # Object storage (S3-compatible, MinIO) — replaces GCS.
    s3_endpoint_url: str = ""  # e.g. http://minio.minio.svc.cluster.local:9000
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_bucket_documents: str = "companion-documents"
    s3_region: str = "us-east-1"

    # Firebase
    firebase_project_id: str = "companion-dev"

    # ── Authentik BFF native login (PR #2 of the Firebase→Authentik migration) ──
    # Master auth switch. DEFAULT "firebase": every existing endpoint keeps
    # verifying Firebase ID tokens exactly as it does today — this PR does NOT
    # rewire the ~10 verify_firebase_token call sites. Setting "authentik" only
    # turns ON the additive BFF /auth/login|/auth/logout endpoints below (which
    # are otherwise 404/inert); it does NOT yet change how existing endpoints
    # authenticate. The real cutover (rewiring get_current_user et al.) is a
    # later PR. So this flag is safe to leave at "firebase" in prod: nothing
    # authenticates via Authentik until then.
    auth_provider: str = "firebase"  # firebase | authentik

    # companion-authentik OIDC — consumed ONLY by the BFF path above. In-cluster
    # base URL for the server-side flow driver + token exchange (no browser).
    authentik_internal_url: str = "http://companion-authentik-server.companion-authentik.svc"
    # CA bundle (PEM path) to verify Authentik's TLS when authentik_internal_url is https.
    # Empty → httpx default (system CAs). In prod set to the mounted internal-CA ca.crt so
    # the BFF↔Authentik channel — which carries the user password (flow executor) + the
    # id_token — is encrypted AND server-authenticated (cutover gate #2). Inert while
    # auth_provider=firebase (the flow authenticator is never constructed).
    authentik_ca_bundle_path: str = ""
    # The Authentik authentication flow slug the executor drives.
    authentik_auth_flow_slug: str = "companion-authentication-flow"
    # OIDC client (application/provider) credentials. The client_id is a PUBLIC
    # client identifier (not a secret): it is the ``aud`` the OIDCVerifier checks,
    # so it must be present for the Authentik path to verify tokens. Defaults to
    # the real companion-authentik provider client_id; override via env if the
    # provider is re-created. The client_secret stays env/SealedSecret-driven.
    authentik_oidc_client_id: str = "Jc9eGA2hKkQatYjpDfr0Q0zt9k3RrUHGNYxrukut"
    authentik_oidc_client_secret: str = ""  # noqa: S105
    # Authentik ADMIN API token, used ONLY to provision Authentik user accounts
    # (see app/integrations/authentik_admin.py) at Companion account-creation
    # seams. Supplied from OpenBao via the companion-secrets Sealed Secret as
    # COMPANION_AUTHENTIK_API_TOKEN; empty ⇒ provisioning is inert (no HTTP). It
    # reuses authentik_internal_url + authentik_ca_bundle_path for the channel.
    authentik_api_token: str = ""  # noqa: S105
    # Public issuer + JWKS (for the future browser bearer path, verified with
    # require_issuer=True). BFF-fetched in-cluster id_tokens are verified with
    # require_issuer=False because issuer_mode=per_provider stamps the internal
    # host as `iss` (signature+audience still prove provenance). See oidc.py.
    authentik_oidc_issuer: str = ""
    authentik_oidc_jwks_uri: str = ""
    # OIDC audience (== client_id for Authentik). Defaults to client_id via
    # `oidc_audience` below when left empty.
    authentik_oidc_audience: str = ""
    # Redirect URI registered on the Authentik provider for the code exchange.
    bff_oidc_redirect_uri: str = "http://localhost:5173/auth/callback"

    # BFF session cookie + double-submit CSRF cookie (browser/app SPA).
    session_cookie_name: str = "companion_sid"
    csrf_cookie_name: str = "companion_csrf"
    session_cookie_secure: bool = True
    session_cookie_domain: str = ""  # empty → host-only cookie
    session_ttl_seconds: int = 60 * 60 * 8  # 8h sliding

    # Password-strength floor enforced on the branded set-password seams
    # (/invitations/set-password, /activation/set-password). The Authentik admin
    # set_password API bypasses Authentik's own flow password policy, so this — plus
    # the app-side denylist/predictability screen — is the strength gate. Tunable.
    password_min_length: int = 10

    # Login throttle (per username + per client IP, fixed window).
    login_max_attempts: int = 10
    login_window_seconds: int = 300
    # Self-signup throttle (per client IP, reuses login_window_seconds). Tighter than
    # login: an unauthenticated account-creation + outbound-email endpoint is the #1
    # abuse surface, so cap sign-up attempts per IP/window lower. This bounds both
    # bulk account creation AND email-bombing an existing INVITED address (each
    # re-fire costs one hit against this counter).
    signup_max_attempts: int = 5
    # Per-EMAIL activation-mail cap (per window). A second, address-keyed bound so an
    # attacker rotating IPs can't trickle activation emails at one victim's address:
    # once this many sends fire for an email in a window, further signups for it become
    # silent no-ops (the response stays byte-identical — anti-enumeration is preserved).
    signup_email_max_per_window: int = 3
    # Whether to trust the raw X-Forwarded-For chain for the login rate-limit client
    # IP. cf-connecting-ip (set by Cloudflare, unspoofable via the cloudflared tunnel)
    # is always trusted; XFF is client-injectable unless a trusted proxy owns it, so it
    # is only consulted when this is True. Default False → a spoofed XFF cannot evade or
    # poison the throttle (cutover gate #3).
    trust_forwarded_for: bool = False

    # LLM
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    llm_provider: str = "gemini"  # "gemini", "anthropic", or "openai"
    gemini_model: str = "gemini-2.5-flash"
    gemini_location: str = "us-central1"

    # RAG / Embeddings — via the shared LiteLLM gateway (OpenAI-compatible),
    # which load-balances nomic-embed-text (768-dim) across both Mac Studios.
    # Chat generation stays direct on Vertex (see llm_provider).
    embedding_api_base: str = "http://192.168.0.104:4000/v1"
    embedding_api_key: str = ""
    embedding_model: str = "nomic-embed-text"
    embedding_timeout_seconds: float = 60.0
    rag_chunk_size: int = 800
    rag_chunk_overlap: int = 100
    rag_top_k: int = 5

    # Pipeline service-to-service auth
    pipeline_api_key: str = ""  # Required in production

    # Auth bypass for local development ONLY.
    # Must be explicitly set to true. Never enable in production.
    dev_auth_bypass: bool = False

    # Gmail SMTP (Google Workspace)
    gmail_smtp_user: str = "dd@mydailydignity.com"
    gmail_smtp_password: str = ""

    # Document AI OCR
    documentai_processor_id: str = "6785df08989fd9a6"
    documentai_location: str = "us"

    # OCR provider selection (see app/pipeline/ocr/). The primary engine's
    # text flows downstream exactly as before; the optional shadow engine runs
    # best-effort for A/B comparison and never affects the pipeline.
    ocr_provider: str = "documentai"  # PRIMARY: documentai | paddleocr
    ocr_shadow_provider: str = ""  # if set (and != primary), run for compare
    ocr_service_url: str = ""  # PaddleOCR HTTP service base URL

    # App
    app_url: str = "http://localhost:5173"  # Frontend URL for email links
    environment: str = "development"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    # RLS unset-GUC guard (WS1 Phase 2f-ii): warn-only observability that flags a
    # query hitting an RLS tenant table on the app connection with no
    # app.current_user_id set (which fails closed to 0 rows silently — a latent
    # "member sees nothing" bug). "auto" = on when environment != prod. "on"/"off"
    # force it. Never raises; it is diagnostics, not the security control (RLS is).
    rls_guc_guard: str = "auto"  # auto | on | off

    @property
    def authentik_enabled(self) -> bool:
        """True only when the master switch selects Authentik. Gates the additive
        BFF /auth endpoints; DEFAULT False keeps Firebase the sole live auth."""
        return self.auth_provider == "authentik"

    @property
    def authentik_login_enabled(self) -> bool:
        """DUAL-RUN switch for request-time auth resolution.

        When True (auth_provider == "authentik"), the auth dependencies ACCEPT a
        BFF Authentik session cookie (preferred when present) AND still accept a
        Firebase bearer as a fallback, so no client is locked out mid-migration.
        When False (DEFAULT "firebase"), the Authentik resolution branch is inert
        — behavior is byte-identical to the pre-dual-run Firebase-only path. There
        is deliberately NO mode that rejects Firebase; Firebase retirement is a
        later PR."""
        return self.auth_provider == "authentik"

    @property
    def oidc_audience(self) -> str:
        """OIDC audience for the Authentik verifier — the explicit
        ``authentik_oidc_audience`` if set, else the client_id (Authentik's aud)."""
        return self.authentik_oidc_audience or self.authentik_oidc_client_id


settings = Settings()
