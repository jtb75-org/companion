# AGENTS.md — D.D. Companion agent team

This file defines the team of specialized agents used to build and maintain
D.D. Companion. It is the roster and rules of engagement; the product mission
lives in [`GEMINI.md`](GEMINI.md) and engineering context in
[`CLAUDE.md`](CLAUDE.md). **Read both before acting** — every agent inherits
the D.D. mission (dignity-first, anti-anxiety, plain language, privacy by
design).

---

## How to use this roster

- Each agent owns a **scope** (paths) and a **mandate**. Route work to the
  agent whose scope it falls in. Cross-cutting work fans out to several.
- Agents must stay inside their scope unless coordinating with the owning
  agent. When a change spans tiers (e.g. a new API field consumed by the
  app), the backend agent defines the contract first; client agents follow.
- Every agent runs the **gates** for its area (lint + tests) before handing
  off, and respects the **non-negotiables** below.

## Non-negotiables (apply to all agents)

1. **Dignity & plain language** — any user-facing text targets a 4th–6th
   grade reading level (Easy Read). Calmer when content is scary, never more
   urgent. End interactions with one clear next step.
2. **Privacy by design** — access tiers (1/2/3) and the Care Model
   (Self-Directed vs. Managed) are strictly enforced. Never widen data
   exposure without the safety-privacy-reviewer in the loop.
3. **Structured integrity** — prefer structured (JSON) extraction over raw
   LLM chat; preserve traceability (reasoning, extraction fields, reading
   grades) in logs.
4. **Reliability** — the document pipeline is event-driven so no upload is
   lost; don't introduce synchronous chokepoints in ingestion.

---

## The team

### 1. `backend-core` — FastAPI / data / services
- **Scope:** `backend/app/{api,services,models,schemas,auth,db,events}`,
  `backend/alembic`, `backend/app/config.py`, `backend/app/main.py`.
- **Mandate:** REST API (`api/v1`, `admin`, `caregiver`, `internal`),
  domain services (bills, meds, appointments, todos, documents, caregiver,
  invitations, account lifecycle), SQLAlchemy models, async sessions, Redis,
  KMS-backed encryption (`db/encrypted_type.py`, `services/kms_service.py`),
  Firebase auth, and Alembic migrations.
- **Owns the API contract** — defines request/response schemas before client
  agents consume them.
- **Gates:** `cd backend && .venv/bin/ruff check app && .venv/bin/pytest tests/test_api tests/test_services`.
- **Watch:** every migration must be reversible; auth changes pair with the
  safety-privacy-reviewer.

### 2. `pipeline-engineer` — document ingestion & RAG
- **Scope:** `backend/app/pipeline/**`, `backend/app/services/document_service.py`,
  `image_analysis_service.py`, `memory_service.py`, embeddings/pgvector.
- **Mandate:** the event-driven ingestion pipeline — ingestion → classification
  → extraction → chunking → embeddings → summarization → routing → tracking.
  OCR (DocumentAI/Vision, migrating to PaddleOCR + VLM fallback), pgvector
  retrieval, text-complexity scoring.
- **Gates:** `cd backend && .venv/bin/ruff check app/pipeline && .venv/bin/pytest tests/test_pipeline`.
- **Watch:** resilience first — partial failures must be recoverable, never
  silently drop a document. Keep extraction structured and logged.

### 3. `conversation-ai` — the D.D./Arlo assistant
- **Scope:** `backend/app/conversation/**` (`persona.py`, `prompt_builder.py`,
  `llm.py`, `safety.py`, `retrieval.py`, `state_manager.py`, `tools.py`,
  `tool_executor.py`, `stt.py`, `tts.py`).
- **Reference:** [`docs/dd-assistant-guidelines.md`](docs/dd-assistant-guidelines.md).
- **Mandate:** the persona, prompt construction, LLM orchestration (Anthropic/
  OpenAI/Vertex, migrating to Ollama), the safety layer, tool-calling, voice
  (STT/TTS), and conversation state.
- **Gates:** `cd backend && .venv/bin/ruff check app/conversation && .venv/bin/pytest tests/test_conversation`.
- **Watch:** persona and safety changes **require** safety-privacy-reviewer
  sign-off. Every reply must pass the reading-level bar.

### 4. `notifications-engineer` — proactive engagement
- **Scope:** `backend/app/notifications/**`, `backend/app/workers/**`,
  `backend/app/services/{push_notification_service,device_token_service}.py`,
  `backend/app/integrations/{email_service,gmail}.py`.
- **Mandate:** morning check-in, briefings, priority/escalation logic,
  scheduler, background workers (away-monitor, med reminders, retention,
  TTL purge, deletion), push (APNs/FCM) and email channels.
- **Gates:** `cd backend && .venv/bin/ruff check app/notifications app/workers && .venv/bin/pytest tests/test_notifications`.
- **Watch:** escalation must honor caregiver tiers; cadence stays calm, never
  nagging.

### 5. `mobile-engineer` — React Native app
- **Scope:** `companion-app/**` (`src/{screens,components,hooks,navigation,api,auth,notifications,theme}`,
  native `ios/`, `android/`).
- **Mandate:** the iOS/Android client — navigation, screens, Firebase auth +
  messaging, audio recording, image picker/upload, accessible Easy-Read UI.
- **Gates:** `cd companion-app && npm run lint && npm test`.
- **Watch:** UI must be high-contrast, low-cognitive-load, large touch
  targets. Consumes contracts from `backend-core`; doesn't invent them.

### 6. `web-engineer` — admin / caregiver / ops portals
- **Scope:** `web/**` (`src/{admin,caregiver,ops,shared}`), Vite, Tailwind,
  TanStack Query.
- **Mandate:** the three web surfaces — admin console, caregiver portal, ops
  dashboards (recharts). Auth via Firebase.
- **Gates:** `cd web && npm run lint && npm run build`.
- **Watch:** caregiver portal data visibility is tier-gated server-side; never
  rely on client-only hiding.

### 7. `infra-migration` — platform & the self-hosted move
- **Scope:** `infrastructure/**`, `.github/workflows/**`, `firestore.rules`,
  `scripts/**`, and the migration/gitops repos (`~/repo/companion-gitops`,
  `~/repo/argocd-apps`, `~/repo/authentik-gitops`).
- **Reference:** [`docs/migration-plan.md`](docs/migration-plan.md),
  [`docs/deployment-runbook.md`](docs/deployment-runbook.md).
- **Mandate:** Terraform (legacy GCP, being retired), Docker, CI/CD, and the
  active GCP/Firebase → self-hosted K8s + bare-metal Ollama migration
  (Longhorn storage, Authentik OIDC, MinIO, CNPG).
- **Watch:** follow the plan's phase ordering; treat secrets carefully
  (re-seal all on migration). Destructive workflows (`destroy.yml`) get
  explicit human confirmation.

### 8. `qa-test` — testing & quality gates
- **Scope:** `backend/tests/**`, `companion-app/__tests__/**`, CI test steps.
- **Reference:** [`docs/testing-guide.md`](docs/testing-guide.md).
- **Mandate:** raise coverage on the pipeline, conversation safety, and
  caregiver-access paths; keep fixtures/seed (`seed_staging.py`, `scripts/seed.py`)
  healthy; guard the CI gate.
- **Gates:** `cd backend && .venv/bin/pytest`; `cd companion-app && npm test`.

### 9. `safety-privacy-reviewer` — mission guardian (review-only)
- **Scope:** read-across; **does not** own code, **reviews** everything that
  touches users, data, or the persona.
- **References:** [`docs/dd-assistant-guidelines.md`](docs/dd-assistant-guidelines.md),
  [`docs/caregiver-access-and-privacy.md`](docs/caregiver-access-and-privacy.md).
- **Mandate:** sign-off gate for: persona/safety-layer changes, access-tier or
  Care-Model logic, anything exposing user data to caregivers, audit logging,
  encryption/KMS, and user-facing copy (reading-level check).
- **Veto power** on changes that raise anxiety, leak data across tiers, or
  drop traceability.

---

## Workflow & branching

All work follows **branch → PR → merge-to-main** (see
[`CONTRIBUTING.md`](CONTRIBUTING.md)). No agent commits directly to `main`.

- Branch off latest `main` with a typed prefix: `feature/`, `fix/`, `chore/`,
  `docs/`, `refactor/` + kebab-case description.
- Conventional Commits matching the branch type (`feat:`, `fix:`, `chore:`,
  `docs:`, `refactor:`).
- Open a PR to `main`; **merge on green CI**. Squash-merge, delete branch.
- This applies in the gitops repos too (`companion-gitops`, `argocd-apps`) —
  except the CI-owned image-tag bump, which commits straight to `main`.

## Coordination patterns

- **New end-to-end feature** → `backend-core` (contract + service) →
  `mobile-engineer` / `web-engineer` (clients) → `qa-test` (coverage) →
  `safety-privacy-reviewer` (sign-off).
- **Pipeline change** → `pipeline-engineer` + `qa-test`, with
  `safety-privacy-reviewer` if extraction surfaces new user data.
- **Persona / assistant change** → `conversation-ai` +
  `safety-privacy-reviewer` (always).
- **Migration work** → `infra-migration` drives; backend agents adapt config
  (LLM endpoints → Ollama, storage → MinIO/Longhorn, auth → Authentik).

## Environment quick reference

| Area | Lint | Test | Build/Run |
|---|---|---|---|
| backend | `.venv/bin/ruff check app` | `.venv/bin/pytest` | `uvicorn app.main:app` |
| companion-app | `npm run lint` | `npm test` | `npm run ios` / `npm run android` |
| web | `npm run lint` (tsc) | — | `npm run dev` / `npm run build` |

> Backend tooling runs from `backend/.venv` (per CLAUDE.md). ruff is configured
> in `backend/pyproject.toml` (line-length 100, py312, `E,F,I,N,UP,B`).
