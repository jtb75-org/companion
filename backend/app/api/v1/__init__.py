"""App API v1 — aggregates all app-facing routers."""

from fastapi import APIRouter

from app.api.v1 import (
    activation,
    appointments,
    assignments,
    bills,
    contacts,
    conversation,
    device_tokens,
    documents,
    integrations,
    invitations,
    medications,
    notifications,
    reviews,
    sections,
    todos,
    users,
)

router = APIRouter(prefix="/api/v1")

router.include_router(users.router)
router.include_router(activation.router)
router.include_router(documents.router)
router.include_router(sections.router)
router.include_router(medications.router)
router.include_router(appointments.router)
router.include_router(bills.router)
router.include_router(todos.router)
router.include_router(contacts.router)
router.include_router(invitations.router)
router.include_router(assignments.router)
router.include_router(conversation.router)
router.include_router(notifications.router)
router.include_router(integrations.router)
router.include_router(device_tokens.router)
router.include_router(reviews.router)
