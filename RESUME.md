# RESUME — Companion self-hosted deploy (re-plan)

Session handoff. Last updated **2026-06-18**. **Status: companion is DEPLOYED
and Synced/Healthy on the live cluster** (stub-to-boot). All gitops/CI PRs
merged. Remaining = Firebase functional wiring + app-side config migration.

## ✅ Companion deployed (2026-06-18)
`kubectl get application companion -n argocd` → **Synced/Healthy**. In ns
`companion`: api `1/1`×2 (`/health` 200), web `1/1`, redis, CNPG paradedb DB
3/3, migrate Job Completed (alembic ran). Ingresses live via the tunnel
(`api`/`app`/`s3.mydailydignity.com` → traefik).
- **Deploy bugs found+fixed (companion-gitops PR #6, merged):** (1) paradedb
  needs `postgresUID/GID: 999` (CNPG default 26 → `initdb` "user ID 26 not
  found"); (2) migrate was a `PreSync` hook → deadlocked before its own DB/
  secrets → changed to `Sync` hook + sync-waves (db `-1` → secrets `0` →
  migrate `1` → api/web `2`).
- **One-time bootstrap done by hand this session** (kubectl apply the 3
  SealedSecrets + delete/recreate the broken db); with PR #6's waves a fresh
  deploy no longer needs it.
- **Still dark (expected):** prod Firebase Auth not provisioned + clients on
  staging (login won't work yet); LLM/GCS/KMS stubbed. Worker CronJobs error
  until api ready / pipeline path verified — recheck.

## ⚠️ Reality check (earlier RESUME/CLAUDE.md was stale by ~2 months)

## ⚠️ Reality check (RESUME was stale by ~2 months)

The earlier notes / `CLAUDE.md` (dated 2026-04-19) say "hardware ordered,
pre-execution." **That is wrong.** Verified live on 2026-06-18:

- **Cluster is LIVE**: 5 nodes — `mini01-04` (control-plane/etcd + worker) +
  `tower01` — k3s v1.34.6, up 54–56 days.
- **Platform is DONE**: ArgoCD root-app + ~32 apps all **Synced/Healthy**,
  including `blue` (deployed on the *exact* pattern companion-gitops uses),
  `cnpg-operator`, `minio`, `sealed-secrets`, `cert-manager`, `longhorn`,
  `authentik`, `openbao`, `cloudflared`, `traefik`, `zot`.
- **Companion is NOT deployed** — `companion` namespace doesn't exist
  (argocd-apps #38 unmerged, so root-app hasn't picked it up).

> TODO (follow-up PR): `CLAUDE.md` "Current state" + "Next steps" are stale
> (pre-execution framing, "Companion does not have its own gitops repo").
> Update once we resume — not touched this session to avoid clashing with PR #1.

## Verified facts (from the live cluster)

- **pgvector image** → use `paradedb/paradedb:17-v0.23.1` (already used by
  `atlas-db`; paradedb bundles pgvector). Replaces the `db-cluster.yaml` TODO.
- **sealed-secrets** → controller is `sealed-secrets-controller` in namespace
  **`infra`** (NOT `kube-system` as companion-gitops README currently says).
  `kubeseal` v0.37.0 is installed locally → can seal from this machine:
  `kubeseal --controller-name sealed-secrets-controller --controller-namespace infra`
- **TLS** → `letsencrypt-prod` ClusterIssuer solver covers only
  `ng20.org` + `silkstrand.io`. **`mydailydignity.com` must be added**
  (DNS-01 via cloudflare; the `cloudflare-api-token` must manage that zone).
- **zot** is up (needs auth); `jtb75-arc` runner is up. No `companion-*`
  images in zot yet (first CI build creates them).
- **GITOPS_DEPLOY_TOKEN** confirmed: admin/push on `companion-gitops`.

## Repo / PR state (both GREEN + mergeable)

| Repo | State |
|---|---|
| `companion` | PR **#1 MERGED** (agents/workflow/CI) + PR **#2 MERGED** (c86ee04, single-stage Dockerfile fix). **build-and-push PIPELINE WORKS**: images push to zot + companion-gitops auto-bumps. |
| `companion-gitops` | `main` = `6a23003`; CI auto-bumps image tags (e.g. `6a69bae`). Open PRs: **#1** ingresses, **#2** paradedb+README+review-fixes, **#3** zot-registry, **#4** secrets (stacked on #3). Merge order: #3→#4; #1/#2 any time. |
| `argocd-apps` | **#38** register companion app, **#39** MinIO s3 ingress — both OPEN, validate green. |

> NOTE: custom `.claude/agents` types now on `main` but only dispatchable in a
> NEW session (registry loads at startup). This session falls back to
> `general-purpose` carrying each role's mandate.

## Guiding decision

> **Everything local in k3s EXCEPT Firebase** (auth + FCM stay). DB→CNPG
> +pgvector(paradedb), GCS→MinIO, LLM→Ollama, embeddings→bge-m3,
> OCR→PaddleOCR, KMS→sealed-secrets/OpenBao, Pub/Sub→in-cluster.

## Networking & secrets re-plan (from this session's direction)

We are authed to both **gcloud** (owner on companion projects) and
**Cloudflare** (`CF_TOKEN` in `.env`). Open work:

1. **Firebase (KEEP, recreate/retrieve config).** GCP compute/db are torn down
   but the Firebase projects remain. Recreate/retrieve the Firebase **web app
   config** from `companion-prod-491606` for the web build's `VITE_*` values
   and the backend's firebase settings.
2. **Cloudflare / DNS — ANALYZED 2026-06-18.** Registrar **and** DNS host =
   Cloudflare. Registered 2026-03-29, expires 2027-03-29. NS
   `abdullah/tani.ns.cloudflare.com`. Zone id `431feb79120e992de2a7128b698d7ccd`.
   Current records (the relevant ones):
   - `*.mydailydignity.com` → **proxied to tunnel `54d42b8c-046b-45bd-a9c5-d980bcf62523`**
     — this is **NOT our cluster tunnel** (`530c92d3-6639-4d6f-9b76-df74b710fbc2`).
     Stale/foreign tunnel. `www.` and `s3.` resolve via this wildcard today.
   - `app.` and `api.` → CNAME `ghs.googlehosted.com` (dns-only) — **stale GCP**
     (Firebase Hosting / Cloud Run domain mapping; GCP is torn down → dead).
   - No apex `A` record.
   - Email (leave as-is): Cloudflare Email Routing MX + SES + firebasemail DKIM
     + SPF (`firebasemail`, `google`, `cloudflare`, `amazonses`).
   - `TXT firebase=companion-staging-491606` → domain verified to the **STAGING**
     Firebase project (app prod config expects `companion-prod-491606` — mismatch
     to resolve).
   - `CF_TOKEN` is **zone-scoped** (mydailydignity.com only; no account/tunnel
     API). Adding tunnel public-hostname routes needs the Zero Trust dashboard
     or a broader token; DNS record edits may be possible with this token (perm
     not yet confirmed).
   **DECISION: tunnel.** Done 2026-06-18:
   - ✅ Repointed `*.mydailydignity.com` (proxied) → OUR tunnel
     `530c92d3-6639-4d6f-9b76-df74b710fbc2.cfargotunnel.com`.
   - ✅ Deleted stale `app.`/`api.` → `ghs.googlehosted.com` CNAMEs (now
     governed by the wildcard).
   - Result: hosts resolve to our tunnel; HTTP returns cloudflared **404
     (empty body)** = tunnel has no public-hostname route for the zone yet.
     (Confirmed `*.ng20.org` → traefik route exists; mydailydignity needs the
     same.)
   - ✅ **Tunnel route added 2026-06-18** (after CF_TOKEN got Tunnel:Edit):
     tunnel `530c92d3` (name `k3s-home`) ingress now has
     `*.mydailydignity.com → http://traefik.traefik.svc.cluster.local:80`
     (mirrors `*.ng20.org`/`*.silkstrand.io`). Verified: all
     `*.mydailydignity.com` hosts now hit **traefik** (traefik `404 page not
     found` = path works, no Ingress matches yet). Edge TLS handled by
     Cloudflare (proxied / Universal SSL).
   **Ingresses — PRs OPEN 2026-06-18 (web entrypoint, no TLS, tunnel pattern):**
   - ✅ companion-gitops **PR #1** — `api.`/`app.mydailydignity.com` ingresses
     switched to `entrypoints: web` (mirrors minio-s3-external).
   - ✅ argocd-apps **PR #39** — `s3.mydailydignity.com` MinIO ingress.
   - Both verified: hosts already reach traefik (404 until these merge + the
     companion app deploys / MinIO matches the host).
   **Still open:**
   - cert-manager `letsencrypt-prod` solver for `mydailydignity.com` is **only
     needed for a direct LAN/WAN (websecure+TLS) path** — NOT for the tunnel
     (Cloudflare does edge TLS). Defer unless we add `*-lan` ingresses.
   - Firebase: domain is verified to **staging** — decide staging vs prod and
     redo verification/`VITE_*` accordingly.
3. **TLS.** Add `mydailydignity.com` to the `letsencrypt-prod` ClusterIssuer
   solver (cloudflare DNS-01) so `app.`/`api.`/`s3.mydailydignity.com` get certs.
4. **MinIO alias.** MinIO is healthy; add an ingress/alias for
   **`s3.mydailydignity.com`** (mirrors the existing `s3.ng20.org` MinIO
   ingresses in `argocd-apps/infra/minio/`).
5. **companion-secrets.** Settle the key set under local-except-Firebase, then
   `kubeseal` → `companion-secrets-sealed.yaml` + `zot-registry-sealed.yaml`,
   and uncomment them in `overlays/onprem/kustomization.yaml`.

## Agent review (2026-06-18)

Ran two reviewers over all open PRs (as `general-purpose` — the custom
`.claude/agents` types aren't registered until PR #1 merges to main):
- **infra correctness → APPROVE.** Kustomize builds, port/CNPG-key/SA-mount/CI
  wiring all correct and faithful to blue. No blockers.
- **safety-privacy → APPROVE. Plaintext credentials exposed: NO** (full history
  scan, 3 repos). SealedSecrets encrypted, `.env` gitignored + never tracked,
  CORS/dev_auth_bypass/tier-isolation intact.

Fixes applied from the review:
- ✅ SA role narrowed: `firebase.sdkAdminServiceAgent` → `firebaseauth.admin` +
  `firebasecloudmessaging.admin` (least privilege).
- ✅ db-cluster `sync-wave: -1` (PR #2).
- ✅ `companion-secrets.example.yaml` kubeseal controller corrected (PR #2).
- Noted: SA key is long-lived → plan rotation. Runtime secrets
  (ANTHROPIC/KMS/GMAIL) deferred (stub-to-boot) — fill before traffic.
- **HIGH (go-live gate):** auth project mismatch → see step 5.

## NEXT STEPS (after re-plan — paused per user)

1. ✅ **companion-gitops corrections — PR #2 OPEN** (paradedb image +
   sealed-secrets/`infra` README fix).
2. **Ingresses — PRs OPEN:** companion-gitops #1 (`app.`/`api.`), argocd-apps
   #39 (MinIO `s3.`). cert-manager issuer change only needed for a direct
   LAN/WAN path (tunnel uses Cloudflare edge TLS).
3. ✅ **Secrets — sealed (DECISION: prod Firebase, stub-to-boot).** PRs:
   - companion-gitops **#3** — `zot-registry` pull secret.
   - companion-gitops **#4** (stacked on #3) — `companion-secrets`
     (generated pipeline key + `COMPANION_FIREBASE_PROJECT_ID`/
     `COMPANION_GCP_PROJECT_ID=companion-prod-491606`; LLM/SMTP/KMS at defaults)
     + `companion-firebase-sa` (SA JSON) + api-deployment SA mount.
   - **Created in GCP:** SA `companion-backend@companion-prod-491606`
     (`roles/firebase.sdkAdminServiceAgent`) + a key (sealed; plaintext shredded).
   Merge order: #3 → #4 (stacked); both before #38.
4. **Merge companion #1** → first CI build → images in zot → companion-gitops
   tag bump. (Safe: doesn't deploy until the app is registered.)
5. **GO-LIVE GATE — commit to PROD Firebase (DECIDED).** Backend is on
   `companion-prod-491606`; clients still target staging, so logins break until
   reconfigured. Workstream:
   - ✅ **addFirebase done** — `companion-prod-491606` is now a Firebase project
     (was a bare GCP project; the API being enabled ≠ Firebase added).
   - **Register prod client apps + pull config (IN PROGRESS, #2).** Firebase
     Mgmt REST API (no firebase CLI): `gcloud auth print-access-token` + header
     `x-goog-user-project: companion-prod-491606`; zsh-brace `${VAR}` before `:`.
     iOS bundle `com.mydailydignity.companion`, Android pkg `com.companionapp`,
     Web "Companion Web". Then wire: web SDK config → GH **vars** `VITE_FIREBASE_*`
     on jtb75-org/companion; iOS plist + android json → GH **secrets**
     `FIREBASE_IOS_PLIST`/`FIREBASE_ANDROID_JSON` (replace staging).
   - Provision prod Firebase **Auth** (enable Google sign-in provider; app uses
     @react-native-google-signin + Firebase). identitytoolkit enabled; provider
     + OAuth client config still needed.
   - Domain `firebase=` TXT → prod (currently staging); decide user migration
     staging→prod vs fresh.
6. **Merge argocd-apps #38** → root-app deploys companion to the live cluster.
7. **App-side migration (config.py):** GCS→MinIO S3 endpoint, LLM→Ollama
   (D1), embeddings→bge-m3 (D2), OCR→PaddleOCR (D4), KMS→sealed-secrets.
8. **Worker CronJobs:** only `morning-checkin` + `medication-reminders` wired;
   add internal endpoints + CronJobs for away-monitor, escalation-check,
   ttl-purge, retention, account-deletion.

## Retire (GCP torn down)
Legacy GCP deploy workflows `deploy-staging.yml` / `deploy-prod.yml` /
`destroy.yml` — remove once we're satisfied the self-hosted path is the only one.

## Handy context
- Macs (Ollama bare-metal): `studio-max` 192.168.0.94, `studio-ultra`
  192.168.0.104, `0.0.0.0:11434`. Models NOT yet pulled.
- Registry `zot.lan.ng20.org`; runner `jtb75-arc`; sealed-secrets ns `infra`.
- Reference: `~/repo/blue` + `~/repo/blue-gitops`; `~/repo/argocd-apps`.
- Plan: `docs/migration-plan.md`. `.env` (gitignored): `CF_TOKEN`,
  `GITOPS_DEPLOY_TOKEN`, `ZOT_USERNAME`, `ZOT_PASSWORD`.
