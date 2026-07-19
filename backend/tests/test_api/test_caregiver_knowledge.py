import uuid
from datetime import datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, text

from app.auth import principal as principal_module
from app.config import settings
from app.db import session as db_module
from app.main import app
from app.models.admin_user import AdminUser
from app.models.enums import AccountStatus
from app.models.trusted_contact import TrustedContact
from app.models.user import User
from app.services import knowledge_service
from tests.conftest import requires_db

pytestmark = requires_db

_SEARCH_ENDPOINT = "/api/v1/caregiver/knowledge/search"
_INGEST_ECFR_ENDPOINT = "/api/v1/caregiver/knowledge/ingest/ecfr"
_INGEST_FEDREG_ENDPOINT = "/api/v1/caregiver/knowledge/ingest/fedreg"

# The exact not-legal-advice disclaimer the service appends in code (BLOCKER 1). Kept in
# sync with knowledge_service.NOT_LEGAL_ADVICE_DISCLAIMER on purpose so the test breaks
# loudly if the string drifts (a persona/safety change that needs sign-off).
_DISCLAIMER = knowledge_service.NOT_LEGAL_ADVICE_DISCLAIMER


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _cleanup_reg_chunks():
    async with db_module.async_session_factory() as s:
        await s.execute(text("DELETE FROM disability_reg_chunks"))
        await s.commit()


@pytest.fixture(autouse=True)
async def cleanup():
    await _cleanup_reg_chunks()
    yield
    await _cleanup_reg_chunks()


def _authentik_session(monkeypatch, subject: str) -> None:
    """Turn on Authentik and pretend the request carries a BFF session for ``subject``.

    Patches the single seam every resolver funnels through (resolve_session_subject), so
    the real cohort walk in _email_for_subject + the real admin/caregiver role gates run —
    the same technique test_auth_check_cohorts uses."""
    monkeypatch.setattr(settings, "auth_provider", "authentik")
    monkeypatch.setattr(settings, "dev_auth_bypass", False)

    async def _fake_subject(_request):
        return subject

    monkeypatch.setattr(principal_module, "resolve_session_subject", _fake_subject)


async def _seed_admin(subject: str) -> str:
    email = f"kb-admin-{uuid.uuid4().hex[:8]}@example.invalid"
    async with db_module.maintenance_session() as mdb:
        mdb.add(
            AdminUser(
                email=email,
                name="KB Admin",
                role="admin",
                is_active=True,
                external_subject_id=subject,
            )
        )
        await mdb.commit()
    return email


async def _seed_caregiver(subject: str) -> tuple[str, str]:
    """Seed a pure caregiver (TrustedContact) with NO admin_users row."""
    cg_email = f"kb-cg-{uuid.uuid4().hex[:8]}@example.invalid"
    member_email = f"kb-member-{uuid.uuid4().hex[:8]}@example.invalid"
    async with db_module.maintenance_session() as mdb:
        member = User(
            email=member_email,
            preferred_name="KBMember",
            display_name="KB Member",
            account_status=AccountStatus.ACTIVE,
        )
        mdb.add(member)
        await mdb.flush()
        mdb.add(
            TrustedContact(
                user_id=member.id,
                contact_name="KB Caregiver",
                contact_email=cg_email,
                relationship_type="family",
                access_tier="tier_1",
                is_active=True,
                external_subject_id=subject,
            )
        )
        await mdb.commit()
    return cg_email, member_email


async def _cleanup_accounts(*emails: str) -> None:
    async with db_module.maintenance_session() as mdb:
        for email in emails:
            await mdb.execute(delete(AdminUser).where(AdminUser.email == email))
            await mdb.execute(
                delete(TrustedContact).where(TrustedContact.contact_email == email)
            )
            await mdb.execute(delete(User).where(User.email == email))
        await mdb.commit()


async def _insert_chunk(*, citation: str, text_content: str, program: str = "SSDI") -> None:
    """Insert one regulation chunk directly (no pgvector needed — the service's ILIKE
    fallback path is used in CI)."""
    async with db_module.async_session_factory() as s:
        await s.execute(
            text(
                "INSERT INTO disability_reg_chunks "
                "(id, jurisdiction, source_corpus, source_url, citation, program, "
                " text_content, token_count, effective_date) "
                "VALUES (:id, :jur, :corpus, :url, :cit, :prog, :txt, :tok, :eff)"
            ),
            {
                "id": str(uuid.uuid4()),
                "jur": "US_Federal",
                "corpus": "eCFR",
                "url": "https://www.ecfr.gov/current/title-20/part-404",
                "cit": citation,
                "prog": program,
                "txt": text_content,
                "tok": len(text_content) // 4,
                "eff": datetime(2024, 1, 1),
            },
        )
        await s.commit()


class MockResponse:
    def __init__(self, text, status_code=200, json_data=None):
        self.text = text
        self.status_code = status_code
        self._json_data = json_data

    def json(self):
        return self._json_data or {}


# ── BLOCKER 2: ingestion is admin-only ─────────────────────────────────────────


async def test_ecfr_ingestion_and_search(monkeypatch):
    subject = f"kb-admin-subj-{uuid.uuid4().hex[:8]}"
    admin_email = await _seed_admin(subject)
    _authentik_session(monkeypatch, subject)

    # 1. Mock eCFR public HTML response
    mock_html = """
    <div class="part" id="part-404">
        <div class="section" id="404.1520">
            <h4>§ 404.1520 Evaluation of disability of adults, in general.</h4>
            <p>We use a five-step sequential evaluation process
               to determine if you are disabled.</p>
        </div>
    </div>
    """

    async def mock_get(*args, **kwargs):
        return MockResponse(text=mock_html, status_code=200)

    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    try:
        async with _client() as ac:
            # 2. Trigger ingestion (admin-authed)
            resp = await ac.post(_INGEST_ECFR_ENDPOINT)
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "success"
            assert body["chunks_ingested"] == 2

            # 3. Verify in database using raw SQL to survive absence of pgvector in testing
            async with db_module.async_session_factory() as s:
                sql = (
                    "SELECT source_corpus, part, section, citation, text_content "
                    "FROM disability_reg_chunks"
                )
                res = await s.execute(text(sql))
                chunks = res.fetchall()
                assert len(chunks) == 2
                chunk = chunks[0]
                assert chunk.source_corpus == "eCFR"
                assert chunk.part in ["404", "416"]
                assert chunk.section == "1520"
                assert chunk.citation in ["20 CFR § 404.1520", "20 CFR § 416.1520"]
                assert "five-step sequential" in chunk.text_content

            # 4. Search and get answer (RAG)
            search_resp = await ac.post(
                _SEARCH_ENDPOINT,
                json={"query": "What is the five step sequential evaluation?", "limit": 5}
            )
            assert search_resp.status_code == 200
            search_body = search_resp.json()
            assert "query" in search_body
            assert "answer" in search_body
            assert len(search_body["sources"]) >= 1
            assert "20 CFR §" in search_body["sources"][0]["citation"]
    finally:
        await _cleanup_accounts(admin_email)


async def test_fedreg_ingestion(monkeypatch):
    subject = f"kb-admin-subj-{uuid.uuid4().hex[:8]}"
    admin_email = await _seed_admin(subject)
    _authentik_session(monkeypatch, subject)

    mock_json = {
        "results": [
            {
                "title": "SSI Rental Subsidy Expansion",
                "abstract": "Applying nationwide the ISM rental subsidy exception.",
                "document_number": "2024-12345",
                "publication_date": "2024-07-15",
                "html_url": (
                    "https://www.federalregister.gov/documents/2024/07/15"
                    "/2024-12345/ssi-rental-subsidy"
                ),
            }
        ]
    }

    async def mock_get(*args, **kwargs):
        return MockResponse(text="", status_code=200, json_data=mock_json)

    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    try:
        async with _client() as ac:
            resp = await ac.post(_INGEST_FEDREG_ENDPOINT)
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "success"
            assert body["chunks_ingested"] == 1

            async with db_module.async_session_factory() as s:
                sql = "SELECT source_corpus, citation FROM disability_reg_chunks"
                res = await s.execute(text(sql))
                chunks = res.fetchall()
                assert len(chunks) == 1
                chunk = chunks[0]
                assert chunk.source_corpus == "Federal_Register"
                assert chunk.citation == "Federal Register Vol. 2024-12345"
    finally:
        await _cleanup_accounts(admin_email)


async def test_ingest_requires_admin_not_just_caregiver(monkeypatch):
    """BLOCKER 2: a resolvable NON-admin caregiver session must be 403'd on BOTH ingest
    endpoints — otherwise any authenticated caregiver could wipe/re-ingest the corpus."""
    subject = f"kb-cg-subj-{uuid.uuid4().hex[:8]}"
    cg_email, member_email = await _seed_caregiver(subject)
    _authentik_session(monkeypatch, subject)

    # If auth were somehow bypassed, this would let us detect any accidental network call.
    async def _explode(*args, **kwargs):
        raise AssertionError("ingestion service ran for a non-admin caller")

    monkeypatch.setattr(knowledge_service, "trigger_ecfr_ingestion", _explode)
    monkeypatch.setattr(knowledge_service, "trigger_federal_register_ingestion", _explode)

    try:
        async with _client() as ac:
            r1 = await ac.post(_INGEST_ECFR_ENDPOINT)
            assert r1.status_code == 403, f"caregiver got {r1.status_code} on eCFR ingest"
            r2 = await ac.post(_INGEST_FEDREG_ENDPOINT)
            assert r2.status_code == 403, f"caregiver got {r2.status_code} on fedreg ingest"

        # Corpus untouched.
        async with db_module.async_session_factory() as s:
            res = await s.execute(text("SELECT count(*) AS n FROM disability_reg_chunks"))
            assert res.scalar() == 0
    finally:
        await _cleanup_accounts(cg_email, member_email)


# ── BLOCKER 1: disclaimer + citation enforced in code, not by the LLM ───────────


async def test_answer_always_carries_disclaimer_and_citation(monkeypatch):
    """The stub LLM returns text with NO disclaimer and NO citation (STUB_REPLY). The
    endpoint response must STILL contain the not-legal-advice disclaimer and at least one
    server-computed citation — proving they are enforced in code, not by the model."""
    subject = f"kb-cg-subj-{uuid.uuid4().hex[:8]}"
    cg_email, member_email = await _seed_caregiver(subject)
    _authentik_session(monkeypatch, subject)

    await _insert_chunk(
        citation="20 CFR § 404.1520",
        text_content=(
            "We use a five-step sequential evaluation process to determine disability."
        ),
    )

    try:
        async with _client() as ac:
            resp = await ac.post(
                _SEARCH_ENDPOINT,
                json={"query": "five-step sequential evaluation", "limit": 5},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # Disclaimer is present verbatim in the answer AND surfaced structurally.
        assert _DISCLAIMER in body["answer"]
        assert body["disclaimer"] == _DISCLAIMER
        # The stub reply itself carried no disclaimer — so it can only be here in code.
        assert "stubbed" in body["answer"].lower()

        # Provenance line enforced in code and surfaced structurally.
        assert body["answer"].startswith("Provenance: As of ")
        assert body["provenance"].startswith("Provenance: As of ")

        # At least one citation, computed server-side from the chunk (not the model text).
        assert body["grounded"] is True
        assert len(body["citations"]) >= 1
        assert "20 CFR § 404.1520" in body["citations"]
        assert len(body["sources"]) >= 1
    finally:
        await _cleanup_accounts(cg_email, member_email)


async def test_answer_with_no_grounding_refuses_but_keeps_disclaimer(monkeypatch):
    """No retrieved chunk → no citation. The service must NOT fabricate an answer; it
    returns a deterministic refusal that still carries the disclaimer + provenance."""
    subject = f"kb-cg-subj-{uuid.uuid4().hex[:8]}"
    cg_email, member_email = await _seed_caregiver(subject)
    _authentik_session(monkeypatch, subject)

    # No chunks inserted → retrieval returns nothing → LLM must not be trusted to refuse.
    async def _explode(*args, **kwargs):
        raise AssertionError("LLM was called with no grounding chunks")

    from app.conversation import llm as llm_module

    class _NoCallClient:
        async def generate(self, *args, **kwargs):
            await _explode()

    monkeypatch.setattr(llm_module, "get_llm_client", lambda: _NoCallClient())

    try:
        async with _client() as ac:
            resp = await ac.post(
                _SEARCH_ENDPOINT,
                json={"query": "some unrelated question with no corpus match zzz", "limit": 5},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["grounded"] is False
        assert body["citations"] == []
        assert body["sources"] == []
        assert _DISCLAIMER in body["answer"]
        assert "cannot find the answer" in body["answer"].lower()
    finally:
        await _cleanup_accounts(cg_email, member_email)


async def test_quota_limit_exceeded(monkeypatch):
    subject = f"kb-cg-subj-{uuid.uuid4().hex[:8]}"
    cg_email, member_email = await _seed_caregiver(subject)
    _authentik_session(monkeypatch, subject)

    # Mock quota increment to exceed the limit immediately
    async def mock_quota(*args, **kwargs):
        from fastapi import HTTPException
        raise HTTPException(
            status_code=429,
            detail="Knowledge search query limit reached. Please try again tomorrow."
        )

    monkeypatch.setattr(knowledge_service, "check_and_increment_quota", mock_quota)

    try:
        async with _client() as ac:
            resp = await ac.post(
                _SEARCH_ENDPOINT,
                json={"query": "How does SSI count rental subsidy?"}
            )
            assert resp.status_code == 429
            assert "limit reached" in resp.json()["detail"]
    finally:
        await _cleanup_accounts(cg_email, member_email)
