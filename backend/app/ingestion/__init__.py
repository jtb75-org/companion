"""Regulation ingestion worker (Phase A).

A source-agnostic reconcile engine plus per-source adapters that keep the public
disability-regulation corpus (``disability_reg_chunks``) current, complete, and
safe to refresh. Replaces the old manual full-delete-and-reinsert admin
ingestion with a proper new/changed/unchanged/absent diff, guarded against ever
wiping the corpus on a bad fetch or an embedding outage.

Writes ONLY to the public reg tables — NO ``user_id``, NO RLS, NO encryption,
NO PHI. Public federal data only.
"""

from app.ingestion.reconciler import (
    LegacyPurgeShrinkError,
    MassPurgeError,
    RunSummary,
    SystemicFetchError,
    run_source,
)
from app.ingestion.types import (
    Adapter,
    IngestionMode,
    PurgePolicy,
    SourceDoc,
)

__all__ = [
    "Adapter",
    "IngestionMode",
    "PurgePolicy",
    "SourceDoc",
    "RunSummary",
    "run_source",
    "SystemicFetchError",
    "MassPurgeError",
    "LegacyPurgeShrinkError",
]
