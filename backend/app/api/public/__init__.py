"""Public API — UNAUTHENTICATED, no-PHI surfaces.

Every router mounted here is reachable by anonymous internet users. Endpoints in
this package MUST NOT read or write member/PHI data, MUST NOT require or resolve a
user session, and MUST carry their own cost/abuse controls (the platform's per-user
quota and auth guards do not apply to anonymous callers). See app/api/public/
knowledge.py for the pattern.
"""

from fastapi import APIRouter

from app.api.public import knowledge

router = APIRouter(prefix="/public")

router.include_router(knowledge.router)
