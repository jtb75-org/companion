"""Tests for the admin-managed OCR provider feature flag.

Covers the pipeline resolver (SystemConfig flag with env fallback) and the
admin-config guard (role elevation + provider validation). Both are exercised
without a live DB by stubbing ``config_service.get_by_key``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.admin import config as admin_config
from app.models.enums import ConfigCategory
from app.pipeline import ingestion
from app.services import config_service

# ---------------------------------------------------------------------------
# Pipeline resolver — _resolve_ocr_provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolver_falls_back_to_env_when_no_flag(monkeypatch):
    async def _none(*_a, **_k):
        return None

    monkeypatch.setattr(config_service, "get_by_key", _none)
    got = await ingestion._resolve_ocr_provider(None, "ocr_primary_provider", "documentai")
    assert got == "documentai"


@pytest.mark.asyncio
async def test_resolver_reads_flag_when_present(monkeypatch):
    async def _row(*_a, **_k):
        return SimpleNamespace(value={"provider": "paddleocr"})

    monkeypatch.setattr(config_service, "get_by_key", _row)
    got = await ingestion._resolve_ocr_provider(None, "ocr_primary_provider", "documentai")
    assert got == "paddleocr"


@pytest.mark.asyncio
async def test_resolver_honours_explicit_empty_shadow(monkeypatch):
    # An explicitly-empty provider disables the shadow; it must NOT fall back
    # to the env default (absence vs. empty are distinct).
    async def _row(*_a, **_k):
        return SimpleNamespace(value={"provider": ""})

    monkeypatch.setattr(config_service, "get_by_key", _row)
    got = await ingestion._resolve_ocr_provider(None, "ocr_shadow_provider", "paddleocr")
    assert got == ""


@pytest.mark.asyncio
async def test_resolver_falls_back_on_read_error(monkeypatch):
    async def _boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(config_service, "get_by_key", _boom)
    got = await ingestion._resolve_ocr_provider(None, "ocr_primary_provider", "documentai")
    assert got == "documentai"


# ---------------------------------------------------------------------------
# Admin guard — _guard_ocr_flag
# ---------------------------------------------------------------------------

_FF = ConfigCategory.FEATURE_FLAG
_admin = SimpleNamespace(role="admin", email="a@x.io")
_editor = SimpleNamespace(role="editor", email="e@x.io")


def test_guard_ignores_non_ocr_keys():
    # A non-OCR feature flag is untouched even for a lowly editor.
    admin_config._guard_ocr_flag(_editor, _FF, "some_other_flag", {"x": 1})


def test_guard_blocks_non_admin_role():
    with pytest.raises(HTTPException) as exc:
        admin_config._guard_ocr_flag(
            _editor, _FF, "ocr_primary_provider", {"provider": "paddleocr"}
        )
    assert exc.value.status_code == 403


def test_guard_rejects_unknown_provider():
    with pytest.raises(HTTPException) as exc:
        admin_config._guard_ocr_flag(_admin, _FF, "ocr_primary_provider", {"provider": "tesseract"})
    assert exc.value.status_code == 422


def test_guard_allows_valid_primary():
    admin_config._guard_ocr_flag(_admin, _FF, "ocr_primary_provider", {"provider": "paddleocr"})


def test_guard_rejects_empty_primary():
    with pytest.raises(HTTPException) as exc:
        admin_config._guard_ocr_flag(_admin, _FF, "ocr_primary_provider", {"provider": ""})
    assert exc.value.status_code == 422


def test_guard_allows_empty_shadow_to_disable():
    # Shadow may be empty (disabled); primary may not.
    admin_config._guard_ocr_flag(_admin, _FF, "ocr_shadow_provider", {"provider": ""})
