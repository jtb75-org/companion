"""App API — Document routes."""

import logging
import uuid

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import User, require_complete_profile
from app.db import get_db
from app.models.enums import DocumentStatus, SourceChannel
from app.schemas.document import DocumentStatusUpdate
from app.services import document_service, storage_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["Documents"])


async def _serialize_document(db: AsyncSession, doc) -> dict:
    """Serialize a Document, decrypting the per-tenant encrypted fields.

    Without this, FastAPI would serialize the raw ORM object and leak the
    tagged ciphertext (and emit ``extracted_fields`` as a string).
    """
    from app.services.field_crypto import decrypt_row_field

    return {
        "id": doc.id,
        "source_channel": doc.source_channel,
        "classification": doc.classification,
        "confidence_score": doc.confidence_score,
        "urgency_level": doc.urgency_level,
        "extracted_fields": await decrypt_row_field(db, doc, "extracted_fields"),
        "spoken_summary": await decrypt_row_field(db, doc, "spoken_summary"),
        "card_summary": await decrypt_row_field(db, doc, "card_summary"),
        "routing_destination": doc.routing_destination,
        "page_count": doc.page_count,
        "status": doc.status,
        "received_at": doc.received_at,
        "processed_at": doc.processed_at,
        "acknowledged_at": doc.acknowledged_at,
    }

ALLOWED_SCAN_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/heic": "heic",
    "application/pdf": "pdf",
}
MAX_SCAN_SIZE = 10 * 1024 * 1024  # 10 MB


@router.post("/scan/analyze")
async def analyze_scan_quality(
    file: UploadFile = File(...),
    user: User = Depends(require_complete_profile),
):
    """Analyze a single camera frame for quality and text presence."""
    from app.services.image_analysis_service import get_image_analysis_service

    data = await file.read()
    analysis = await get_image_analysis_service().analyze_quality(data)
    return analysis


@router.post("/scan", status_code=status.HTTP_201_CREATED)
async def scan_document(
    files: list[UploadFile] = File(default=[]),
    file: UploadFile | None = File(None),
    user: User = Depends(require_complete_profile),
    db: AsyncSession = Depends(get_db),
):
    """Upload one or more camera-scanned document pages for processing."""
    # Backward compatibility: single file -> list
    upload_files = files if files else []
    if file is not None and not upload_files:
        upload_files = [file]

    if not upload_files:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No files provided.",
        )

    # Validate all files before uploading
    pages_data: list[tuple[bytes, str]] = []
    for f in upload_files:
        content_type = f.content_type or ""
        if content_type not in ALLOWED_SCAN_TYPES:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=(
                    "Unsupported file type. "
                    "Accepted: JPEG, PNG, HEIC, PDF."
                ),
            )
        data = await f.read()
        if len(data) > MAX_SCAN_SIZE:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="File exceeds 10 MB limit.",
            )
        pages_data.append((data, content_type))

    # Upload all pages to object storage (MinIO)
    doc_id = uuid.uuid4()
    page_refs: list[str] = []
    first_gcs_uri = ""
    try:
        for i, (data, content_type) in enumerate(pages_data):
            ext = ALLOWED_SCAN_TYPES[content_type]
            blob_path = f"scans/{user.id}/{doc_id}/page_{i:03d}.{ext}"
            uri = await storage_service.upload(blob_path, data, content_type)
            page_refs.append(uri)
            if i == 0:
                first_gcs_uri = uri
    except Exception:
        logger.exception("Storage upload failed for user %s", user.id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to upload file to storage.",
        ) from None

    page_count = len(pages_data)

    # Create document record (publishes document.received event)
    doc = await document_service.create_document(
        db,
        user.id,
        {
            "source_channel": SourceChannel.CAMERA_SCAN,
            "status": DocumentStatus.RECEIVED,
            "raw_text_ref": first_gcs_uri,
            "page_count": page_count,
            "source_metadata": {
                "original_filename": upload_files[0].filename,
                "content_type": pages_data[0][1],
                "size_bytes": sum(len(d) for d, _ in pages_data),
                "page_refs": page_refs,
            },
        },
    )

    # Commit the transaction
    await db.commit()

    return {
        "document_id": doc.id,
        "status": "processing",
        "page_count": page_count,
    }


@router.get("")
async def list_documents(
    document_status: str | None = Query(None, alias="status"),
    classification: str | None = None,
    urgency: str | None = None,
    user: User = Depends(require_complete_profile),
    db: AsyncSession = Depends(get_db),
):
    """List documents with optional filters."""
    docs = await document_service.list_documents(
        db, user.id, status=document_status, classification=classification, urgency=urgency
    )
    serialized = [await _serialize_document(db, d) for d in docs]
    return {"documents": serialized, "total": len(serialized)}


@router.get("/{document_id}/status")
async def get_document_status(
    document_id: uuid.UUID,
    user: User = Depends(require_complete_profile),
    db: AsyncSession = Depends(get_db),
):
    """Get the current processing status of a document."""
    doc = await document_service.get_document(
        db, user.id, document_id
    )
    if doc is None:
        raise HTTPException(
            status_code=404, detail="Document not found"
        )
    return {
        "document_id": doc.id,
        "status": doc.status,
        "classification": doc.classification,
        "processed_at": doc.processed_at,
    }


@router.get("/{document_id}")
async def get_document(
    document_id: uuid.UUID,
    user: User = Depends(require_complete_profile),
    db: AsyncSession = Depends(get_db),
):
    """Get document detail."""
    doc = await document_service.get_document(db, user.id, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return await _serialize_document(db, doc)


@router.patch("/{document_id}")
async def update_document(
    document_id: uuid.UUID,
    data: DocumentStatusUpdate,
    user: User = Depends(require_complete_profile),
    db: AsyncSession = Depends(get_db),
):
    """Update document status."""
    doc = await document_service.update_document_status(
        db, user.id, document_id, data.model_dump()
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return await _serialize_document(db, doc)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    user: User = Depends(require_complete_profile),
    db: AsyncSession = Depends(get_db),
):
    """Delete a document."""
    deleted = await document_service.delete_document(db, user.id, document_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found")
    return None
