"""Shared types for the regulation ingestion engine.

``SourceDoc`` + the ``Adapter`` contract are the seam between the source-agnostic
reconcile spine (see ``reconciler.py``) and the per-source adapters (``adapters/``).
Adding a new source means writing one ``Adapter`` — the spine never changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum


class IngestionMode(StrEnum):
    """How much of a source to pull on a run.

    - ``INCREMENTAL``: append recently-changed docs (cheap, for append-only feeds
      like the Federal Register, or a between-reconcile top-up).
    - ``RECONCILE``: pull the source's full current state and diff the whole
      corpus (drives purge-on-absence + retention sweeps).
    """

    INCREMENTAL = "incremental"
    RECONCILE = "reconcile"


class PurgePolicy(StrEnum):
    """What "present in the DB but absent from the source this run" MEANS.

    - ``DELETE``: the source publishes current state (eCFR / POMS). Absent means
      the regulation was genuinely removed → delete its chunks (guarded by the
      mass-purge circuit-breaker).
    - ``RETAIN``: the source is a permanent, append-only dated feed (Federal
      Register). Absent means nothing — old docs stay published. NEVER delete on
      absence; aging is handled by a separate time-window retention sweep.
    """

    DELETE = "delete"
    RETAIN = "retain"


@dataclass
class SourceDoc:
    """One logical document yielded by an adapter.

    ``source_id`` is the STABLE per-source identity (eCFR = citation, Federal
    Register = document number). ``text`` is the full normalized document text;
    the reconciler chunks/sub-chunks it. ``metadata`` carries the persistable
    provenance fields:

        jurisdiction, source_corpus, program, citation, section, part, title,
        source_url, effective_date

    Every chunk row derived from this doc shares its ``source_id`` and the
    content hash of ``text``.
    """

    source_id: str
    text: str
    metadata: dict = field(default_factory=dict)


class Adapter(ABC):
    """Per-source ingestion contract.

    Subclasses declare their identity/policy as class attributes and implement
    ``list_documents``. The reconcile spine reads only these attributes + the
    yielded docs — it is entirely source-agnostic.
    """

    #: Must match the ``source_corpus`` column values written for this source
    #: (e.g. "eCFR", "Federal_Register"). The reconciler loads/purges the DB
    #: index scoped to this value.
    source_corpus: str = ""

    #: How absence-from-source is handled (see :class:`PurgePolicy`).
    purge_policy: PurgePolicy = PurgePolicy.DELETE

    #: Systemic-fetch-guard floor for DELETE-policy sources: a pull yielding
    #: fewer than this many docs is treated as a broken fetch and aborts BEFORE
    #: any purge (never shrink the corpus on a bad fetch). 0 disables the floor
    #: (0 docs is still always an abort for a DELETE source).
    min_expected_docs: int = 0

    #: Rolling retention window in months for RETAIN sources; 0 = keep all. The
    #: retention sweep (RECONCILE mode) removes docs whose effective_date is
    #: older than this window.
    retention_months: int = 0

    @abstractmethod
    async def list_documents(self, mode: IngestionMode) -> Iterable[SourceDoc]:
        """Fetch + parse the source into an iterable of :class:`SourceDoc`.

        MAY raise on a network/parse failure — the reconciler treats a raised
        fetch as a systemic failure and aborts without touching the corpus.
        """
        raise NotImplementedError
