import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.api.caregiver import knowledge as knowledge_api
from app.config import settings
from app.db import session as db_module
from app.main import app
from app.services import knowledge_service
from tests.conftest import requires_db

pytestmark = requires_db

_SEARCH_ENDPOINT = "/api/v1/caregiver/knowledge/search"
_INGEST_ECFR_ENDPOINT = "/api/v1/caregiver/knowledge/ingest/ecfr"
_INGEST_FEDREG_ENDPOINT = "/api/v1/caregiver/knowledge/ingest/fedreg"


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


class MockResponse:
    def __init__(self, text, status_code=200, json_data=None):
        self.text = text
        self.status_code = status_code
        self._json_data = json_data

    def json(self):
        return self._json_data or {}


async def test_ecfr_ingestion_and_search(monkeypatch):
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

    # Monkeypatch httpx.AsyncClient.get inside the service
    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    # Pretend auth bypass is active so dev@companion.app is used
    monkeypatch.setattr(settings, "dev_auth_bypass", True)

    async with _client() as ac:
        # 2. Trigger ingestion
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


async def test_fedreg_ingestion(monkeypatch):
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
    monkeypatch.setattr(settings, "dev_auth_bypass", True)

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


async def test_quota_limit_exceeded(monkeypatch):
    monkeypatch.setattr(settings, "dev_auth_bypass", True)

    # Mock quota increment to exceed the limit immediately
    async def mock_quota(*args, **kwargs):
        from fastapi import HTTPException
        raise HTTPException(
            status_code=429,
            detail="Knowledge search query limit reached. Please try again tomorrow."
        )

    monkeypatch.setattr(knowledge_service, "check_and_increment_quota", mock_quota)

    async with _client() as ac:
        resp = await ac.post(
            _SEARCH_ENDPOINT,
            json={"query": "How does SSI count rental subsidy?"}
        )
        assert resp.status_code == 429
        assert "limit reached" in resp.json()["detail"]
