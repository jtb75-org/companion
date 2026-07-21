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
| Worker **discovery** (ALL workers) | — (bypass) | **Correction:** every worker first scans cross-user (`select all active users/meds` — morning/med/escalation/away too, not just retention/deletion). Under RLS these fail-close → the discovery scan runs under the **maintenance bypass role** (2c). |
| Worker **per-user work** | each `user_id` | per-user-session workers (`run_morning_trigger_for_user`, `run_medication_reminder_for_user`) set the GUC in their per-user function (done, 2a-ii). Inline-loop workers (`escalation_check`, `away_monitor`, `run_medication_reminder` main) set the GUC **per iteration** after the bypass discovery — done in 2c with the role. |
| Cross-user mutations | — | `retention`, `execute_deletion` legitimately write across users → scoped-bypass (D1): bypass the discovery read, `SET LOCAL ROLE companion_app` + GUC per user for writes. |

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

**Decision D1 — DECIDED:** `companion_maintenance` CNPG managed role, **BYPASSRLS
+ `inRoles: [companion_app]`**, used only by the retention + account-deletion
workers. **Critical refinement (kali):** the bypass is reserved for the narrow
**cross-user DISCOVERY** query (e.g. "which docs/users are due for purge" — a scan
RLS would fail-close). For the actual per-user **mutations**, the job does
`SET LOCAL ROLE companion_app` + `SET LOCAL app.current_user_id = <user>` so each
user's purge runs **RLS-scoped exactly like the app**. A job that runs entirely
under BYPASSRLS is "a loaded gun — one bad WHERE deletes across every user";
reserve the bypass for the read, fence every write.

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
- **DECIDED — denormalize `user_id`** onto both tables (kali: HCC put the tenant
  key on EVERY scoped table, never EXISTS-joined). An EXISTS-through-parent policy
  runs a correlated subquery **per row** and compounds under RLS — bad on a hot
  path like `chat_messages`. Add `user_id` (migration + backfill), set it at
  insert, and keep it honest with a **same-user trigger** (`child.user_id` must
  equal parent's — the `enforce_same_user` pattern). Policy is then the flat
  standard `user_id = current_setting`.

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

**`users` — the bootstrap problem (Decision D3 — DECIDED: keep `users` under RLS
now, via an email-GUC bootstrap policy).** Auth resolves the member by **email**
(`dependencies.py:76`) *before* any `user_id`/GUC exists, so a bare
`id = current_setting` policy breaks login. kali's mechanism (HCC keeps `users`
under RLS the whole time — it's the cross-user identity index you least want
unprotected):
- Before the email lookup, `SET LOCAL app.current_login_email = <email>`.
- `users` SELECT policy: `id = current_setting('app.current_user_id', true)::uuid
  OR email = current_setting('app.current_login_email', true)`. Writes stay
  self-only (`WITH CHECK id = current_user_id`).
- Same for the admin email lookup (`admin_users`) and caregiver `trusted_contacts`
  lookup — set the relevant lookup GUC before each bootstrap SELECT.
- When Authentik lands, swap the `email` clause for
  `external_subject_id = current_setting('app.current_external_subject')` — zero
  structural change. (Exempting `users` was the alternative; rejected because one
  app-authz bug = full email/name enumeration on exactly the worst table, and
  "exempt now, fix later" tends to become permanent.)

## Loud unset-GUC guard (kali's regret, bake in from day one)

Fail-closed manifests as silent emptiness. Add a **dev/CI assertion** that a
tenant-table query with `app.current_user_id` unset RAISES (not returns 0). And a
CI `rls`-marked suite (live Postgres, not SQLite): two members, set member A's
GUC, attempt to read/write B's rows, assert zero/blocked; assert the bypass role
CAN cross-user; assert RLS + `iterative_scan` recall together.

## Safe sub-sequencing

| Step | Work | Risk |
|---|---|---|
| 2a | `app/db/context.py` GUC helper + wire the 3 auth deps (`app.current_user_id`; `app.current_login_email` before the email lookup) + the **2 per-user-session** workers (`run_*_for_user` set the GUC). **No policies** → no-op. Deploy; verify every path sets it (temporary log/metric). | none (no-op) |
| 2b | Denormalize `user_id` onto `chat_messages` + `medication_confirmations` (migration + backfill + set-on-write + same-user trigger) | low |
| 2c | `companion_maintenance` BYPASSRLS+inRoles role. **ALL workers**: run the cross-user discovery scan under bypass, then per-user work via `SET LOCAL ROLE companion_app` + GUC — covers the inline-loop workers (`escalation_check`, `away_monitor`, `run_medication_reminder` main) and the scoped-bypass cross-user writes (`retention`, `execute_deletion`). Needs a maintenance engine/connection in the api pod. | low-med |
| 2d | Enable RLS + policies on **ONE** low-risk member table (`todos`); verify member + caregiver + worker paths | medium |
| 2e | Roll out policies to the rest (standard + `caregiver_assignment_requests` on `member_id` + `users` email-GUC bootstrap) | medium |
| 2f | `rls` negative-test CI gate + unset-GUC guard | — |

## Decisions (RESOLVED 2026-07-12, owner + kali)
- **D1** maintenance principal: **`companion_maintenance` BYPASSRLS + inRoles
  companion_app**; bypass only the cross-user discovery scan, `SET LOCAL ROLE
  companion_app` + GUC for per-user mutations.
- **D2** chat_messages / medication_confirmations: **denormalize `user_id`** +
  same-user trigger; flat standard policy.
- **D3** `users`: **keep under RLS now** via the email-GUC bootstrap policy;
  swap email→subject clause when Authentik lands.
- Workers: **`SET LOCAL` per iteration, inside the per-iteration transaction**
  (never a plain `SET` — pooled-connection bleed).
- Admin cross-user reads: mostly global tables; any genuine member-data admin
  read uses the bypass role.

## Files to port / touch
HCC `db/rls.py` (policy triplet builder — swap `account_id`→`user_id`), `db/context.py`
(`set_config(..., is_local=True)` at UoW enter). Companion: `auth/dependencies.py`
(3 inject points), the 4 per-user workers, a new `app/db/rls.py` (policy DDL, applied
by a migration) + `app/db/context.py` (GUC helper), `db/session.py`/`get_db` wiring.
