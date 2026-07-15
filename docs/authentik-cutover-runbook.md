# Authentik cutover & Firebase-retirement runbook (owner)

Owner-facing checklist to finish the Firebase → self-hosted **Authentik** auth
migration. **All code is merged** (backend BFF cutover + dual-run, all three client
login surfaces, all four provisioning/activation cohorts, password policy). What
remains is **infrastructure + operational** flips, plus one final code PR to remove
Firebase once every cohort is on Authentik.

Do the phases in order. Every step through Phase 2 is **reversible**; the point of
no return is Phase 3.

---

## Phase 0 — where things stand today

| Surface | Auth today | Flag |
| --- | --- | --- |
| **Backend** | **Authentik, dual-run** (accepts an Authentik session **OR** a Firebase bearer as fallback) | `COMPANION_AUTH_PROVIDER=authentik` (already set, prod) |
| **Web dashboard** | Firebase (Google + email/password) | `VITE_AUTH_PROVIDER` unset → `firebase` |
| **Mobile app** | Firebase | `AUTH_PROVIDER = 'firebase'` in `companion-app/src/auth/authConfig.ts` |

**Why this is safe to flip piecemeal:** the backend is in Authentik mode but
*dual-run* — a client sending a Firebase bearer still works (fallback in
`app/auth/principal.py`), and a client sending an Authentik cookie/bearer session
also works. So each client can flip independently, and a broken client rolls back by
reverting just its flag — the backend never has to move.

**Provisioning is already wired** (PRs #90–#93): creating any account (caregiver
invite, admin, member) under Authentik auto-creates the Authentik user and emails a
branded activation link — **but only when** `COMPANION_AUTH_PROVIDER=authentik`
**AND** `COMPANION_AUTHENTIK_API_TOKEN` is set. The token is not set yet, so
provisioning is currently inert (Step 1 fixes that).

---

## Phase 1 — pre-flip prerequisites (gate the cutover)

### 1. Seal a **scoped** Authentik admin-API token

The backend provisions accounts + sets passwords via the Authentik admin API using
`COMPANION_AUTHENTIK_API_TOKEN`. Use a **least-privilege** token, not the superuser
bootstrap one.

- In Authentik: create a dedicated service account (e.g. `companion-provisioner`)
  with only **user create + set-password** permission; issue its token
  (Directory → Tokens).
- Seal it into the `companion-secrets` Sealed Secret as
  `COMPANION_AUTHENTIK_API_TOKEN` (see
  `companion-gitops/overlays/onprem/companion-secrets.example.yaml`):
  ```
  kubeseal --format yaml --controller-name sealed-secrets-controller \
    --controller-namespace infra < companion-secrets.plain.yaml \
    > companion-secrets-sealed.yaml
  ```
  `envFrom` already loads the whole secret — **no deployment change**; the api pods
  pick it up on the next rollout.
- **Verify:** create a throwaway admin (`POST /admin/admin-users`) and confirm an
  Authentik user appears + an activation email is sent. Delete the throwaway.

### 2. Host the mobile deep-link association files

The member activation link (`https://app.mydailydignity.com/activate?token=…`) opens
the app via Universal / App Links. The `.well-known` files are built into the web
bundle (`web/public/.well-known/`, → `dist/.well-known/`), but the **serving**
config must be correct — see `web/public/.well-known/README.md`:

- Serve `GET /.well-known/apple-app-site-association` and `/.well-known/assetlinks.json`
  as the **static files** (exclude `/.well-known/*` from the SPA history-fallback,
  or they return `index.html`).
- Serve both as **`Content-Type: application/json`** (the AASA file has no extension).
- Reachable over **HTTPS with a 200, no redirect**.

### 3. Android release signing SHA-256

`web/public/.well-known/assetlinks.json` ships a placeholder. Replace
`REPLACE_WITH_RELEASE_SIGNING_SHA256` with the SHA-256 of the **release** signing
cert (Play App Signing key if enrolled):
`keytool -list -v -keystore <release.keystore> -alias <alias>` → the `SHA256:` line.
Until this is real, Android `/activate` links fall back to the browser (safe,
degraded) — **iOS is unaffected**.

### 4. iOS Associated Domains

- Enable the **Associated Domains** capability for App ID
  `com.mydailydignity.companion` (Team `2NQD86RATH`) in the Apple Developer portal /
  provisioning profile — the in-app entitlement (`applinks:app.mydailydignity.com`)
  is not enough on its own.
- Confirm the `AppDelegate.swift` `RCTLinkingManager` continue-userActivity handler
  **compiles in an Xcode build** (it was added but only header-verified, not built).

### 5. (Cross-reference) broader pre-real-PHI gates

Not strictly part of the auth flip, but required before onboarding **real** members
with PHI — see `CLAUDE.md` → "Next steps": OpenBao **audit device**, OpenBao **TLS**
(listener is `tls_disable=1`), **vault the encryption keys** off-cluster, **egress
NetworkPolicy** on `companion-ocr`. Track these alongside the cutover.

---

## Phase 2 — the cutover (per surface, reversible)

Do one surface at a time and validate before the next. The backend stays put.

### 2a. Flip the **web dashboard** → Authentik

- In `.github/workflows/build-and-push.yml`, add `VITE_AUTH_PROVIDER` to the
  **"Build web bundle"** env block (alongside `VITE_API_BASE_URL`), sourced from a
  new repo **Actions variable** `VITE_AUTH_PROVIDER=authentik`. Push to `main` (or
  re-run the workflow) to rebuild + redeploy the web image.
- **Validate:** at `app.mydailydignity.com`, the login page shows email/password only
  (no Google/Register); an **admin** logs in → `/ops`; a **caregiver** logs in →
  `/caregiver/alerts`; a caregiver **invite-accept** link works (create-password for a
  first-time invitee, sign-in for a returning one).
- **Rollback:** set the Actions var back to `firebase` (or remove it) and rebuild —
  the web returns to Firebase login; the backend never moved.

### 2b. Flip the **mobile app** → Authentik

- Set `AUTH_PROVIDER = 'authentik'` in `companion-app/src/auth/authConfig.ts`, then
  **build, sign, and ship** (TestFlight / Play internal first).
- **Validate:** a **member** signs in via the branded Authentik login; a member
  **activation deep link** opens the app to the set-password screen (iOS now;
  Android once Step 3 lands) → sets a password → lands in the app; a **caregiver** on
  mobile (if applicable) signs in.
- **Rollback:** revert the constant and ship again. (Slower than web — app-store
  round-trip — so validate hard in TestFlight/internal before promoting.)

### 2c. Re-invite the cohorts into Authentik

New invites auto-provision + email activation (Step 1 must be done first).

- **Admins:** `POST /admin/admin-users` → activation email → `/activate` (web).
- **Members:** create via the admin People / companion-users flow → activation email
  → `/activate` deep link opens the app (Step 2b + 4).
- **Caregivers:** member/admin invites → the existing invite-accept link → the
  first-time invitee sets a password inline.
- **Validate each cohort:** account provisioned in Authentik, activation email
  received, password set (respecting the **≥10-char + non-common** policy), and login
  succeeds on the right surface.

**Password policy reminder** (PR #94): both set-password seams enforce min length
`COMPANION_PASSWORD_MIN_LENGTH` (default **10**) + a common-password denylist +
trivial-pattern/email checks, returning a plain message. Tune the floor via that
env var if needed.

---

## Phase 3 — retire Firebase (final code PR — point of no return)

**Only after every cohort is confirmed on Authentik.** This removes the dual-run
safety net, so do it last and deliberately. This is a **code change** (ask Claude to
implement it):

1. Remove the Firebase-bearer fallback from `app/auth/principal.py` (and the
   `resolve_*` fallbacks) so auth is Authentik-only.
2. Remove Firebase verification code, the `firebase-admin` dependency, the
   `companion-firebase-sa` secret + mount, and Firebase config from web/mobile.
3. Retire the Google sign-in UI paths (already hidden under `authentik`, now delete).
4. Decommission the Firebase/GCP project (`companion-prod-491606`) auth once nothing
   references it. Keep Vertex/Gemini (LLM) separate — that's a different GCP surface.

Ship it behind CI + the usual niru + safety review. After this, rollback means
re-adding the fallback code — no longer a config flip.

---

## Rollback matrix

| If this breaks | Roll back by | Speed |
| --- | --- | --- |
| Web login (Phase 2a) | Actions var `VITE_AUTH_PROVIDER=firebase` + rebuild | minutes |
| Mobile login (Phase 2b) | Revert `AUTH_PROVIDER='firebase'` + ship | app-store |
| Provisioning misbehaves | Clear `COMPANION_AUTHENTIK_API_TOKEN` (provisioning goes inert; existing logins unaffected) | minutes |
| Backend auth broadly | `COMPANION_AUTH_PROVIDER=firebase` — **but only if all clients are back on Firebase first** (an Authentik-session client would 404 against a firebase-mode backend) | minutes |
| After Phase 3 | Re-add the dual-run fallback code (PR) | code change |

---

## Reference

**Flags**

- `COMPANION_AUTH_PROVIDER` (backend, gitops `companion-config.yaml`): `firebase | authentik`. Already `authentik`.
- `VITE_AUTH_PROVIDER` (web, build-time): `firebase` (default) | `authentik`.
- `AUTH_PROVIDER` (mobile, `authConfig.ts`): `'firebase'` | `'authentik'`.
- `COMPANION_PASSWORD_MIN_LENGTH` (backend): set-password floor, default `10`.

**Secrets (companion-secrets Sealed Secret)**

- `COMPANION_AUTHENTIK_API_TOKEN` — scoped Authentik service-account token (Step 1).

**Endpoints (Authentik-only; 404 under firebase)**

- `POST /auth/login` `{username, password, mobile?}` — BFF login (cookie for web, bearer for mobile).
- `POST /auth/logout`.
- `GET /api/v1/invitations/validate?token` · `POST /api/v1/invitations/set-password` · `/accept` · `/decline` — caregiver invite flow.
- `GET /api/v1/activation/validate?token` · `POST /api/v1/activation/set-password` — generic (admin + member) activation.

**Key files**

- Backend: `app/api/auth_authentik.py`, `app/auth/principal.py`, `app/api/v1/{invitations,activation}.py`, `app/integrations/authentik_admin.py`, `app/services/{activation_service,password_policy}.py`.
- Web: `src/shared/auth/{AuthProvider,LoginPage}.tsx`, `src/shared/invite/AcceptInvitationPage.tsx`, `src/shared/activate/ActivatePage.tsx`, `src/shared/api/client.ts`.
- Mobile: `src/auth/{authConfig,authApi,AuthProvider,AuthentikLoginScreen,AuthentikActivateScreen}.tsx`, `src/navigation/{AppNavigator,linking}.ts`, native iOS/Android deep-link config.
- Deploy: `companion-gitops/overlays/onprem/companion-config.yaml`, `.github/workflows/build-and-push.yml`, `web/public/.well-known/`.

**Background:** `docs/authentik-migration-plan.md`, and the auto-memory note
`authentik-migration-blueprint` (live state across the migration PRs #71–#94).
