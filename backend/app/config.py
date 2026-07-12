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


settings = Settings()
