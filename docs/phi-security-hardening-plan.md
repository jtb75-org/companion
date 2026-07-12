# PHI Security Hardening Plan

**Status:** proposed (2026-07-12). **Goal:** close the defense-in-depth gaps
between Companion's current PHI controls and a mature layered model, sequenced as
**pre-real-PHI gates** (before onboarding real members). Reference implementation
and consult: **healthcostclarity** (HCC, `~/repo/healthcostclarity`, dev = kali),
which already runs this model in prod.

Companion is **per-USER** multi-tenant (each member is a tenant); HCC is
per-account. The controls port directly with `account_id` → `user_id`.

---

## 0. What we already have — do NOT rebuild

Companion's crypto **core matches HCC** and is solid: per-tenant AES-256-GCM
envelope (`app/services/field_crypto.py`), per-user DEK wrapped by an **OpenBao
Transit KEK via Kubernetes auth** (`openbao_transit.py` — short-lived cached
token, not static), `user_id` bound as field AAD, fail-closed, versioned KEK
keyring for rotation, dedicated field-level-key capability (`fl1:`) + CI
tripwire, scoped LLM egress. This plan does **not** touch the envelope core
except WS4 (AAD widening).

---

## 1. The gaps → workstreams (ranked by value)

### WS1 — Postgres Row-Level Security (per-user isolation) — **headline**

Today Companion has **zero RLS**. Per-member isolation rests entirely on ~21
hand-written `where(user_id == …)` filters across the services. Miss one and it
is a cross-member PHI leak. RLS makes isolation **fail-closed at the database**,
so the DB enforces it even when app code forgets.

**Policy shape (per-user analog of HCC `db/rls.py`):**
```sql
ALTER TABLE <t> ENABLE ROW LEVEL SECURITY;
ALTER TABLE <t> FORCE  ROW LEVEL SECURITY;
CREATE POLICY <t>_isolation ON <t>
  USING      (user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid)
  WITH CHECK (user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid);
```
The `, true` (missing_ok) + `NULLIF(…, '')` is the whole fail-closed trick: GUC
unset/empty → NULL → predicate false → **zero rows and zero writes**.

**The one thing to get right (kali):** RLS is only a guarantee because **the app
connects as a NOBYPASSRLS, non-owner role**. `FORCE` makes RLS apply even to the
table owner, but a `BYPASSRLS`/superuser role still bypasses silently. **Phase 1
task #1: verify Companion's runtime DB role** — the CNPG migrate Job runs as
`postgres` (superuser); the app must NOT. If the app currently connects as a
superuser/owner, RLS would silently evaporate.

**Migration ordering that bit HCC — three separate actors, pin the sequence:**
1. **CNPG `managed.roles`** creates the app role (`companion_app`: NOSUPERUSER,
   **NOBYPASSRLS**, non-owner) + manages its password.
2. **Alembic** migrations run as the **owner/superuser** and create tables +
   policies (DDL should bypass RLS).
3. A **separate grants step** grants table privileges TO `companion_app` (CNPG
   manages role existence, NOT grants).
Order: **role exists → migrate (tables+policies) → grant → app deploy.** Out of
order gives confusing "permission denied" vs "zero rows" depending on the layer.

**pgvector + RLS — Companion-critical gotcha.** Companion uses pgvector for RAG
retrieval. **RLS filters rows AFTER the ANN index scan**: a `LIMIT 10` HNSW/IVF
search finds 10 nearest candidates, THEN RLS drops the ones not owned by the
current user → **silent under-return** (ask for 10 chunks, get 3). Mitigation
(pick one, then test): over-fetch (`LIMIT k·N` then app-trim), a per-user
pre-filter in the query, or a per-tenant vector index. **Must** be tested with a
multi-tenant vector corpus — this is the one place per-user RLS + ANN interact
badly.

**Tenant tables to enumerate (Phase 1, exhaustive):** documents, pending_reviews,
functional_memory, RAG chunks/embeddings, medications, bills, appointments,
todos, trusted_contacts, conversation sessions/messages, notification/schedule
rows, the audit tables (WS3), `user_encryption_keys`, etc. The `users` row itself
gets a self-only policy (like HCC's `users_self_*`).

**Same-user FK integrity:** where tenant tables reference each other, add
`enforce_same_user` triggers (HCC `enforce_same_account` analog) — RLS alone does
not stop a mis-set FK pointing at another user's row.

**GUC discipline (UnitOfWork):** set `set_config('app.current_user_id', uid,
true)` **transaction-locally** (`is_local=true` → vanishes at COMMIT/ROLLBACK,
never leaks across pooled connections) at UoW entry; **workers set it first**
before any tenant query. Port HCC `db/context.py` + `db/uow.py`.

**Perf:** negligible — one `set_config` per txn + an equality on an indexed uuid.
Ensure `user_id` is the **leading column** of the relevant indexes.

**Testing (non-negotiable):** an `rls`-marked pytest suite against **live**
Postgres (ephemeral docker PG — SQLite cannot test RLS): provision two users, set
user A's GUC, attempt to SELECT/UPDATE user B's rows, assert zero/blocked. Runs
in CI; skips locally without a DB URL. Do **not** rely on app-layer tests.

### WS2 — Encrypt document bytes at rest in MinIO — **confirmed gap**

**Confirmed:** `api/v1/documents.py:122` uploads **raw bytes**
(`storage_service.upload(blob_path, data, content_type)`); `put_object` sets no
SSE. Raw uploaded images (photos of bills, medical letters = PHI) sit in MinIO
**plaintext**. Only extracted `ocr_text` is encrypted, and only in the DB.

Per kali: **app-level envelope is THE control; disk/volume encryption is
defense-in-depth only** (it does not cover a compromised app/MinIO, a
misconfigured bucket, backups/snapshots, or a storage-access insider).

**Fix:** envelope-encrypt bytes **before** upload (AES-256-GCM under the per-user
DEK), store `nonce||ct` as the blob; keep the wrapped-DEK reference + a keyed
content fingerprint on the `documents` row; decrypt on the BFF-mediated download
path (`GET /documents/{id}/content`, `Cache-Control: no-store`, no presigned
URLs). Migrate any existing blobs (currently just the smoke-test corpus, if any).
**Audit every read site** that pulls blob bytes directly — they must go through
decrypt.

### WS3 — Append-only / tamper-evident audit — **recommend**

Companion's audit tables (`account_audit_log`, `caregiver_activity_log`,
`deletion_audit_log`) are ordinary rows the app role can `UPDATE`/`DELETE`. Make
them append-only like HCC `audit_events`: RLS **SELECT + INSERT policies only**
(no UPDATE/DELETE policy) **plus** grant-level `UPDATE`/`DELETE` **revoked** from
`companion_app`. Pairs with the WS1 grants step. Valuable for HIPAA-adjacent
PHI-access trails.

### WS4 — Widen field AAD to `user_id | table.column` — **easy win**

Companion binds only `user_id`; HCC binds `account_id|table.column`, preventing
intra-tenant **column-swap** (ciphertext from `notes` can't be pasted into
`display_name` and decrypt, even for the same user) and making a buggy migration
that shuffles columns fail-closed instead of silently decrypting wrong.

Cost is one string in the AAD tuple, but it is a **ciphertext-format change**:
existing `f2:` values are bound to `user_id` only. Use a **scheme bump** (`f3:`)
+ dual-read during migration (try new AAD, fall back to old), or a background
re-encrypt. Add an **allowlist + CI tripwire** for AAD context strings — a typo
silently fails to decrypt (kali regret).

### WS5 — Blind index + encrypt email/names — **explicitly declined (for now)**

kali's assessment: **not worth it** for Companion. Blind indexes exist to allow
**equality lookup on an encrypted column** (you can't btree ciphertext). Companion
**deliberately** keeps email/names plaintext (auth gate, unique-email lookup,
display) and health content envelope-encrypted — a defensible posture once RLS
covers tenant isolation. Adopt only if a "must-be-encrypted-AND-queried" field
appears. Documented as a conscious decision, not an oversight.

---

## 2. Ops hardening (folded from kali's regrets)

- **Loud unset-GUC guard:** fail-closed manifests as **silent emptiness**, not an
  error (a query with no GUC returns 0 rows, reading as "data missing"). Add a
  dev/CI assertion that a tenant-table query with the GUC unset **RAISES**.
- **Maintenance jobs under RLS:** the retention / ttl-purge / account-deletion /
  away-monitor CronJobs must run **RLS-scoped** (`SET LOCAL ROLE companion_app`
  per user), not as a bypassing privileged role. Decide per-job up front; default
  to scoped.
- **KEK custody is the highest-fragility dependency.** No KEK = no decrypt = fail
  closed **everywhere**. HCC's worst incident: repathing the OpenBao audit device
  wedged Bao **SEALED** and took the whole app down. Companion's existing
  pre-PHI gates (enable OpenBao audit device, enable TLS) carry exactly this
  risk — **rehearse seal/unseal + recovery before real PHI**, and sequence those
  Bao changes carefully.
- **Verify data ops with `psql` against the primary**, never through a wrapper
  that can shape errors; assert response **shape** before counting.

---

## 3. Sequencing with the existing pre-real-PHI gates

Fold into the CLAUDE.md pre-real-PHI list alongside: OpenBao audit device + TLS +
off-cluster key vaulting, and the `companion-ocr` egress NetworkPolicy.

| Phase | Work | Depends on |
|---|---|---|
| 0 | **Spike:** port `rls.py`+`context.py` against ONE tenant table + the pgvector RAG table under two users; confirm the ANN under-return + a mitigation (pair with kali) | — |
| 1 | Verify runtime DB role; add NOSUPERUSER/NOBYPASSRLS non-owner `companion_app` (CNPG `managed.roles`) + grants step + deploy ordering | 0 |
| 2 | RLS policies on all tenant tables + GUC UnitOfWork + unset-GUC guard + `rls` negative-test CI suite | 1 |
| 3 | pgvector retrieval fix (over-fetch/pre-filter) + multi-tenant vector tests | 2 |
| 4 | MinIO document-byte envelope encryption + read-site audit + blob migration | — (parallel) |
| 5 | Append-only audit (RLS SELECT+INSERT + grant revoke) | 1 |
| 6 | Widen AAD to user_id\|table.column (scheme bump + dual-read) | — (parallel) |
| 7 | Ops rehearsal: OpenBao seal/unseal recovery; maintenance-job role scoping | 2 |

---

## 4. Risks

- **Runtime as owner/superuser silently disables RLS** — Phase 1 gate must
  confirm the app role, or the whole effort is a no-op.
- **pgvector under-return is silent** — must be test-gated, or RAG quietly loses
  chunks per user.
- **MinIO encryption breaks direct blob reads** — audit read sites first.
- **AAD widening is a ciphertext-format migration** — needs dual-read, else old
  values won't decrypt.
- **Maintenance jobs** that bypass RLS re-open the hole they're meant to close.

---

## 5. Review & ownership

**safety-privacy-reviewer sign-off required** (PHI isolation + crypto changes —
mandatory gate). **qa-test** owns the `rls` negative-test CI gate and the
multi-tenant pgvector test. Pairing offered by kali on the `rls.py` + `context.py`
port.

## Appendix — files to port from HCC

`backend/app/db/{rls,context,uow,grants}.py`, `backend/role_setup.sql`,
`backend/app/crypto/envelope.py` (the `account_id|table.column` AAD pattern).
See HCC `docs/security.md` §"Tenant isolation" + §"Encryption".
