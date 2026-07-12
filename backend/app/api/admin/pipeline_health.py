"""Admin API — Pipeline health monitoring."""

import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AdminUser, require_admin_role
from app.db.session import get_maintenance_db
from app.models.document import Document
from app.models.enums import DocumentStatus
from app.models.pipeline_metrics import PipelineMetric

router = APIRouter(prefix="/admin/pipeline", tags=["Admin - Pipeline Health"])

_viewer = require_admin_role("viewer")


@router.get("/health")
async def pipeline_health(
    admin: AdminUser = Depends(_viewer),
    db: AsyncSession = Depends(get_maintenance_db),
):
    """Overall pipeline health status with per-stage metrics."""
    now = datetime.utcnow()
    cutoff_24h = now - timedelta(hours=24)

    # Documents currently in processing
    in_flight = await db.execute(
        select(func.count()).select_from(Document).where(
            Document.status.in_([
                DocumentStatus.RECEIVED,
                DocumentStatus.PROCESSING,
                DocumentStatus.CLASSIFIED,
            ])
        )
    )
    documents_in_flight = in_flight.scalar_one()

    # Per-stage metrics from last 24h
    stage_rows = await db.execute(
        select(
            PipelineMetric.stage,
            func.count().label("total"),
            func.count().filter(PipelineMetric.status == "success").label("successes"),
            func.avg(PipelineMetric.duration_ms).label("avg_ms"),
        )
        .where(PipelineMetric.recorded_at >= cutoff_24h)
        .group_by(PipelineMetric.stage)
    )

    stages = {}
    for row in stage_rows.all():
        total = row.total or 0
        successes = row.successes or 0
        success_rate = successes / total if total > 0 else 1.0
        stages[row.stage] = {
            "success_rate": round(success_rate, 3),
            "avg_ms": round(row.avg_ms or 0),
            "total": total,
        }

    # Recent failures (last 24h)
    failure_rows = await db.execute(
        select(PipelineMetric)
        .where(
            PipelineMetric.status == "error",
            PipelineMetric.recorded_at >= cutoff_24h,
        )
        .order_by(PipelineMetric.recorded_at.desc())
        .limit(20)
    )
    recent_failures = [
        {
            "document_id": str(f.document_id) if f.document_id else None,
            "stage": f.stage,
            "error": f.error_message or "Unknown error",
            "timestamp": f.recorded_at.isoformat(),
        }
        for f in failure_rows.scalars().all()
    ]

    return {
        "documents_in_flight": documents_in_flight,
        "stages": stages,
        "recent_failures": recent_failures,
    }


@router.get("/metrics")
async def pipeline_metrics(
    admin: AdminUser = Depends(_viewer),
    db: AsyncSession = Depends(get_maintenance_db),
):
    """Pipeline processing metrics (last 24h)."""
    cutoff = datetime.utcnow() - timedelta(hours=24)

    total_result = await db.execute(
        select(func.count()).select_from(PipelineMetric).where(
            PipelineMetric.recorded_at >= cutoff
        )
    )
    success_result = await db.execute(
        select(func.count()).select_from(PipelineMetric).where(
            PipelineMetric.recorded_at >= cutoff,
            PipelineMetric.status == "success",
        )
    )
    avg_result = await db.execute(
        select(func.avg(PipelineMetric.duration_ms)).where(
            PipelineMetric.recorded_at >= cutoff
        )
    )

    total = total_result.scalar_one()
    successes = success_result.scalar_one()

    return {
        "documents_processed_24h": total,
        "avg_processing_time_ms": round(avg_result.scalar_one() or 0),
        "success_rate": round(successes / total, 3) if total > 0 else 1.0,
    }


@router.get("/failures")
async def pipeline_failures(
    admin: AdminUser = Depends(_viewer),
    db: AsyncSession = Depends(get_maintenance_db),
):
    """Recent pipeline failures."""
    rows = await db.execute(
        select(PipelineMetric)
        .where(PipelineMetric.status == "error")
        .order_by(PipelineMetric.recorded_at.desc())
        .limit(50)
    )
    failures = [
        {
            "document_id": str(f.document_id) if f.document_id else None,
            "stage": f.stage,
            "error_message": f.error_message,
            "recorded_at": f.recorded_at.isoformat(),
        }
        for f in rows.scalars().all()
    ]
    return {"failures": failures, "total": len(failures)}


@router.get("/documents/{document_id}/stages")
async def document_stages(
    document_id: uuid.UUID,
    admin: AdminUser = Depends(_viewer),
    db: AsyncSession = Depends(get_maintenance_db),
):
    """Pipeline stage details for a specific document."""
    rows = await db.execute(
        select(PipelineMetric)
        .where(PipelineMetric.document_id == document_id)
        .order_by(PipelineMetric.recorded_at)
    )
    stages = [
        {
            "stage": m.stage,
            "status": m.status,
            "duration_ms": m.duration_ms,
            "error_message": m.error_message,
            "recorded_at": m.recorded_at.isoformat(),
        }
        for m in rows.scalars().all()
    ]
    return {"document_id": str(document_id), "stages": stages}
