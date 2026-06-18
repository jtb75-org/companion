# Contributing to Companion

We use a **branch → PR → merge-to-main** workflow. `main` is protected and
always deployable; nothing lands on it except via a reviewed, green PR.

## Branch naming

Branch off the latest `main` using a typed prefix:

| Prefix | Use for |
|---|---|
| `feature/` | new user-facing or backend capability |
| `fix/` | bug fixes |
| `chore/` | tooling, deps, CI, config, non-behavioral maintenance |
| `docs/` | documentation only |
| `refactor/` | internal restructuring with no behavior change |

Use short, kebab-case descriptions: `feature/medication-reminders`,
`fix/caregiver-tier-leak`, `chore/bump-fastapi`.

## Commit messages

Conventional Commits, matching the branch type:

```
feat: add medication reminder cronjob
fix: enforce tier-2 redaction in caregiver bill view
chore: bump ruff to 0.7
docs: document gitops bootstrap
refactor: extract retrieval scoring into helper
```

Keep the subject ≤ 72 chars, imperative mood, technical change only.

## Pull requests

1. Push your branch and open a PR against `main`.
2. CI (`.github/workflows/ci.yml`) must be **green** — lint, backend tests,
   web lint, terraform validate. **Merge on green CI** is the standing rule.
3. Changes touching the persona, safety layer, access tiers, caregiver data
   exposure, encryption, or user-facing copy require **safety-privacy-reviewer**
   sign-off (see [`AGENTS.md`](AGENTS.md)).
4. Squash-merge into `main`. Delete the branch after merge.

## Deploy flow (self-hosted)

Merging to `main` triggers `build-and-push.yml`: it runs the CI gate, builds
the backend and web images with kaniko, pushes them to
`zot.lan.ng20.org/companion-{backend,web}`, then bumps the image tags in
[`companion-gitops`](https://github.com/jtb75-org/companion-gitops). ArgoCD
reconciles from there. See that repo's README for the GitOps layout and
one-time bootstrap.

> The legacy GCP/Firebase deploy workflows (`deploy-staging.yml`,
> `deploy-prod.yml`) remain until the self-hosted migration completes
> (see [`docs/migration-plan.md`](docs/migration-plan.md)).
