# Companion (D.D.) — project notes for future sessions

## Active initiative: self-hosted migration

Migrating from GCP/Firebase to a self-hosted K8s cluster with bare-metal
Ollama on Mac Studios.

**Primary reference:** [`docs/migration-plan.md`](docs/migration-plan.md) —
~870 lines, Phase -1 through Phase 12, updated 2026-04-19.

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

## Current state (2026-04-19)

- **Hardware:** 3× Minisforum AI X1-255 barebones + 3× 64GB DDR5-5600
  SODIMM kits + 6× 1TB NVMe ordered. 5-7 day ETA. Plan is pre-execution.
- **Macs (inference tier):** Ollama running bare-metal on both:
  - `studio-max` (M4 Max, 64GB) — 192.168.0.94
  - `studio-ultra` (M3 Ultra, 96GB) — 192.168.0.104
  - LaunchDaemon at `/Library/LaunchDaemons/com.ollama.server.plist`
    (user `joe`, bound `0.0.0.0:11434`, KV cache q8_0, flash attention on)
  - SSH as user `joe` with passwordless sudo.
  - Models NOT yet pulled.
- **Old OrbStack K3s cluster** on Macs still runs existing workloads
  (traefik, cloudflared, argocd, zot, sonarr/radarr/nzbget, mail-relay).
  Will be fully decommissioned after new cluster proves out.
- **`argocd-apps`** has Phase 0 commits (`cnpg-operator.yaml`,
  `minio.yaml`, `ollama-endpoints.yaml` + their `infra/` dirs) that
  target the OLD cluster. Flagged in the plan as deprecated —
  manifests mostly port forward to the new cluster; main changes are
  `nfs-client` → `longhorn` on MinIO PVC and re-sealing all Secrets.

## Next steps on resume

1. **Decide D1 + D2** (LLM + embedding model). Recommended: Qwen 2.5 32B
   on studio-ultra, bge-m3 on studio-max. Once decided, kick off
   `ollama pull` on each Mac so models are warm before hardware arrives.
2. **Pre-hardware checklist** — back up Cloudflare Tunnel token, mail-relay
   plaintext secret, Sonarr/Radarr/nzbget configs, optionally Zot data.
   Plan §Phase -1 describes the rebuild sequence.
3. **Commit the plan revision** — `docs/migration-plan.md` is currently
   uncommitted on `main`.
4. **When hardware arrives:** execute Phase -1 → Phase 0 per plan.

## Open decisions (plan §4)

| # | What | Recommendation |
|---|---|---|
| D1 | Primary LLM | Qwen 2.5 32B Instruct |
| D2 | Embedding model | bge-m3 (1024-dim) |
| D3 | Push notifications | APNs-direct + WebSocket hybrid |
| D4 | OCR engine | PaddleOCR first, VLM fallback |
| D10 | Offsite backup destination | Backblaze B2 or similar |

All others closed — see plan §6.

## Architecture reminders

- **Tiers:** Macs = Ollama-only bare metal. Minisforums = 3-node K3s
  cluster for everything else.
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
