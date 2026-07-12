# WS1 Phase 2 — Per-user RLS enforcement (design)

**Status:** design (2026-07-12). Follows Phase 1 (non-owner `companion_app` role +
grants, shipped). This is the enforcement phase: ENABLE + FORCE RLS + per-user
policies + the transaction-local `app.current_user_id` GUC. Source map:
backend access-pattern audit (2026-07-12).

## The golden rule (why ordering is everything)

Once a table is `ENABLE + FORCE ROW LEVEL SECURITY` with `USING (user_id =
current_setting('app.current_user_id'))`, **every query path that touches it must
set the GUC first, or it fail-closes to zero rows** — which reads as "data
missing," not an error. So the rollout is strictly:

1. **Deploy the GUC-setting mechanism covering ALL paths first** (it's a no-op
   until policies exist — setting a GUC nothing reads changes nothing).
2. Verify every path sets it (logging/guard).
3. **Only then enable policies**, table by table.

## GUC context model — `app.current_user_id` = the MEMBER whose data is accessed

| Path | GUC value | Where to set |
|---|---|---|
| Member session | their own `user.id` | `auth/dependencies.py:82` (`get_current_user`, both `db` + user in scope) |
| Caregiver | the **authorized member** id (`contact.user_id`) | `dependencies.py:179` + dashboard/activity authz (`caregiver_service` already scopes `where user_id == member`) — **app-authz first, then RLS scopes to that member. No caregiver branch in policies.** |
| Admin (member data) | n/a — admins read cross-user | admins mostly read **global** tables; genuine cross-user reads need bypass (below) |
| Worker (per-user loops) | each `user_id` in the loop | `SET LOCAL` per iteration in morning-checkin / medication-reminders / escalation-check / away-monitor (all already loop per user) |
| Worker (cross-user) | — | `retention`, `execute_deletion` span all users → **bypass role** (below) |

`SET LOCAL` (transaction-local) in the same transaction as the query — the
`get_db` request txn, or the worker's per-iteration txn. Never a session-wide
`SET` (pooled-connection bleed). Set `hnsw.iterative_scan = relaxed_order` in the
same place (the RAG path is already patched, but keep them together).

## The maintenance / bypass principal (required)

`retention` (purges Documents + `account_audit_log` across all users,
`retention.py:104`) and `execute_deletion` (deletes **other** members'
`trusted_contacts` / `caregiver_assignment_requests` by email,
`account_lifecycle_service.py:267-289`) are **legitimately cross-user** and
cannot run under a single member GUC. They need a dedicated principal.

**Decision D1:** add a `companion_maintenance` CNPG managed role with **BYPASSRLS**
(mirrors HCC's dedicated shred principal), used ONLY by the retention +
account-deletion workers (a second sealed cred + those workers connect as it).
Everything else stays on the non-bypass `companion_app`. Alternative: keep those
ops on the owner `companion` (already bypasses via ownership) — simplest, but
widens owner use. **Recommend: `companion_maintenance` BYPASSRLS** — least
privilege, explicit, auditable.

## Table-by-table policy matrix

**Standard per-user policy** `USING/WITH CHECK (user_id = current_setting)` — 13
member tables, all have `user_id` and are only read member-scoped:
`appointments, bills, chat_sessions, device_tokens, document_chunks, documents,
functional_memory, medications, pending_reviews, questions_tracker, todos,
trusted_contacts, caregiver_activity_log`.

**`user_encryption_keys`** — standard policy (PK is `user_id`), BUT only correct
if the GUC equals the user being crypto'd. Multi-user worker loops that decrypt
many users' fields must set the GUC per user (they already loop). Confirmed reads
are always the row owner. ✔ with per-iteration GUC.

**No `user_id` column — need through-parent or denormalization (Decision D2):**
- `chat_messages` (only `chat_session_id`) → parent `chat_sessions.user_id`
- `medication_confirmations` (only `medication_id`) → parent `medications.user_id`
- Options: (a) `USING (EXISTS (SELECT 1 FROM parent p WHERE p.id = fk AND
  p.user_id = current_setting))` — no schema change, small per-row cost; or
  (b) **denormalize a `user_id` column** (migration + backfill + set on write) —
  uniform policy, faster, but more code. **Recommend (a) EXISTS** for these two
  (low write volume; avoids a backfill migration), revisit if hot.

**Different key column:** `caregiver_assignment_requests` policy keys on
**`member_id`**, not `user_id`. Admin-override + caregiver-email-cleanup paths are
cross-user → they run under the bypass role / admin path.

**Global — NO per-user policy (must stay readable by `companion_app`):**
`system_config` (read on member paths — flags/grace period), `admin_users`
(auth reads by email, pre-GUC), `config_audit_log` (admin-only),
`pipeline_metrics` (admin/system aggregates over all docs), `alembic_version`.

**Audit tables that BREAK a strict equality policy → keep global:**
`account_audit_log` (nullable `user_id`; NULL refused-signup rows; retention
purges globally) and `deletion_audit_log` (user often already deleted; written
cross-user in loops). Leave global; append-only hardening is WS3.

**`users` — the bootstrap problem (Decision D3):** auth resolves the member by
**email** (`dependencies.py:76`) *before* any `user_id`/GUC exists. A policy
`id = current_setting` would return nothing → **all logins break**. Options:
- (a) **Exempt `users` from RLS** for now — its sensitive profile fields
  (phone/dob/address) are already field-encrypted; residual exposure is
  email/name/status cross-user. Simplest; unblocks Phase 2.
- (b) Bootstrap policy + a pre-GUC lookup path: set `app.current_external_subject`
  from the verified token and a `users` SELECT policy matching it — but Companion
  looks up by **email** today, not subject (that's the Authentik plan). Full fix
  lands with the Authentik migration (`external_subject_id`).
- **Recommend (a) now**, (b) when Authentik lands. (Cross-ref Authentik plan §3 +
  the PHI plan's `users` bootstrap note.)

## Loud unset-GUC guard (kali's regret, bake in from day one)

Fail-closed manifests as silent emptiness. Add a **dev/CI assertion** that a
tenant-table query with `app.current_user_id` unset RAISES (not returns 0). And a
CI `rls`-marked suite (live Postgres, not SQLite): two members, set member A's
GUC, attempt to read/write B's rows, assert zero/blocked; assert the bypass role
CAN cross-user; assert RLS + `iterative_scan` recall together.

## Safe sub-sequencing

| Step | Work | Risk |
|---|---|---|
| 2a | GUC-setting in the 3 auth deps + the 4 per-user workers (`SET LOCAL`), + a request/worker context helper. **No policies.** Deploy; verify every path sets it (temporary log/metric). | none (no-op) |
| 2b | `companion_maintenance` BYPASSRLS role (D1) + point retention/deletion workers at it | low |
| 2c | Enable RLS + policies on **ONE** low-risk member table (e.g. `todos`) behind verification; confirm member-scoped + caregiver + worker paths still work | medium |
| 2d | Roll out policies to the rest (standard + EXISTS + member_id); `users` per D3 | medium |
| 2e | `rls` negative-test CI gate + unset-GUC guard | — |

## Open decisions (for owner / kali)
- **D1** maintenance principal: `companion_maintenance` BYPASSRLS (recommended) vs reuse owner.
- **D2** chat_messages/medication_confirmations: EXISTS-through-parent (recommended) vs denormalize `user_id`.
- **D3** `users`: exempt now + fix at Authentik (recommended) vs bootstrap-subject policy now.
- Admin cross-user reads: confirmed mostly global tables; any genuine member-data admin read uses the bypass role.

## Files to port / touch
HCC `db/rls.py` (policy triplet builder — swap `account_id`→`user_id`), `db/context.py`
(`set_config(..., is_local=True)` at UoW enter). Companion: `auth/dependencies.py`
(3 inject points), the 4 per-user workers, a new `app/db/rls.py` (policy DDL, applied
by a migration) + `app/db/context.py` (GUC helper), `db/session.py`/`get_db` wiring.
