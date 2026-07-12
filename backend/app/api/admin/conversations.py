"""Admin API — Conversation audit endpoints."""

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.dependencies import AdminUser, require_admin_role
from app.db.session import get_maintenance_db
from app.models.chat_session import ChatSession
from app.models.user import User

router = APIRouter(
    prefix="/admin/conversations",
    tags=["Admin - Conversations"],
)

_viewer = require_admin_role("viewer")


@router.get("")
async def list_conversations(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user_email: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    admin: AdminUser = Depends(_viewer),
    db: AsyncSession = Depends(get_maintenance_db),
):
    """List chat sessions with pagination."""
    query = (
        select(ChatSession, User)
        .join(User, ChatSession.user_id == User.id)
        .order_by(ChatSession.started_at.desc())
    )

    if user_email:
        query = query.where(User.email == user_email)
    if date_from:
        query = query.where(
            ChatSession.started_at >= datetime(
                date_from.year,
                date_from.month,
                date_from.day,
            )
        )
    if date_to:
        query = query.where(
            ChatSession.started_at < datetime(
                *(date_to + timedelta(days=1)).timetuple()[:3]
            )
        )

    result = await db.execute(
        query.offset(offset).limit(limit)
    )
    rows = result.all()

    sessions = []
    for chat_session, user in rows:
        sessions.append({
            "id": str(chat_session.id),
            "session_id": chat_session.session_id,
            "user_name": user.display_name,
            "user_email": user.email,
            "started_at": (
                chat_session.started_at.isoformat()
            ),
            "ended_at": (
                chat_session.ended_at.isoformat()
                if chat_session.ended_at
                else None
            ),
            "message_count": chat_session.message_count,
            "duration_seconds": (
                int(
                    (
                        chat_session.ended_at
                        - chat_session.started_at
                    ).total_seconds()
                )
                if chat_session.ended_at
                and chat_session.started_at
                else None
            ),
        })

    return {
        "conversations": sessions,
        "total": len(sessions),
        "limit": limit,
        "offset": offset,
    }


@router.get("/export")
async def export_conversations(
    date_from: date | None = None,
    date_to: date | None = None,
    admin: AdminUser = Depends(_viewer),
    db: AsyncSession = Depends(get_maintenance_db),
):
    """Export chat sessions as JSON."""
    query = (
        select(ChatSession)
        .options(selectinload(ChatSession.messages))
        .order_by(ChatSession.started_at.desc())
    )

    if date_from:
        query = query.where(
            ChatSession.started_at >= datetime(
                date_from.year,
                date_from.month,
                date_from.day,
            )
        )
    if date_to:
        query = query.where(
            ChatSession.started_at < datetime(
                *(date_to + timedelta(days=1)).timetuple()[:3]
            )
        )

    result = await db.execute(query)
    sessions = result.scalars().all()

    export = []
    for s in sessions:
        export.append({
            "id": str(s.id),
            "session_id": s.session_id,
            "user_id": str(s.user_id),
            "started_at": s.started_at.isoformat(),
            "ended_at": (
                s.ended_at.isoformat()
                if s.ended_at
                else None
            ),
            "message_count": s.message_count,
            "summary": s.summary,
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    "created_at": (
                        m.created_at.isoformat()
                    ),
                }
                for m in s.messages
            ],
        })

    return {"conversations": export, "total": len(export)}


@router.get("/{session_id}")
async def get_conversation(
    session_id: str,
    admin: AdminUser = Depends(_viewer),
    db: AsyncSession = Depends(get_maintenance_db),
):
    """Get full conversation transcript."""
    result = await db.execute(
        select(ChatSession)
        .options(selectinload(ChatSession.messages))
        .where(ChatSession.session_id == session_id)
    )
    chat_session = result.scalar_one_or_none()

    if chat_session is None:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found",
        )

    return {
        "id": str(chat_session.id),
        "session_id": chat_session.session_id,
        "user_id": str(chat_session.user_id),
        "started_at": (
            chat_session.started_at.isoformat()
        ),
        "ended_at": (
            chat_session.ended_at.isoformat()
            if chat_session.ended_at
            else None
        ),
        "message_count": chat_session.message_count,
        "summary": chat_session.summary,
        "messages": [
            {
                "id": str(m.id),
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at.isoformat(),
            }
            for m in chat_session.messages
        ],
    }
