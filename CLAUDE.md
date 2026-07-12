# Companion (D.D.) — project notes for future sessions

## Active initiative: self-hosted migration (largely COMPLETE)

Migrated from GCP/Firebase to a self-hosted K8s cluster with bare-metal
Ollama on Mac Studios. The cluster is LIVE and Companion is DEPLOYED and
functionally wired (see Current state). OCR localization done (PaddleOCR
deployed in shadow). Remaining: pre-real-PHI security gates, Firebase
prod-auth finish, mobile builds.

**Primary reference:** [`docs/migration-plan.md`](docs/migration-plan.md)
(Phase -1 → 12). NOTE: the plan and this file's older history predate
execution — the auto-memory (`MEMORY.md` + linked notes) and `RESUME.md`
hold the live, current state and are the source of truth on resume.

**Related repos:**
- `~/repo/companion-gitops` (github.com/jtb75-org/companion-gitops) —
  Companion's own ArgoCD GitOps repo (base + overlays/onprem). Image tags
  bumped by Companion CI on each push to `main`. Modeled on `blue-gitops`.
- `~/repo/argocd-apps` (github.com/jtb75-org/argocd-apps) — cluster gitops
  source of truth; its root-app watches `applications/`, where
  `companion.yaml` registers the Companion app.
- `~/repo/authentik-gitops` — reference manifests for Authentik; ~80%
  reusable, being adapted into `argocd-apps/infra/authentik/`.

**Dev workflow:** branch → PR → merge-to-main, merge on green CI. Branch
prefixes `feature/ fix/ chore/ docs/ refactor/`. See `CONTRIBUTING.md` and
`AGENTS.md`.

## Current state (2026-07-11)

- **Cluster is LIVE**: 5-node k3s (`mini01-04` + `tower01`). Platform fully
  deployed via ArgoCD (`~/repo/argocd-apps` root-app): cnpg-operator, minio,
  sealed-secrets (controller in ns `infra`), cert-manager, longhorn, traefik,
  cloudflared, zot, openbao (+ unsealer), authentik. (The "hardware ordered /
  pre-execution" framing in this file's older history is obsolete.)
- **Companion is DEPLOYED + functionally wired** (ns `companion`, ArgoCD app
  Synced/Healthy). What's localized vs kept:
  - **DB** → CNPG `paradedb/paradedb:17` (bundles pgvector). migrate Job =
    Sync hook; paradedb needs `postgresUID/GID 999`.
  - **Storage** GCS → **MinIO** (S3); bucket `companion-documents`, scoped key.
  - **Embeddings** → **nomic-embed-text** (768-dim, no schema change) via the
    shared **LiteLLM gateway** (`llm.ng20.org` / studio-ultra `:4000`), which
    HA-balances across both Macs. Scoped virtual key.
  - **Generation KEPT on Gemini/Vertex** (`COMPANION_LLM_PROVIDER=gemini`,
    project `companion-prod-491606`, Vertex re-enabled + SA `aiplatform.user`).
    Quality/safety reason; localizing to Ollama is deferred behind the switch.
  - **Field encryption** → local **AES-256-GCM per-tenant envelope**
    (`app/services/field_crypto.py`): per-user DEK wrapped by a KEK in
    **OpenBao Transit** (`companion-kek`); fields `f2:` with user_id AAD. Profile
    phone/dob/address + RAG chunk_text encrypted. Field-level key capability +
    CI tripwire (no SSN/bank/MRN stored).
  - **Auth** → Firebase **prod** (web login works; admin = joe.buhr@gmail.com).
    **Signup is invite-only** (`complete-profile` gated). OAuth consent screen
    still needs publishing for non-test users.
  - **Workers** → all wired as internal endpoints + CronJobs (morning-checkin,
    medication-reminders, escalation-check, away-monitor, retention, ttl-purge,
    account-deletion). `/api/internal/*` blocked at the edge.
  - **Admin runtime controls (#30/#31)**:
    - OCR primary/shadow provider is configurable through `SystemConfig` and the
      admin Settings OCR dropdown, not only env vars. `_guard_ocr_flag` requires
      admin role + provider validation; ingestion `_resolve_ocr_provider` reads
      the flag before falling back to env.
    - D.D. emotional-awareness guidance is now in the **live prompt**
      (`EMOTIONAL_AWARENESS` appended in `prompt_builder.py`), implementing
      `docs/dd-assistant-guidelines.md` §3.5. The admin Prompts UI writes
      `dd_persona/system_prompt`, bounded by `_guard_persona` (admin role,
      length cap, override-phrase denylist) with safety canaries. Persona/safety
      changes require safety-privacy-reviewer sign-off.
- **Macs (inference tier):** Ollama bare-metal — `studio-max` (M4 Max, 64GB,
  192.168.0.94) + `studio-ultra` (M3 Ultra, 96GB, 192.168.0.104), `0.0.0.0:11434`.
  Models pulled (qwen2.5:14b/72b, qwen3-coder, nomic-embed-text on both).
  **LiteLLM gateway** runs on studio-ultra `:4000` (hand-edited
  `~/.config/litellm/config.yaml`, launchd). SSH as `joe`, passwordless sudo.
- **Prod DB:** currently has **1 member user** (`smoketest@mydailydignity.com`,
  active) + **1 admin** (`joe.buhr@gmail.com`). For DB ops, refer to **the CNPG
  primary** generically; instance names roll and should not be hardcoded as
  permanent.

## Next steps / remaining work

1. **Pre-real-PHI gates** (before onboarding real members): enable the OpenBao
   **audit device** (declarative — add `audit "file"` to
   `argocd-apps/applications/openbao.yaml` + restart); enable **OpenBao TLS**
   (listener is `tls_disable=1`); **vault the encryption keys** off-cluster
   (`~/companion-key-backup/` → 1Password); encrypt `source_metadata.ocr_text`;
   add an **egress NetworkPolicy** on `companion-ocr` (it pulls models from
   `paddleocr.bj.bcebos.com` on first run — bake models into the image or allow
   only that CDN + DNS).
2. **Firebase finish:** publish the OAuth consent screen; build/sign mobile
   binaries + register the Android release SHA-1.
3. **OCR rollout blocker:** PaddleOCR is DEPLOYED and running in **shadow**
   behind DocumentAI (A/B), and admin Settings can override the provider at
   runtime. Gitops still sets primary OCR to `documentai`, but DocumentAI
   primary is currently dead because no processor exists; as of 2026-07-11, a
   real user scanning a document would 404 on the primary path. Decide whether
   to create a Document OCR processor + update config, or flip primary to
   `paddleocr` after the remaining scan-robustness/shadow-eval work.
4. **CI image builds:** `build-and-push.yml` last 5 runs all succeeded in
   ~9-11 min and auto-bumps gitops image tags after pushes to `main` (last
   checked 2026-07-11).
5. Owner one-offs: revoke any bootstrap OpenBao token; key rotation automation.

## Open decisions (mostly resolved)

| # | What | Decision |
|---|---|---|
| D1 | Primary LLM | Generation kept on **Gemini/Vertex** for now (quality/safety); Ollama qwen2.5 deferred behind the provider switch |
| D2 | Embedding model | **nomic-embed-text** (768-dim, no migration) via LiteLLM — NOT bge-m3 |
| D4 | OCR engine | **PaddleOCR** — DEPLOYED, running in shadow behind DocumentAI; primary still `documentai` in gitops, but DocAI is a rollout blocker until a processor exists or primary flips |
| KMS | Field encryption | Local AES-256-GCM envelope, **KEK in OpenBao Transit** |

## Architecture reminders

- **Tiers:** Macs = bare-metal inference (Ollama on both + the LiteLLM
  gateway on studio-ultra). Minisforums = 5-node k3s cluster for everything
  else.
- **Storage:** Longhorn 3-replica with per-node anti-affinity. NAS
  demoted to bulk media + offsite backup — not in Companion's critical
  path.
- **Domains:** `ng20.org` = shared infrastructure (Authentik, MinIO,
  Argo, Grafana, Zot). `mydailydignity.com` = Companion product.
  `silkstrand.io` = separate product (not in this plan).
- **Shared infra pattern:** one deployment per service, per-tenant
  IAM/Groups, OIDC via Authentik for admin UIs. Authentik at
  `auth.ng20.org`, MinIO S3 in-cluster primary with `s3-console.ng20.org`
  for admin.

## Key project docs

- `docs/architecture.md` — existing system architecture (pre-migration baseline)
- `docs/migration-plan.md` — the migration plan (primary)
- `docs/dd-assistant-guidelines.md` — D.D. persona rules, safety layer
- `docs/caregiver-access-and-privacy.md` — three-tier caregiver model
- `docs/deployment-runbook.md`, `docs/developer-setup.md`, etc.

## Codebase layout

- `backend/` — Python 3.12 / FastAPI (use `backend/.venv` for ruff/tools)
- `companion-app/` — React Native 0.84 (iOS + Android)
- `web/` — React 18 / Vite / Tailwind
- `infrastructure/` — legacy Dockerfiles, Terraform for GCP (will be
  retired when migration completes)
