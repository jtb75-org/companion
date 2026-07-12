# Authentik Auth Migration Plan — retire Firebase

**Status:** proposed (2026-07-12). **Goal:** move Companion fully self-hosted by
retiring Firebase — both **Firebase Auth** (the only auth today) and, as a
separate later workstream, **Firebase Cloud Messaging** — in favor of a
**dedicated in-cluster Authentik** stack.

**Reference implementation:** `~/repo/healthcostclarity` (HCC), whose main
developer (kali) already built this exact pattern. Much of the backend is a
direct port. This plan folds in kali's hard-won gotchas.

---

## 1. Decisions (settled 2026-07-12)

| # | Decision | Choice |
|---|---|---|
| Stack | Dedicated vs shared Authentik | **Dedicated** `companion-authentik` (own server + DB + redis), not shared `auth.ng20.org` |
| Login | UI ownership | **BFF native-login** — our own branded login form; backend drives Authentik server-side. No Authentik-hosted UI for members. |
| Mobile | RN auth | **BFF in-app login** (not `react-native-app-auth`) — accessibility: no system-browser hop for developmental-disability users |
| Mobile token | Session transport | **Opaque Redis-backed session token returned as a Bearer** (reuse `session.py`; sid-as-bearer for mobile, httpOnly cookie for web). **No OIDC tokens on the device.** |
| MFA | Policy | **Members/caregivers: none** (frictionless). **Admin/staff: optional** via a separate login path (see §4). |
| Push | Scope | Replace FCM/APNs too — but see §6; Android push without Google is a genuine constraint. |

---

## 2. Target architecture

```
Member / caregiver (web SPA + RN app)
        │  POST email+password  (our own login form)
        ▼
Companion FastAPI  /api/v1/auth/login   ── BFF ──►  companion-authentik  (in-cluster, TLS)
        │  drives flow-executor (identification → password),                 │
        │  then PKCE authorize → token  ────────────────────────────────────►│
        │  ◄── id_token ──────────────────────────────────────────────────────
        │  verify (PyJWT + JWKS, RS256)
        │  provision (invite-gated) → mint opaque session (Redis)
        ▼
   web  → Set-Cookie httpOnly `companion_session` + CSRF cookie
 mobile → { token: <sid> }  → RN stores in Keychain → Authorization: Bearer <sid>

Admin / staff (web only)  → standard hosted OIDC redirect → Authentik login (+ optional MFA) → bearer
```

- **One verifier, one session store, two transports.** `get_verified_token`
  accepts Bearer **or** session cookie (Bearer wins). Mobile's Bearer is the
  same opaque Redis sid the web cookie carries — not an OIDC token.
- **Authorization stays app-side** (our `admin_users` / `users` + field_crypto +
  caregiver tiers). Authentik authenticates; it never authorizes. We do **not**
  key off Authentik group claims.

---

## 3. Identity & provisioning (keep invite-only)

- Today: `users.firebase_uid` ← Firebase. After: store the Authentik OIDC `sub`
  (per-provider hash under `sub_mode=hashed_user_id`) as the external subject.
- **Invite-only is preserved.** On invite/admin-create we (a) create the
  Authentik user via its API, (b) keep the existing `users` stub
  (`account_status='invited'`). First login flips INVITED→ACTIVE. Provisioning
  is gated on an existing stub — we do **not** enable HCC's `auto_provision`.
- `admin_users` is unchanged; admin role resolves from our table keyed by the
  verified subject/email.
- **`sub_mode` is immutable** — pick it once at provider creation and never
  change it (changing re-keys every user). Decide before provisioning anyone real.
- **Cross-path subject consistency (kali):** the member BFF path and the staff
  hosted-redirect path (§4) land on the **same verifier + same
  principal/provisioning**, so a user MUST resolve to the same `sub` regardless
  of which path they used. `sub_mode=hashed_user_id` is a **per-provider** hash —
  if the two paths use two different OIDC providers, the same person keys as two
  different subjects. Fix: either (a) use a **single OIDC application** for both
  the BFF and the hosted-redirect flows, or (b) use **`sub_mode=user_uuid`**
  (stable across providers). **Recommended: `user_uuid`** — it survives even if
  we later add providers, and sidesteps the whole per-provider-hash class of
  bugs. (HCC used `hashed_user_id` because it had exactly one provider/path.)

---

## 4. MFA login-path split

- **Members + caregivers** → BFF in-app form. The flow-executor driver handles
  `ak-stage-identification` + `ak-stage-password` only; an authenticator stage
  raises `MfaRequired`. Their Authentik flow has **no** authenticator stage, so
  it never triggers. No browser, no MFA prompt.
- **Admin / staff (web)** → the standard **hosted OIDC redirect** (browser →
  Authentik login page). This inherits Authentik's MFA ladder for free — no need
  to build a "Phase B" authenticator stage into our flow-executor driver. Staff
  are a small, capable population for whom a redirect + optional TOTP is fine.
- This split means we implement **two** login entry points but only **one**
  token/session model behind them.

---

## 5. Phases

### Phase 0 — Spike (prove the flow)
Port `oidc.py` + `authentik_flow.py` into a throwaway branch; drive a
`companion-authentik` dev instance (or HCC's, for the shape) end-to-end:
identification → password → PKCE → token → verify. Validates the flow-executor
stage names and provider config before committing to infra. **Pair with kali.**

### Phase 1 — Infra (`companion-authentik`)
- ArgoCD app in `~/repo/argocd-apps` (reference `~/repo/authentik-gitops` +
  HCC's `hcc-authentik` manifests): Authentik server + worker, dedicated **CNPG
  `companion-authentik-db`**, redis. Namespace `companion-authentik`.
- Secrets via sealed-secrets (ns `infra`); bootstrap creds → OpenBao.
- Create the OIDC **provider + application** for Companion. **Gotcha (kali):
  API-created providers default `grant_types` AND `property_mappings` to EMPTY**
  → no email claim → mysterious 403s. Set scope mappings (openid/profile/email)
  explicitly. `redirect_uris` is an **object list** (matched-mode + url), not
  bare strings. Note `issuer_mode=per_provider` (internal iss ≠ public iss).
  Provider config is manual/non-gitops at HCC; if we gitops it, **pin the
  `client_id`** so recreates don't orphan users.
- Decide issuer exposure: internal-only (BFF reaches it in-cluster; members
  never hit Authentik directly) **+** a public issuer host only if/where the
  staff hosted-redirect path needs it. Default: internal for the BFF, expose a
  minimal `auth.mydailydignity.com` only for the staff redirect.

### Phase 2 — Backend
- Port `backend/app/auth/{oidc,authentik_flow,session,ratelimit}.py` and the
  `deps` bearer-or-session chain. Companion already runs `companion-redis` →
  reuse `RedisSessionStore`.
- New `/api/v1/auth/login` + `/logout`:
  - web → set httpOnly `companion_session` cookie + CSRF cookie.
  - mobile (detected via header/param) → return `{ token: <sid> }`; **same sid**,
    Bearer transport. `verify(require_issuer=False)` for the BFF-fetched
    in-cluster id_token; `require_issuer=True` for any browser bearer.
- Replace `verify_firebase_token` in `dependencies.py` with the OIDC verifier;
  keep `AdminUser` / `require_admin_role`, sourcing identity from the verified
  subject/email.
- Wire invites → Authentik user creation (Authentik API) + subject mapping onto
  `users`. Password reset/recovery → Authentik recovery flow (replaces Firebase
  reset). Add `pyjwt[crypto]`; remove `firebase-admin`.
- **Safety-privacy-reviewer sign-off required** (auth + user-data path) before
  merge.

### Phase 3 — Web portals (admin / caregiver)
- Swap Firebase JS SDK → our login form POSTing `/auth/login` (cookie session).
- Admin/staff sign-in wired to the hosted-redirect path (§4).

### Phase 4 — Mobile (React Native)
- Remove the Firebase Auth SDK; replace with the BFF login (POST creds → store
  the returned sid in Keychain via `react-native-keychain` → `Authorization:
  Bearer`). Sliding TTL server-side; on 401 the app re-prompts (re-login) — no
  client-side OIDC refresh to manage.
- Keep the login screen visually identical to today so the UX doesn't change for
  members.

### Phase 5 — Staff MFA (optional)
- Enable an authenticator stage (TOTP/passkey) on the **staff** Authentik flow
  only. Members' flow stays password-only. No backend change (hosted redirect
  owns the ladder).

### Phase 6 — Push migration (FCM/APNs) — separate workstream, see §6
Sequence after auth is stable; do **not** couple to the auth cutover.

### Phase 7 — Cutover & Firebase removal
- Only one real user today (`smoketest@mydailydignity.com`) → recreate in
  Authentik, re-map subject. No bulk migration.
- Dual-run: feature-flag the auth provider (firebase|authentik) so we can flip
  and roll back per-environment. Validate member web + member mobile + caregiver
  + admin(+MFA). Then flip, soak, and remove `firebase-admin`,
  `companion-firebase-sa`, and Firebase config.

---

## 6. Push (FCM) — honest constraint

Firebase **Auth** and Firebase **Cloud Messaging** are independent; retiring
Auth does not require touching push. "Fully local push" is harder than it looks:

- **iOS** → **direct APNs** (token-based, .p8 key) is clean and fully removes
  Google from the iOS push path. Straightforward.
- **Android** → consumer Android push effectively **requires FCM** (Google's
  transport). The Google-free options are poor fits for our users:
  - *UnifiedPush* (e.g. self-hosted ntfy) requires the user to install a
    separate "distributor" app — a non-starter for a developmental-disability
    audience.
  - WebSocket/long-poll while foregrounded — no reliable background delivery.
- **Recommendation:** iOS → direct APNs now; **Android → keep FCM as a
  transport-only dependency** (no Firebase Auth, minimal Firebase footprint) and
  revisit only if a Google-free Android push story becomes viable. Flag this back
  to the owner — "also replace push" is fully achievable on iOS but only
  partially on Android without degrading reliability.

---

## 7. Risks & open sub-decisions

- **`sub_mode` choice is permanent** (§3) — settle before provisioning real
  users; recommend `user_uuid` so the member and staff login paths resolve to the
  same subject (per-provider `hashed_user_id` would split identity across paths).
- **Issuer exposure** (§Phase 1) — internal-only BFF + minimal public host for
  staff redirect is the default; confirm.
- **Android push** (§6) — accept FCM-as-transport, or invest in a Google-free
  path? Owner decision.
- **Session TTL / re-login UX on mobile** — members shouldn't be logged out
  abruptly; tune sliding TTL (HCC uses 12h) and consider a long-lived refresh sid
  for the app if re-login friction is too high.
- **Recovery/reset flow** for members with limited literacy — design the
  Authentik recovery flow for accessibility (may need caregiver-assisted reset).

---

## 8. Rollback

Per phase: auth-provider feature flag flips back to Firebase (Firebase code and
`companion-firebase-sa` stay in place until Phase 7 soak completes). Infra
(`companion-authentik`) is additive — standing it up changes nothing until the
backend flag points at it.

---

## 9. Testing

- Backend: unit-test `OIDCVerifier` with a local RSA key (no network — HCC
  injects the JWKS client); flow-executor driver against a mocked Authentik;
  bearer-or-session dep matrix; invite-gated provisioning (stub required).
- Integration: real `companion-authentik` dev instance — login, wrong password
  (401 + rate-limit), logout revokes, expired session.
- E2E: member web, member mobile (Keychain bearer), caregiver, admin+MFA.
- qa-test owns the CI gate; safety-privacy-reviewer signs off the auth path.

---

## Appendix — files to port from HCC

`backend/app/auth/oidc.py` (verifier), `authentik_flow.py` (~120 lines, directly
liftable), `session.py` (Redis session — return sid as bearer for mobile),
`deps.py` (bearer-or-session), `ratelimit.py` (login throttle), `api/auth.py`
(login/logout). See also HCC `docs/security.md` for the consent/session posture.
