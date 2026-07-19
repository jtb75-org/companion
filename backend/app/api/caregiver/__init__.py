"""Caregiver API — aggregates caregiver-facing routers."""

from fastapi import APIRouter

from app.api.caregiver import activity, alerts, dashboard, knowledge

router = APIRouter(prefix="/api/v1/caregiver")

router.include_router(activity.router)
router.include_router(alerts.router)
router.include_router(dashboard.router)
router.include_router(knowledge.router)
