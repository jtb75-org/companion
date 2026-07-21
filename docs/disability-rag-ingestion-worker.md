# Disability RAG — Regulation Ingestion Worker (design spec)

Status: proposed · Owner: pipeline · Relates to: disability-rag-module, #147/#150/#151

## 1. Purpose

One engine that keeps the public disability-regulation corpus (`disability_reg_chunks`)
**current, complete, and safe to refresh** — across sources with very different update
models — running as scheduled Kubernetes CronJobs. It replaces today's manual,
full-delete-and-reinsert admin HTTP endpoints.

Non-goals: retrieval/answering (separate), the member PHI RAG (never touched),
hybrid search / reranking (separate quality track).

## 2. Invariants (inherited, non-negotiable)

- Writes ONLY to the public reg tables. NO `user_id`, NO RLS, NO encryption, NO
  `document_chunks`/PHI. Public federal data only.
- Public-domain sources only (17 USC §105). NEVER ingest commercial repackages
  (Nolo/Westlaw/Lexis).
- A failed/partial fetch must NEVER wipe or shrink the corpus (extends #150's
  embed-before-delete + systemic-failure guard).

## 3. Architecture

**Source-agnostic spine** + **per-source adapters**.

```
CronJob(source) → Adapter.list_documents() → Reconciler
                                               ├─ normalize + content_hash
                                               ├─ diff vs DB (new / changed / unchanged / absent)
                                               ├─ chunk (section-aware + sub-chunk) → embed (batched, resilient)
                                               ├─ upsert docs+chunks   (new/changed)
                                               ├─ touch last_seen_at    (unchanged)
                                               └─ purge/age-out         (absent, per policy)
                                             → record RegIngestionRun (counts, status)
```

- **Spine**: fetch-orchestration, hashing, diff, chunking, embedding, upsert,
  purge, run-logging, guards. Identical for every source.
- **Adapter** (one per source): implements `list_documents() -> Iterable[SourceDoc]`
  and encapsulates API-vs-crawl, parsing, and per-source metadata. Adapters:
  `ECFRAdapter`, `FederalRegisterAdapter`, `PomsAdapter` (1b), `HallexAdapter` (1b),
  later `GuidesAdapter`/`SSRAdapter`.

`SourceDoc` = `{ source_id, text, metadata }` where metadata carries jurisdiction,
source_corpus, program, citation, section, effective_date, (source last-updated).

## 4. Stable identity + change detection (the core primitives)

Every ingested doc gets:
- **`source_id`** — stable per source: eCFR = citation (`20 CFR 404.1520`);
  Federal Register = FR document number; POMS = section number (`DI 24501.001`);
  HALLEX = section id.
- **`content_hash`** — hash of the normalized text. Refresh compares hash to skip
  re-embedding unchanged docs (cheap, avoids gateway churn).
- **`last_seen_at`** — bumped on every run that observes the doc; drives purge.
- **`effective_date`** / **`retrieval_date`** — already in schema; keep.

## 5. The Reconcile algorithm (per source, or source+scope)

```
run = start_run(source)
docs = adapter.list_documents()                    # may raise / yield partial
guard_systemic(source, docs)                       # abort BEFORE any delete if the pull looks failed
                                                   #   (0 docs, or << expected count) — never purge on a bad fetch
existing = load_index(source)                      # {source_id: (content_hash, id)}
seen = set()
for d in docs:
    seen.add(d.source_id); h = hash(d.text)
    if d.source_id not in existing:        embed+insert(d)            # NEW
    elif h != existing[d.source_id].hash:  embed+replace_chunks(d)    # CHANGED
    else:                                  touch(d.source_id)         # UNCHANGED (no re-embed)
absent = existing.keys() - seen
purge_policy(source).apply(absent)                 # see §6 — differs by source
finish_run(run, counts)                            # new/changed/unchanged/purged/embed-skipped
```

- Embed-before-delete: a doc's OLD chunks are replaced only after its NEW chunks
  embed successfully; the whole run commits transactionally (a mid-run failure
  rolls back — never a partial/empty corpus).
- **Mass-purge circuit-breaker**: if `purged / existing` exceeds a threshold
  (e.g. >30%), abort the purge + alert — protects against a source-format change
  making everything look "absent."

## 6. Per-source freshness + purge policy (the part that must differ)

| Source | Model | Refresh | "Absent from source" means | Cadence |
|---|---|---|---|---|
| **eCFR (20 CFR)** | current snapshot | full reconcile per part | reg genuinely removed → **delete** | monthly (skip if eCFR "last amended" unchanged) |
| **Federal Register** | permanent dated feed | **incremental** by publish-date watermark; append new | nothing — FR docs are permanent; **do NOT purge on absence**. Age out by **rolling retention window (~24 mo)** in a separate sweep | weekly pull + monthly retention sweep |
| **POMS / HALLEX** | living manual | **incremental** by per-section last-updated + periodic full reconcile | section deleted/superseded → **delete** | weekly incremental + monthly full reconcile |
| **Blue Book / Listings** | slow snapshot | full reconcile | removed → delete | quarterly |

Key point: **eCFR/POMS purge-on-absence (they're "current state"); Federal
Register does NOT** (it's an append-only feed; recency is handled by a time window,
not by deletion).

## 7. Chunking + embedding (reuse #150)

- Section-aware node-level chunking preserving citation hierarchy; sub-chunk any
  section over the embedder token budget (~1200 tok). (Small-to-big parent/child
  is a later optional enhancement — chunks already reference their parent doc.)
- nomic-embed-text 768 via the LiteLLM gateway, batched, per-chunk retry+skip.
- On a doc CHANGE, delete+reinsert only THAT doc's child chunks (scoped, not the
  whole corpus).

## 8. Scheduling / deployment

- One image, `python -m app.ingestion.worker --source <ecfr|fedreg|poms|hallex> [--mode incremental|reconcile]`.
- **K8s CronJobs** per source at the §6 cadences; `concurrencyPolicy: Forbid`;
  `backoffLimit` small; TTL after finish.
- Manual on-demand run retained as an **admin-only** endpoint (or `kubectl create job --from=cronjob`) — the current caregiver/admin ingest endpoints get demoted to admin-only trigger shims or removed.
- **Egress**: the worker needs egress to `ecfr.gov`, `federalregister.gov`,
  `secure.ssa.gov`, `ssa.gov` — needs its own NetworkPolicy (mirror the
  `companion-ocr` egress precedent; deny the rest). Use a browser-like User-Agent
  (Cloudflare blocks bare urllib UAs).

## 9. Politeness (POMS/HALLEX crawlers)

Rate-limit (configurable req/s), respect robots.txt, conditional requests
(ETag/If-Modified-Since) to skip unchanged pages cheaply, exponential backoff,
resumable by section id. Cache raw pages to make re-parses free.

## 10. Observability + freshness SLA

- New **`reg_ingestion_runs`** table: `id, source, mode, started_at, finished_at,
  status, docs_seen, new, changed, unchanged, purged, embed_skipped, error`.
- Structured log per run (counts + top purged/changed source_ids, NO PHI — there is
  none here anyway).
- **Alerts (Prometheus→ntfy)**: (a) a source hasn't had a `success` run in > SLA
  (eCFR 45d, FR 14d, POMS 45d); (b) mass-purge circuit-breaker tripped; (c) embed
  skip-fraction > 10%; (d) time-sensitive figures stale (annual COLA/SGA/limits
  past their expected update date).
- A tiny "corpus health" query the admin console can show (per-source count +
  last-refresh + oldest retrieval_date).

## 11. Schema changes

Add to the reg store (evolve `disability_reg_chunks`, or split into parent
`regulation_documents` + child `regulation_chunks` if adopting small-to-big):
- `source_id TEXT` (indexed, unique per source), `content_hash TEXT`,
  `last_seen_at TIMESTAMPTZ`, `ingestion_run_id UUID`.
- New `reg_ingestion_runs` table (§10).
- Migration is DDL only (no tenant DML, so the FORCE-RLS silent-noop gotcha
  doesn't apply — but it's a public table anyway).

## 12. Security / correctness checklist

- Worker service account: write to reg tables only; no PHI/RLS/user tables; no
  secrets beyond the LiteLLM key + DB creds.
- Systemic-failure guard + mass-purge breaker → corpus can't be emptied by an
  outage or a source reshaping its HTML/JSON.
- Idempotent + resumable: re-running a source is safe (upsert by source_id).
- Licensing gate: adapters only point at public federal endpoints; a hardcoded
  allowlist of source hosts.

## 13. Phased rollout

- **Phase A (foundation)**: schema (`source_id`/`content_hash`/`last_seen_at` +
  `reg_ingestion_runs`); refactor eCFR + Federal Register into the adapter+reconcile
  spine; run both as CronJobs. Turns today's manual full-replace into proper
  reconcile + purge. **Also finally loads Federal Register** (never run in prod yet)
  and verifies Blue Book/Listings coverage.
- **Phase B**: FR incremental watermark + 24-mo retention sweep; freshness alerts;
  admin corpus-health view.
- **Phase C (= Phase 1b)**: POMS + HALLEX crawler adapters (politeness, incremental
  by last-updated). The big content unlock.
- **Phase D**: SSRs / Acquiescence Rulings / SSA plain-language guides (PDFs via the
  existing OCR path). (Hybrid retrieval + reranking is a separate, parallel track.)

## 14. Open decisions for the owner

1. **Small-to-big now or later** — split into parent-doc + child-chunk tables in
   Phase A (cleaner, avoids a later migration) vs. keep the single chunk table and
   add the columns? (Recommend: split now.)
2. **FR retention window** — 24 months? (Or keep-all + recency-weight at retrieval?)
3. **Cadences** — confirm eCFR monthly / FR weekly / POMS weekly+monthly.
4. **Manual trigger** — admin endpoint kept, or CronJob-only + `kubectl` for ad hoc?
