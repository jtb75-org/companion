# RESUME — Companion self-hosted migration

Session handoff. Last updated **2026-07-21**. Source of truth for live state is
this file + `MEMORY.md` (+ linked notes). `CLAUDE.md` "Current state" is also
current as of 2026-07-12. The older 2026-06-18 history below the line is kept
for context but is superseded.

> The migration is DONE and stable; work since ~2026-07-13 has moved to
> PRODUCT features. RESUME.md only summarizes the biggest landings — the fuller,
> current breadth (Authentik auth cutover, self-service password reset, the
> pentest, account-deletion, doc dedup, caregiver gates, etc.) lives in
> `MEMORY.md` + its linked notes.

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

## Where we landed (2026-07-21 session)

### 🚀 Disability-benefits RAG — LAUNCHED + fully operationalized, on self-hosted Qwen
A NEW public, caregiver/claimant-facing knowledge assistant over federal SSA/SSDI
disability regulations — SEPARATE from the per-member PHI RAG (own
`disability_reg_chunks` table, no user_id/RLS/encryption, never co-queried). Full
detail in [[disability-rag-module]] + [[product-positioning-landing]]. State:

- **LIVE at `www.mydailydignity.com`** (companion-first landing; the helper is a
  "free resource"). Public `POST /public/knowledge/ask`, anon cookie + Redis
  quota (3/24h, fail-closed). Cloudflare rate-limit + Bot Fight Mode on;
  **Turnstile still TODO before broad advertising.**
- **Hybrid retrieval** (BM25 pg_search + vector, RRF). NOTE a real bug fixed this
  session: the BM25 leg had been silently degrading to vector-only in prod
  (unqualified `::searchqueryinput[]` cast; `paradedb` not on the app role's
  search_path) — fixed by schema-qualifying the cast (#169).
- **Ingestion worker OPERATIONALIZED** ([[disability-rag-module]]): eCFR
  reconciled (1641 tracked chunks) + Federal Register loaded + **Blue Book /
  Listings of Impairments** ingested (243 listings, #166); scheduled via K8s
  CronJobs (gitops #27) with an egress NetworkPolicy (gitops #28→30 — see the
  kube-router POST-DNAT gotcha in [[k3s-kube-router-egress-postdnat]]).
- **Generation moved to self-hosted qwen2.5:14b** for THIS public surface (env
  flag `COMPANION_KNOWLEDGE_LLM_PROVIDER=qwen`, #168 + gitops #31), HA across both
  Macs via the LiteLLM gateway — **$0 tokens**, eval-comparable to Gemini. The
  member PHI D.D. assistant STAYS on Gemini/Vertex (separate `COMPANION_LLM_PROVIDER`).
  Gateway needed the companion virtual key broadened to the qwen model (LiteLLM
  `/key/update`); qwen HA entry added to `~/.config/litellm/config.yaml` on
  studio-ultra. Phase 1b (POMS/HALLEX crawlers) + reranker still to come.

### 🩹 D.D. chat robustness (member assistant)
- **Truncation fixed (#167):** the public helper served mid-sentence answers —
  Gemini is a THINKING model so reasoning tokens ate the 800-token budget
  (`MAX_TOKENS`). Bumped to 3072 + guarded `finish_reason` (SAFETY/RECITATION/
  MAX_TOKENS) on the non-stream path; also fixed over-refusal + added plain-
  language prompt guidance.
- **finish_reason guards everywhere (#170):** `generate_stream` + `generate_with_tools`
  now guard blocked/truncated cuts too (shared `_BLOCKED_FINISH_REASONS`).
- **§8.5 fallback fix (#171):** all client `_fallback_response`s consolidated to a
  single input-free `LLM_FALLBACK_MESSAGE` (no more echoing member input); the
  reg-helper detects it via the shared constant to swap in the grounded refusal.
- **"Response stopped early" note (cut-short):** when a member answer is cut, the
  backend returns `cut_short`/`cut_reason` (coarse `content`/`length`, never the
  raw finish_reason) — SSE path #172, and the **non-streaming `/message` path the
  app actually uses** #174; mobile renders a soft note #173 (`CutShortNote`,
  safety-approved copy). `COMPANION_CHAT_MAX_TOKENS` is now an env knob
  (default 2048, #175). A temporary low-override used to demo the note on the
  iOS simulator was REVERTED (gitops #33) — prod chat is back to normal.

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

**Pre-real-PHI gates — CLOSED in the 2026-07-19 sweep:**
1. ✅ **Egress NetworkPolicy** on `companion-ocr` — gitops #24 (default-deny
   egress, allow only DNS + the model CDN; deployed). Follow-up: bake models into
   the image so egress can be denied entirely.
2. ✅ **OpenBao audit device** — already live (the docs listing it as remaining
   were stale; `audit.log` actively appending, ~1.6yr PVC runway). Alert PR
   argocd-apps #69 pending owner merge; rotation sidecar deferred (maintenance
   window — `selfHeal:true` auto-applies podSpec changes → unseal).
3. ✅ **`content_fingerprint` → per-user HMAC** — #140 (HKDF subkey off the member
   DEK, domain-separated; removes cross-member correlation, keeps same-user dedup).
4. ✅ **Chat transcript encryption** — #141 (`chat_messages.content` now `f2:`
   per-user envelope, parity with RAG/OCR/extracted-fields).
5. ✅ **`rls_guc_guard=on` in prod** — gitops #23 (the no-tenant-context tripwire
   was disabled in prod under `auto`).
6. ✅ **Conversation persistence** — #139 (transcripts were silently dropping every
   turn after the greeting; transaction-local RLS GUC cleared by a mid-request
   commit). See [[conversation-transcript-persistence]].
7. ✅ **RLS-safe migration helper** — #144 (`app/db/rls_migration.py`); migrations
   doing DML on the 18 FORCE-RLS tenant tables were silently affecting 0 rows +
   recording as applied. Audit found only 044/045/039-downgrade affected, all
   fixed. See [[migrations-silent-noop-force-rls]].
8. ◻ **OCR confidence recalibration** — instrumentation + a conservative review
   floor shipped (#142); the actual close-out (real-data threshold tuning) stays
   OPEN until telemetry accumulates post-onboarding.
9. ◻ **OpenBao TLS** — Phase A (inert CA-trust prerequisite) shipped (#143); later
   phases (cert-manager server cert → dual-listener cutover → addr flip) DEFERRED,
   each needs its own safety review + owner per-phase go. Blast radius: a bad flip
   breaks all field-crypto (but not the auto-unseal — revertible). See
   [[argocd-partial-sync-skips-hooks]] for the deploy caveats.

**Still open pre-PHI:** OpenBao TLS later phases (#9); OCR real-data tuning (#8);
**vault the encryption keys** off-cluster (`~/companion-key-backup/` → 1Password,
then shred — OWNER); §6 emotional-disclosure-persistence reconciliation + a
`chat_messages` retention TTL (deferred owner decision — see
[[conversation-transcript-persistence]]).

Done earlier: `source_metadata.ocr_text` is encrypted in `process_camera_scan`
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
- **Firebase finish:**
  - **FCM iOS push — APNs key missing (owner action, root-caused 2026-07-13).**
    Push delivery to iOS fails with `401 THIRD_PARTY_AUTH_ERROR` ("missing
    required authentication credential"). Root cause: Firebase project
    `companion-prod-491606` has **no APNs auth credential**, so FCM can't auth to
    Apple's APNs. NOT a code bug — proved the backend SA mints a valid OAuth
    token (dummy token → 400 INVALID_ARGUMENT = auth OK; real iOS token → 401
    THIRD_PARTY_AUTH_ERROR), and the mobile `GoogleService-Info.plist` /
    `google-services.json` both = `companion-prod-491606` (no project mismatch).
    **Fix:** Apple Developer → Keys → create an **APNs Auth Key (.p8)** (Apple
    Push Notifications service) → Firebase Console → Project Settings → Cloud
    Messaging → Apple app (`com.mydailydignity.companion`) → upload `.p8` + Key ID
    + Team ID. Caveat: iOS **Simulator** push over real APNs is unreliable — test
    on a physical device. Detail in [[document-pipeline-prod-gaps]].
  - Publish OAuth consent screen; build/sign mobile binaries + register the
    Android release SHA-1.
- Owner one-offs: **vault `~/companion-key-backup/` → 1Password + shred**; revoke
  bootstrap OpenBao token; merge argocd-apps #69 (audit-log alert); key rotation
  automation.

> NOTE: prod now has **seeded test members** beyond the original
> `smoketest@mydailydignity.com` — e.g. `alex@ng20.org`, populated with bills /
> medications / appointments (used for the D.D. chat demo this session) — plus
> the admin `joe.buhr@gmail.com`. Still a small, non-real-PHI test set. See
> [[prod-access-model]] (verify counts against the live DB before relying on them).

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
