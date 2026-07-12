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

## Phase 0 spike — RESULTS (2026-07-12) ✅

Ran against an ephemeral **`paradedb/paradedb:17-v0.23.1`** (prod image),
pgvector **0.8.1**, isolated from the cluster. Harness mirrors `document_chunks`
(user_id, `vector(768)`, global HNSW cosine index) with an adversarial two-tenant
corpus (3000 chunks each; tenant B near the query, tenant A far).

**RLS works exactly as designed — GO for WS1.**
- App connected as `companion_app` (`rolsuper=f`, `rolbypassrls=f`) — the
  non-owner/NOBYPASSRLS guarantee holds.
- GUC unset → **0 rows** (fail-closed). User A: sees own 3000, **0** of B's;
  cross-tenant `UPDATE` → 0 rows; cross-tenant `INSERT` → **rejected by WITH
  CHECK**. Per-user isolation is enforced at the DB.

**pgvector HNSW post-filter under-return — CONFIRMED, and it affects the current
code.**
| query (ask 10), user A = "far" tenant | rows returned |
|---|---|
| RLS-only, no user_id filter | **0** |
| explicit `WHERE user_id = A` (retrieval.py shape) | **0** |
| `hnsw.ef_search = 1000` | **0** |
| **`hnsw.iterative_scan = relaxed_order`** | **10** ✅ |
| (contrast) user B = "near" tenant, RLS-only | 10 |

→ The `user_id` pre-filter does **not** save recall under HNSW (the filter runs
*after* the ANN scan). **`retrieval.py` has a latent production under-return
bug today**, independent of RLS. Fix = `hnsw.iterative_scan` (Phase 3). This is
worth a **standalone fix now** in `retrieval.py`, ahead of the rest of the plan.

Harness preserved at `scratchpad/spike/` (`01_setup.sql`, `02_test.sql`,
`run.sh`) — the basis for the WS1 CI negative-test + WS3 recall test.

---

## 1. The gaps → workstreams (ranked by value)

### WS1 — Postgres Row-Level Security (per-user isolation) — **headline**

Today Companion has **zero RLS**. Per-member isolation rests entirely on
hand-written `where(user_id == …)` filters across the services — a spot-count
finds **~110 user_id-filter lines across ~35 files (~63 in `services/` alone)**.
Miss one and it is a cross-member PHI leak. RLS makes isolation **fail-closed at
the database**, so the DB enforces it even when app code forgets.

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

**The standard policy only applies to tables with a direct `user_id`.** Several
Companion PHI/operational tables do NOT have one and need an EXISTS-through-parent
policy (or a denormalized `user_id` column) instead — do not apply the standard
shape blindly. Known examples (verify exhaustively in Phase 1):
- `chat_messages` → only `chat_session_id` (join to the session's `user_id`)
- `medication_confirmations` → only `medication_id`
- `pipeline_metrics` → only `document_id`
- `caregiver_assignment_requests` → `member_id` / `caregiver_email`, not `user_id`

EXISTS-through-parent example:
```sql
CREATE POLICY chat_messages_isolation ON chat_messages USING (
  EXISTS (SELECT 1 FROM chat_sessions s
          WHERE s.id = chat_messages.chat_session_id
            AND s.user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid));
```
Same-user triggers cover FK *integrity* but do **not** define SELECT/UPDATE
visibility for child tables — that is the policy's job. **Phase 1 deliverable: a
table-by-table policy matrix** (direct-user_id vs through-parent vs
schema-change-needed vs global/self) before writing any policy.

**Caregiver access is a distinct axis.** Caregivers legitimately read a member's
data across the user boundary (three-tier model). A pure `user_id = current
setting` policy would block them. The matrix must decide caregiver visibility per
table — e.g. an additional policy branch allowing rows whose `user_id` is in the
caller's active caregiver-authorized set, or a separate caregiver DB principal.
This intersects the Authentik plan's caregiver-principal work.

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

**pgvector + RLS — Companion-critical gotcha. MEASURED in the Phase 0 spike
(2026-07-12) — see §Phase 0 results.** Companion uses a **global HNSW** cosine
index (`ix_document_chunks_embedding_hnsw`). **The filter — whether RLS or an
explicit `user_id` predicate — is applied AFTER the HNSW ANN scan** (confirmed by
`EXPLAIN`: `Index Scan using …_hnsw … Filter: (user_id = …)`). If the current
user's chunks are not in the query's globally-nearest cluster, the top-K
candidates are all *other* users' rows and the filter drops them → **silent
under-return, down to zero**.

**This overturns the earlier "the pre-filter protects us" assumption.** The spike
showed `retrieval.py`'s exact `WHERE user_id = A … LIMIT 10` returns **0 rows**
for a user whose vectors are far from the query cluster — a **latent
silent-under-return bug that exists in production TODAY**, independent of RLS.

**Fix (measured):** set **`hnsw.iterative_scan = relaxed_order`** (available in
our pgvector **0.8.1**) on the RAG retrieval path — it keeps walking the HNSW
graph until `LIMIT` is satisfied *after* filtering. In the spike this restored a
full 10/10. Raising `hnsw.ef_search` alone did **not** help (still 0 — the near
cluster was entirely the other tenant). Keep the explicit `user_id` pre-filter
too (defence-in-depth), but it is **not** sufficient on its own.

Three implementation rules (kali, from adjacent experience):
1. **RLS makes this MORE load-bearing, not redundant.** After WS1, the RLS policy
   is *also* a post-ANN filter, so the vector query runs **two** post-scan
   filters. `iterative_scan` becomes required, not optional. **Test the combined
   path (RLS on + iterative_scan) as one case**, not two.
2. **`iterative_scan` is a per-session GUC — set it `SET LOCAL` inside the same
   UoW/transaction that sets `app.current_user_id`.** Otherwise pooled-connection
   bleed leaves some requests with it and some without → intermittent
   under-recall that is hell to reproduce. Same discipline + footgun as the RLS
   GUCs.
3. **`relaxed_order` may return the k results slightly out of exact distance
   order** (the recall tradeoff). Fine for RAG (feeding a context window), unless
   something downstream assumes strict nearest-first — **audit `retrieval.py`
   post-processing** for dedup-by-first-hit, a "top result" shortcut, or a
   threshold cutoff on `result[0]`. Set `hnsw.max_scan_tuples` to bound scan cost
   on large corpora. (Use `strict_order` only if exact ordering is required.)

Regression-test recall under a multi-tenant corpus, with RLS enabled.

**Tenant tables to enumerate (Phase 1, exhaustive):** documents, pending_reviews,
functional_memory, RAG chunks/embeddings, medications, bills, appointments,
todos, trusted_contacts, conversation sessions/messages, notification/schedule
rows, the audit tables (WS3), `user_encryption_keys`, etc.

**`users` needs a bootstrap-safe policy — critical cross-plan dependency with the
Authentik plan.** A naive self-only `users` policy (`id = current_user_id`)
**breaks first login**: during Authentik provisioning we must look the stub up by
the verified OIDC **subject** *before* `app.current_user_id` is known. Port HCC's
solution — a third GUC `app.current_external_subject`, set server-side ONLY from a
verified token, and a `users` SELECT policy that also matches
`external_subject_id = current_setting('app.current_external_subject')` (HCC
`db/rls.py` users_self_or_member + `db/context.py`). Companion has no
`external_subject_id` column today (see Authentik plan §3) — this column is a
shared prerequisite of both plans. Writes stay self-only (the bootstrap sets
`app.current_user_id` to the new id first).

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

**Fix — pick ONE key model. Chosen: reuse Companion's existing per-user DEK**
(consistent with `field_crypto`; NOT HCC's per-blob-DEK model, which would need a
new `wrapped_dek` column and a second key scheme). The user's DEK already lives in
`user_encryption_keys` and is resolved via `user_id`, so blobs need **no** wrapped
key stored alongside them:
- Encrypt bytes **before** upload: `nonce || AESGCM(user_DEK).encrypt(nonce, data,
  aad=user_id|documents.<blob-kind>)`; store that blob in MinIO. (The AAD reuses
  the WS4 `user_id|table.column` scheme.)
- Applies to **every** blob kind: the primary upload (`documents.py:122`), all
  `page_refs` pages, and `raw_text_ref`. Enumerate the blob-column set in the
  workstream.
- **Schema:** add a nullable `content_fingerprint` (keyed HMAC over plaintext) to
  `documents` for integrity/dedup — this is the *only* new column; no per-blob
  wrapped-DEK column (that was the inconsistency). Add an `encryption_scheme`
  marker so mixed plaintext/ciphertext blobs are distinguishable during migration.
- **Decrypt** on the BFF-mediated download path (`GET /documents/{id}/content`,
  `Cache-Control: no-store`, no presigned URLs).
- **Migration:** re-encrypt existing plaintext blobs (currently just the
  smoke-test corpus) under the owner's DEK; gate reads on `encryption_scheme` so
  old and new coexist during rollout.
- **Audit every read site** that pulls blob bytes directly — all must route
  through decrypt.

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
| 0 | ✅ **DONE 2026-07-12:** RLS proven on paradedb17/pgvector0.8.1; HNSW post-filter under-return confirmed (incl. the current pre-filter query → 0 rows); fix = `hnsw.iterative_scan`. See §Phase 0 results. | — |
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
- **`external_subject_id` on `users` is a shared prerequisite** with the Authentik
  plan (§3 there): the bootstrap-safe `users` policy needs it. Sequence the two
  plans together — do not land RLS on `users` before that column + GUC exist.
- **Caregiver cross-user reads break under a naive per-user policy.** The
  three-tier caregiver model legitimately reads a member's data; RLS must add an
  explicit caregiver-authorized branch (or a separate caregiver DB principal), or
  every caregiver surface returns zero rows. Design in the Phase-1 matrix.

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
