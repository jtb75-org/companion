"""Regulation ingestion worker CLI.

    python -m app.ingestion.worker --source <ecfr|fedreg> [--mode incremental|reconcile]

Runs ONE source through the reconcile engine. Intended to be invoked by a K8s
CronJob per source (manifests are a separate gitops follow-up) or by the retained
admin-only on-demand trigger. Exits non-zero if the run did not succeed, so a
CronJob surfaces the failure.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from app.ingestion.adapters import ADAPTERS
from app.ingestion.reconciler import RunSummary, run_source
from app.ingestion.types import IngestionMode

logger = logging.getLogger(__name__)

# Per-source default cadence/mode when --mode is omitted (spec §6): eCFR is a
# current-state snapshot (full reconcile); the Federal Register is an append-only
# feed (incremental).
_DEFAULT_MODE: dict[str, IngestionMode] = {
    "ecfr": IngestionMode.RECONCILE,
    "fedreg": IngestionMode.INCREMENTAL,
}


async def run(source: str, mode: IngestionMode) -> RunSummary:
    from app.db.session import async_session_factory

    adapter = ADAPTERS[source]()
    async with async_session_factory() as db:
        return await run_source(db, adapter, mode)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="app.ingestion.worker",
        description="Reconcile one regulation source into the public reg corpus.",
    )
    parser.add_argument(
        "--source",
        required=True,
        choices=sorted(ADAPTERS),
        help="Which source to reconcile.",
    )
    parser.add_argument(
        "--mode",
        choices=[m.value for m in IngestionMode],
        default=None,
        help="Override the per-source default (ecfr=reconcile, fedreg=incremental).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = _parse_args(argv)
    mode = (
        IngestionMode(args.mode)
        if args.mode
        else _DEFAULT_MODE.get(args.source, IngestionMode.RECONCILE)
    )
    summary = asyncio.run(run(args.source, mode))
    logger.info(
        "Run %s finished: source=%s mode=%s status=%s seen=%d new=%d changed=%d "
        "unchanged=%d purged=%d embed_skipped=%d",
        summary.run_id, summary.source, summary.mode, summary.status,
        summary.docs_seen, summary.docs_new, summary.docs_changed,
        summary.docs_unchanged, summary.docs_purged, summary.embed_skipped,
    )
    return 0 if summary.ok else 1


if __name__ == "__main__":
    sys.exit(main())
