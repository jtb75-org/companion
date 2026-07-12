# RESUME — Companion self-hosted migration

Session handoff. Last updated **2026-07-12**. Source of truth for live state is
this file + `MEMORY.md` (+ linked notes). `CLAUDE.md` "Current state" is also
current as of 2026-07-12. The older 2026-06-18 history below the line is kept
for context but is superseded.

## Status: DEPLOYED + functionally wired

5-node k3s cluster LIVE; ArgoCD app `companion` Synced/Healthy in ns
`companion`. Companion is no longer "stub-to-boot" — it is functionally wired:

- **DB** → CNPG `paradedb/paradedb:17` (bundles pgvector). migrate Job = Sync
  hook; paradedb needs `postgresUID/GID 999`. See [[cnpg-paradedb-uid]].
- **Storage** GCS → **MinIO** (S3); bucket `companion-documents`, scoped key.
- **Embeddings** → **nomic-embed-text** (768-dim, no schema change) via the
  shared **LiteLLM gateway** (`llm.ng20.org` / studio-ultra `:4000`), HA across
  both Macs. NOT bge-m3 (old RESUME said bge-m3 — wrong).
- **Generation KEPT on Gemini/Vertex** (`COMPANION_LLM_PROVIDER=gemini`,
  project `companion-prod-491606`). Localizing to Ollama deferred behind the
  provider switch (quality/safety). Old RESUME said LLM→Ollama — wrong.
- **Field encryption** → local **AES-256-GCM per-tenant envelope**
  (`app/services/field_crypto.py`): per-user DEK wrapped by KEK in **OpenBao
  Transit** (`companion-kek`); `f2:` ciphertext with user_id AAD.
- **Auth** → Firebase **prod** (web login works; admin = joe.buhr@gmail.com).
  Signup invite-only. OAuth consent screen still needs publishing.
- **Workers** → all wired (internal endpoints + CronJobs); `/api/internal/*`
  blocked at the edge.
- **Admin runtime controls shipped (#30/#31)**:
  - OCR primary/shadow provider is runtime-configurable via `SystemConfig` and
    the admin Settings OCR dropdown, not only env vars. `_guard_ocr_flag`
    requires admin role + valid provider, and ingestion `_resolve_ocr_provider`
    reads the flag before falling back to env.
  - D.D. emotional-awareness guidance is now in the **live prompt**
    (`EMOTIONAL_AWARENESS` appended in `prompt_builder.py`), implementing
    `docs/dd-assistant-guidelines.md` §3.5. The admin Prompts UI is wired to
    write `dd_persona/system_prompt`, bounded by `_guard_persona` (admin role,
    length cap, override-phrase denylist) with safety canaries. Persona/safety
    changes still require safety-privacy-reviewer sign-off.

## ✅ OCR migration — PaddleOCR PRIMARY (resolved 2026-07-12)

Self-hosted **PaddleOCR** is DEPLOYED and now the **primary OCR provider**.
DocumentAI is retired as primary; shadow OCR is disabled. See
[[ocr-paddleocr-shadow]].

- Image `companion-ocr` (`ocr/` in companion repo), latest tag `6f01e70`,
  ns `companion`. Pod stable, warm (~74ms inference), backend-reachable.
- Wiring: backend `app/pipeline/ocr/` provider abstraction; `_run_shadow_ocr`
  in `ingestion.py` is best-effort, records `source_metadata["ocr_shadow"]`
  (provider names, char counts, ms, similarity, + **encrypted** `shadow_text`).
  gitops PR #15 flipped `COMPANION_OCR_PROVIDER=paddleocr` and disabled shadow
  on 2026-07-12. Admin Settings can override primary/shadow provider at runtime
  via guarded `SystemConfig` feature flags.
- **Verified live end-to-end**: primary PaddleOCR is active. Earlier shadow
  testing recorded `shadow_text` encrypted (`f2:` per-user envelope) and
  decryptable via OpenBao.
- **Safety review: APPROVE-WITH-FOLLOWUPS.** Remaining real-PHI OCR follow-ups:
  egress NetworkPolicy and confidence-tier recalibration.
- PRs merged: #22/#23/#24 (build/push fixes), **#25** (targeted trixie-lib rm),
  **#26** (generic `clean_libs.py` + `asyncio.to_thread` warm-up), **#30**
  (admin OCR provider feature flag), **#31** (live emotional-awareness prompt +
  wired admin Prompts UI).

### Benchmark (2026-06-20) + resolved DocumentAI primary blocker
No real shadow records existed at benchmark time, so ran a synthetic
head-to-head: DocumentAI vs PaddleOCR on 5 D.D. doc types × clean/scan, scored
vs known ground truth, both providers driven directly from an api pod.

| metric | clean | scan (degraded) |
|---|---|---|
| DocumentAI acc | 0.963 | **0.937** |
| PaddleOCR acc | **0.998** | 0.767 |
| latency (DocAI / Paddle) | 824 / **598** ms | 720 / **397** ms |

- **Clean: parity** (DocAI's 0.963 is one columnar-bill outlier; else ~0.999).
- **Degraded scans: DocAI more robust** (+0.17); Paddle falls off under heavy
  noise/rotation/blur. (The "scan" degradation was harsh — worst-case floor.)
- **Paddle ~2× faster, local, free, no PHI egress.**
- **Cutover read:** Paddle is good enough for normal-quality captures; continue
  confidence-tier recalibration for lower-quality scans.

**Resolved 2026-07-12:** primary OCR flipped to `paddleocr`; shadow disabled;
DocumentAI retired as primary. The old DocumentAI primary path was
non-functional (found during the benchmark). Two independent breaks:
1. The pod SA `companion-backend@companion-prod-491606` lacked Document AI
   permission → **granted `roles/documentai.apiUser`** (left in place).
2. **No processor exists** — configured `documentai_processor_id=6785df08989fd9a6`
   was deleted in the GCP teardown (0 processors in us/eu). Real document scans
   would have 404'd on the old primary path.

→ DocAI is NOT a free fallback right now. To revive it later, create a Document
OCR processor + update the config's processor ID. (Benchmark used a temp
processor, since deleted — 0 remain.)

### Build/runtime gotchas (cost many cycles)
- Base `python:3.10-slim-bookworm` (glibc 2.36). The PaddleOCR pip layer
  **non-deterministically drops Debian-trixie (glibc 2.38/2.39) duplicates of
  ~30 core system libs** into /usr/lib; ldconfig repoints sonames at them →
  every native import + curl/apt/sed break with "GLIBC_2.38 not found".
  `ocr/clean_libs.py` (runs in Dockerfile) removes the trixie dups, keeps
  bookworm, reinstalls replaced pkgs.
- Models load LAZILY at runtime (build-time instantiation segfaults in kaniko).
  `server.py` offloads blocking work via `asyncio.to_thread` + warms in a
  background thread at startup — else the ~55s load blocks /health and liveness
  SIGTERMs the pod.
- Keep single-stage Dockerfiles + split big pip into separate RUN layers (2GB
  layer 502s pushing to zot). See [[kaniko-single-stage-dockerfiles]].

## Remaining work / gates

**Pre-real-PHI gates** (before onboarding real members):
1. **Egress NetworkPolicy** on `companion-ocr` — it pulls models from
   `paddleocr.bj.bcebos.com` on first run (only model dl leaves cluster, no
   PHI). Either bake models into the image or allow only that CDN + DNS.
2. OpenBao **audit device** (declarative) + **TLS** (listener is
   `tls_disable=1`); **vault the encryption keys** off-cluster
   (`~/companion-key-backup/` → 1Password, then shred).
3. Recalibrate OCR confidence tiers now that PaddleOCR is primary.

Done: `source_metadata.ocr_text` is encrypted in `process_camera_scan`
(`encrypt_for_user` in `backend/app/pipeline/ingestion.py`).

**Tracked follow-ups (non-blocking):**
- **CI image builds are green:** `build-and-push.yml` last 5 runs all succeeded
  in ~9-11 min, and the workflow auto-bumps gitops image tags after pushes to
  `main` (last checked 2026-07-11).
- Cosmetic: in-container `curl` still broken by the overlay (Docker HEALTHCHECK
  only; k8s uses httpGet) — fix libldap reinstall or drop the HEALTHCHECK.
- Hardening (safety followups): `logging_config.py` PII regex under-redacts
  multi-word values (defense-in-depth, no current leak); add admin-strip +
  retention tests for `ocr_shadow.shadow_text`.

**Other:**
- Firebase finish: publish OAuth consent screen; build/sign mobile binaries +
  register Android release SHA-1.
- Owner one-offs: revoke bootstrap OpenBao token; key rotation automation.

> NOTE: DB currently has **1 member user** (`smoketest@mydailydignity.com`,
> active) + **1 admin** (`joe.buhr@gmail.com`). See [[prod-access-model]].

## Handy context
- Macs (Ollama bare-metal): `studio-max` 192.168.0.94 (M4 Max 64GB),
  `studio-ultra` 192.168.0.104 (M3 Ultra 96GB), `0.0.0.0:11434`. LiteLLM
  gateway on studio-ultra `:4000` (launchd). Models pulled on both.
- Registry `zot.lan.ng20.org`; runner `jtb75-arc`; sealed-secrets ns `infra`.
- Database ops should refer to **the CNPG primary** generically; instance names
  roll during failover/maintenance and should not be treated as permanent.
- Repos: `~/repo/companion-gitops` (Companion's ArgoCD repo),
  `~/repo/argocd-apps` (cluster gitops source of truth).
- Plan: `docs/migration-plan.md`. `.env` (gitignored): `CF_TOKEN`,
  `GITOPS_DEPLOY_TOKEN`, `ZOT_USERNAME`, `ZOT_PASSWORD`.

---

## Superseded history (2026-06-18) — kept for context

Earlier handoff captured the initial deploy. Key facts that remain true:
networking (Cloudflare tunnel `530c92d3` / `k3s-home` routes
`*.mydailydignity.com` → traefik; stale GCP CNAMEs deleted), sealed-secrets
controller in ns `infra` (`kubeseal --controller-name sealed-secrets-controller
--controller-namespace infra`), TLS handled at Cloudflare edge (no
cert-manager solver needed for the tunnel path). Everything in that handoff
framed as "future app-side migration" (GCS→MinIO, embeddings, OCR, KMS) is now
DONE per the sections above.
