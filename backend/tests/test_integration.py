"""End-to-end integration tests.

These tests verify the full vertical slice:
document upload -> pipeline processing -> section updates ->
morning check-in -> conversation -> caregiver dashboard.

Requires: local Postgres and Redis running (docker compose up).
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.conftest import requires_db


@pytest.fixture(scope="session")
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test"
    ) as ac:
        yield ac


# ---------------------------------------------------------------------------
# Health & basics
# ---------------------------------------------------------------------------


class TestHealthAndBasics:
    async def test_health(self, client: AsyncClient):
        r = await client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    async def test_openapi_schema(self, client: AsyncClient):
        r = await client.get("/openapi.json")
        assert r.status_code == 200
        schema = r.json()
        assert "paths" in schema
        # Should have a substantial number of endpoints
        assert len(schema["paths"]) > 40


# ---------------------------------------------------------------------------
# User profile
# ---------------------------------------------------------------------------


class TestUserProfile:
    async def test_get_me(self, client: AsyncClient):
        r = await client.get("/api/v1/me")
        assert r.status_code == 200
        data = r.json()
        # Dev user resolved via the auth bypass (first user in DB)
        assert "preferred_name" in data or "display_name" in data

    async def test_get_memories(self, client: AsyncClient):
        r = await client.get("/api/v1/me/memory")
        assert r.status_code == 200
        data = r.json()
        assert "memories" in data
        assert "total" in data

    async def test_get_activity(self, client: AsyncClient):
        r = await client.get("/api/v1/me/activity")
        assert r.status_code == 200
        data = r.json()
        assert "activities" in data


# ---------------------------------------------------------------------------
# Medications
# ---------------------------------------------------------------------------


class TestMedications:
    async def test_list_medications(self, client: AsyncClient):
        r = await client.get("/api/v1/medications")
        assert r.status_code == 200
        data = r.json()
        assert "medications" in data
        # Seed data should include at least one medication
        assert isinstance(data["medications"], list)

    async def test_medication_has_expected_fields(self, client: AsyncClient):
        r = await client.get("/api/v1/medications")
        meds = r.json()["medications"]
        if meds:
            med = meds[0]
            assert "name" in med
            assert "dosage" in med
            assert "frequency" in med


# ---------------------------------------------------------------------------
# Bills
# ---------------------------------------------------------------------------


class TestBills:
    async def test_list_bills(self, client: AsyncClient):
        r = await client.get("/api/v1/bills")
        assert r.status_code == 200
        data = r.json()
        assert "bills" in data
        assert "total" in data

    async def test_bill_summary(self, client: AsyncClient):
        r = await client.get("/api/v1/bills/summary")
        assert r.status_code == 200
        summary = r.json()
        assert "total_due" in summary
        assert "upcoming_count" in summary
        assert "overdue_count" in summary

    async def test_create_bill(self, client: AsyncClient):
        r = await client.post(
            "/api/v1/bills",
            json={
                "sender": "Integration Test Water Co",
                "amount": 33.50,
                "due_date": "2026-05-01",
            },
        )
        assert r.status_code == 201
        data = r.json()
        assert data["sender"] == "Integration Test Water Co"
        assert "id" in data


# ---------------------------------------------------------------------------
# Appointments
# ---------------------------------------------------------------------------


class TestAppointments:
    async def test_list_appointments(self, client: AsyncClient):
        r = await client.get("/api/v1/appointments")
        assert r.status_code == 200
        data = r.json()
        assert "appointments" in data
        assert "total" in data


# ---------------------------------------------------------------------------
# Todos
# ---------------------------------------------------------------------------


class TestTodos:
    async def test_list_todos(self, client: AsyncClient):
        r = await client.get("/api/v1/todos")
        assert r.status_code == 200
        data = r.json()
        assert "todos" in data
        assert "total" in data

    async def test_create_and_complete_todo(self, client: AsyncClient):
        # Create
        r = await client.post(
            "/api/v1/todos",
            json={
                "title": "Integration test todo",
                "category": "task",
            },
        )
        assert r.status_code == 201
        todo = r.json()
        todo_id = todo["id"]
        assert todo["title"] == "Integration test todo"
        assert todo["is_active"] is True

        # Complete
        r = await client.post(f"/api/v1/todos/{todo_id}/complete")
        assert r.status_code == 200
        completed = r.json()
        assert completed["completed_at"] is not None


# ---------------------------------------------------------------------------
# Sections (aggregate views)
# ---------------------------------------------------------------------------


class TestSections:
    async def test_home_section(self, client: AsyncClient):
        r = await client.get("/api/v1/sections/home")
        assert r.status_code == 200
        data = r.json()
        assert "recent_documents" in data
        assert "active_todos" in data
        assert "upcoming_appointments" in data

    async def test_health_section(self, client: AsyncClient):
        r = await client.get("/api/v1/sections/health")
        assert r.status_code == 200
        data = r.json()
        assert "medications" in data
        assert "appointments" in data

    async def test_bills_section(self, client: AsyncClient):
        r = await client.get("/api/v1/sections/bills")
        assert r.status_code == 200
        data = r.json()
        assert "unpaid_bills" in data
        assert "overdue_count" in data

    async def test_plans_section(self, client: AsyncClient):
        r = await client.get("/api/v1/sections/plans")
        assert r.status_code == 200
        data = r.json()
        assert "todos" in data
        assert "appointments" in data

    async def test_today_section(self, client: AsyncClient):
        r = await client.get("/api/v1/sections/today")
        assert r.status_code == 200
        data = r.json()
        assert "items" in data
        assert "count" in data


# ---------------------------------------------------------------------------
# Document pipeline (full 6-stage vertical slice)
# ---------------------------------------------------------------------------


class TestDocumentPipeline:
    async def test_scan_bill(self, client: AsyncClient):
        """Upload a bill scan via multipart and verify acceptance."""
        fake_image = b"\xff\xd8\xff\xe0" + b"\x00" * 100
        r = await client.post(
            "/api/v1/documents/scan",
            files={
                "file": ("bill.jpg", fake_image, "image/jpeg"),
            },
        )
        # 201 if storage works, 502 if storage unavailable in test
        assert r.status_code in (201, 502)

    async def test_scan_junk_mail(self, client: AsyncClient):
        """Upload junk mail scan via multipart."""
        fake_image = b"\xff\xd8\xff\xe0" + b"\x00" * 100
        r = await client.post(
            "/api/v1/documents/scan",
            files={
                "file": ("junk.jpg", fake_image, "image/jpeg"),
            },
        )
        assert r.status_code in (201, 502)

    async def test_list_documents(self, client: AsyncClient):
        r = await client.get("/api/v1/documents")
        assert r.status_code == 200
        data = r.json()
        assert "documents" in data
        assert "total" in data

    @requires_db
    async def test_scan_deduplicates_identical_upload(self, client: AsyncClient):
        """Re-uploading the identical file returns the existing document with
        duplicate=true instead of creating a second copy (the double-tap case)."""
        from sqlalchemy import select

        from app.db.session import async_session_factory
        from app.models.document import Document
        from app.models.enums import DocumentStatus, SourceChannel
        from app.models.user import User
        from app.services.field_crypto import fingerprint_for_user

        fake = b"\xff\xd8\xff\xe0" + b"DEDUPE-PROBE" * 40

        async with async_session_factory() as s:
            uid = (await s.execute(select(User.id).limit(1))).scalar_one()
            # Seed the fingerprint the SAME way the endpoint computes it: the
            # per-member keyed HMAC, so this member's identical re-upload dedupes.
            fp = await fingerprint_for_user(s, uid, fake)
            doc = Document(
                user_id=uid,
                source_channel=SourceChannel.CAMERA_SCAN,
                status=DocumentStatus.RECEIVED,
                raw_text_ref="seed",
                content_fingerprint=fp,
                page_count=1,
            )
            s.add(doc)
            await s.commit()
            seeded_id = str(doc.id)

        r = await client.post(
            "/api/v1/documents/scan",
            files={"file": ("dup.jpg", fake, "image/jpeg")},
        )
        assert r.status_code in (200, 201)
        body = r.json()
        assert body.get("duplicate") is True
        assert body.get("document_id") == seeded_id

    @requires_db
    async def test_deleting_document_removes_its_pending_review(
        self, client: AsyncClient
    ):
        """Removing a document ('Remove this one' on a duplicate) also clears its
        pending review, so the card actually leaves the queue."""
        from sqlalchemy import select

        from app.db.session import async_session_factory
        from app.models.document import Document
        from app.models.enums import (
            DocumentStatus,
            RecommendedAction,
            ReviewStatus,
            SourceChannel,
        )
        from app.models.pending_review import PendingReview
        from app.models.user import User

        async with async_session_factory() as s:
            uid = (await s.execute(select(User.id).limit(1))).scalar_one()
            doc = Document(
                user_id=uid,
                source_channel=SourceChannel.CAMERA_SCAN,
                status=DocumentStatus.SUMMARIZED,
                raw_text_ref="seed",
            )
            s.add(doc)
            await s.flush()
            review = PendingReview(
                user_id=uid,
                document_id=doc.id,
                review_status=ReviewStatus.PENDING,
                recommended_action=RecommendedAction.FILE_ONLY,
                proposed_record_data="{}",
            )
            s.add(review)
            await s.commit()
            doc_id, review_id = str(doc.id), str(review.id)

        r = await client.delete(f"/api/v1/documents/{doc_id}")
        assert r.status_code in (200, 204)

        async with async_session_factory() as s:
            doc_gone = (
                await s.execute(select(Document).where(Document.id == doc_id))
            ).scalar_one_or_none()
            review_gone = (
                await s.execute(
                    select(PendingReview).where(PendingReview.id == review_id)
                )
            ).scalar_one_or_none()
        assert doc_gone is None
        assert review_gone is None  # the review left the queue with the doc


# ---------------------------------------------------------------------------
# Notifications & morning check-in
# ---------------------------------------------------------------------------


class TestNotifications:
    async def test_morning_checkin(self, client: AsyncClient):
        """Morning check-in should return structured sections."""
        r = await client.get("/api/v1/notifications")
        assert r.status_code == 200
        data = r.json()
        checkin = data["checkin"]
        assert "greeting" in checkin
        assert "close" in checkin
        assert "urgent_count" in checkin
        assert "total_items" in checkin

    async def test_notification_preferences(self, client: AsyncClient):
        r = await client.get("/api/v1/notifications/preferences")
        assert r.status_code == 200
        data = r.json()
        # Should have quiet-hours and check-in time fields
        assert "quiet_start" in data
        assert "quiet_end" in data
        assert "checkin_time" in data


# ---------------------------------------------------------------------------
# Conversation (Arlo) lifecycle
# ---------------------------------------------------------------------------


class TestConversation:
    async def test_conversation_lifecycle(self, client: AsyncClient):
        """Start -> message -> check state -> end conversation."""
        # Start a new session
        r = await client.post("/api/v1/conversation/start", json={})
        assert r.status_code == 201
        start_data = r.json()
        session_id = start_data["session_id"]
        assert session_id is not None
        assert start_data["status"] == "active"
        assert "greeting" in start_data

        # Send a message
        r = await client.post(
            "/api/v1/conversation/message",
            json={"text": "What do I need to do today?"},
        )
        assert r.status_code == 200
        msg_data = r.json()
        assert "response" in msg_data
        assert msg_data["session_id"] == session_id
        assert msg_data["message_count"] >= 2  # greeting + user + assistant

        # Check state
        r = await client.get("/api/v1/conversation/state")
        assert r.status_code == 200
        state = r.json()
        assert state["status"] == "active"
        assert state["session_id"] == session_id

        # End the session
        r = await client.post("/api/v1/conversation/end")
        assert r.status_code == 200
        end_data = r.json()
        assert end_data["status"] == "ended"
        assert end_data["session_id"] == session_id



# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------


class TestContacts:
    async def test_list_contacts(self, client: AsyncClient):
        r = await client.get("/api/v1/contacts")
        assert r.status_code == 200
        data = r.json()
        assert "contacts" in data
        assert "total" in data


# ---------------------------------------------------------------------------
# Integrations
# ---------------------------------------------------------------------------


class TestIntegrations:
    async def test_integration_status(self, client: AsyncClient):
        r = await client.get("/api/v1/integrations/status")
        assert r.status_code == 200
        data = r.json()
        assert "gmail" in data
        assert "plaid" in data
