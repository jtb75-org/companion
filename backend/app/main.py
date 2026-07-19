import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import app.events  # noqa: F401
from app.api.admin import router as admin_router
from app.api.admin.seed_admin import router as seed_admin_router
from app.api.auth_authentik import router as authentik_auth_router
from app.api.caregiver import router as caregiver_router
from app.api.internal import router as internal_router
from app.api.pipeline import router as pipeline_router
from app.api.v1 import router as v1_router
from app.api.v1.auth_check import router as auth_router
from app.api.v1.charges import router as charges_router
from app.api.v1.profile import router as profile_router
from app.branding import BRAND_LONG, BRAND_MID
from app.config import settings
from app.db.session import engine
from app.logging_config import setup_logging

# Initialize PII-masked logging
setup_logging()

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: block dev_auth_bypass in production
    if settings.dev_auth_bypass and settings.environment == "prod":
        raise RuntimeError(
            "FATAL: dev_auth_bypass is enabled in production. "
            "This exposes all endpoints without authentication. "
            "Set COMPANION_DEV_AUTH_BYPASS=false and redeploy."
        )
    # Startup: Authentik is the SOLE authentication provider (Firebase auth was
    # retired). There is no Firebase path to fall back to, so a non-"authentik"
    # auth_provider would leave every endpoint's session resolver inert and lock
    # out all users. Fail loud on boot in production rather than serve a broken auth
    # surface.
    if settings.environment == "prod" and settings.auth_provider != "authentik":
        raise RuntimeError(
            "FATAL: COMPANION_AUTH_PROVIDER must be 'authentik' in production "
            f"(got {settings.auth_provider!r}). Firebase authentication has been "
            "removed; there is no other provider. Set it and redeploy."
        )
    # Startup: require the maintenance (BYPASSRLS) DB URL in production. Every
    # per-member table is under FORCE RLS, so admin/cross-member/bootstrap paths
    # depend on the companion_maintenance connection. If it is unset,
    # get_maintenance_db / maintenance_session() silently fall back to the
    # fail-closed companion_app session — admin reads return 0 rows and caregiver
    # auth / invitation flows break with no error. Fail loud on boot instead.
    if settings.environment == "prod" and not settings.maintenance_database_url:
        raise RuntimeError(
            "FATAL: COMPANION_MAINTENANCE_DATABASE_URL is not set in production. "
            "Under per-user RLS, cross-member/admin/bootstrap access silently "
            "degrades to the fail-closed app connection. Set it and redeploy."
        )
    yield
    # Shutdown
    await engine.dispose()


app = FastAPI(
    title=f"{BRAND_MID} API",
    description=f"{BRAND_LONG} — Independence Assistant for Adults with Developmental Disabilities",
    version="0.1.0",
    lifespan=lifespan,
)

CORS_ORIGINS = {
    "development": ["http://localhost:5173", "http://localhost:3000"],
    "staging": [
        "https://app.mydailydignity.com",
        "https://companion-staging-web-44gbcsdrnq-uc.a.run.app",
        "https://companion-staging-web-381910341082.us-central1.run.app",
        "http://localhost:5173",  # TODO: remove for production
    ],
    "prod": [
        "https://app.mydailydignity.com",
        "https://companion-prod-web-mtfid4sksa-uc.a.run.app",
    ],
}

_cors_origins = CORS_ORIGINS.get(settings.environment, [])
if not _cors_origins:
    logger.error(
        f"No CORS origins configured for environment '{settings.environment}'. "
        "Cross-origin requests will be blocked. "
        f"Valid environments: {list(CORS_ORIGINS.keys())}"
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    # X-CSRF-Token: the Authentik BFF double-submit CSRF header a browser SPA sends on
    # unsafe (state-changing) session requests. Without it in allow_headers the CORS
    # preflight fails and the request never reaches the app (cutover gate #6). Additive
    # and inert — no client sends it until the Authentik session flow is live.
    allow_headers=["Authorization", "Content-Type", "X-CSRF-Token"],
)

# Mount API routers
app.include_router(v1_router)
app.include_router(caregiver_router)
app.include_router(pipeline_router)
app.include_router(admin_router)
app.include_router(internal_router)
app.include_router(auth_router)
# BFF native-login surface (Authentik). The endpoints self-gate on
# settings.authentik_enabled, which is the sole supported provider.
app.include_router(authentik_auth_router)
app.include_router(seed_admin_router)
if settings.dev_auth_bypass:
    logger.warning(
        "DEV AUTH BYPASS IS ENABLED -- all endpoints accept "
        "unauthenticated requests. This must NEVER be enabled in production."
    )
app.include_router(charges_router)
app.include_router(profile_router)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "environment": settings.environment,
        "version": "0.1.0",
    }
