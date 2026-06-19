"""Tier 2 caregiver dashboard must never expose document contents.

Ref: caregiver-access-and-privacy.md §3 ("Never document contents", "Never
specific financial figures") + §4. card_summary (a content/financial summary)
must not appear in the caregiver dashboard payload.
"""

from __future__ import annotations

import json

from sqlalchemy import delete

from app.db import session as db_module
from app.models.document import Document
from app.models.enums import (
    AccountStatus,
    RecommendedAction,
    ReviewStatus,
    SourceChannel,
)
from app.models.pending_review import PendingReview
from app.models.user import User
from app.services.caregiver_service import get_dashboard_summary
from tests.conftest import requires_db

pytestmark = requires_db

_EMAIL = "caregiver-doc-content-test@example.com"
_SECRET = "CONFIDENTIAL: electric bill $4321.99 from Lender LLC"


async def _cleanup():
    async with db_module.async_session_factory() as s:
        # documents/reviews cascade off the user
        await s.execute(delete(User).where(User.email == _EMAIL))
        await s.commit()


async def test_caregiver_dashboard_excludes_document_content():
    await _cleanup()
    async with db_module.async_session_factory() as s:
        u = User(
            email=_EMAIL,
            first_name="Mem",
            last_name="Ber",
            display_name="Mem Ber",
            preferred_name="Mem",
            account_status=AccountStatus.ACTIVE,
        )
        s.add(u)
        await s.flush()
        doc = Document(
            user_id=u.id,
            source_channel=SourceChannel.CAMERA_SCAN,
            raw_text_ref="s3://x/y",
            card_summary=_SECRET,  # the content that must NOT reach a caregiver
        )
        s.add(doc)
        await s.flush()
        review = PendingReview(
            user_id=u.id,
            document_id=doc.id,
            review_status=ReviewStatus.PENDING,
            recommended_action=RecommendedAction.REVIEW_WITH_CONTACT,
            proposed_record_data="enc:{}",
            source_description="a document",
        )
        s.add(review)
        await s.commit()
        uid = u.id

    async with db_module.async_session_factory() as s:
        summary = await get_dashboard_summary(s, uid)

    recent = summary.get("recent_documents", [])
    assert recent, "expected a recent document entry to exist"
    for entry in recent:
        assert "card_summary" not in entry, "card_summary leaks to caregiver"
    # Belt-and-suspenders: the content must not appear anywhere in the payload.
    assert _SECRET not in json.dumps(summary, default=str)

    await _cleanup()
