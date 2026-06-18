from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "COMPANION_"}

    # Database
    database_url: str = "postgresql+asyncpg://companion:companion_dev@localhost:5432/companion"
    database_echo: bool = False

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Google Cloud (still used for Gemini/Vertex generation + KMS)
    gcp_project_id: str = "companion-dev"
    pubsub_emulator_host: str | None = None
    kms_key_id: str = ""

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

    # App
    app_url: str = "http://localhost:5173"  # Frontend URL for email links
    environment: str = "development"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"


settings = Settings()
