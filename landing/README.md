# Companion landing — public marketing site (`www.mydailydignity.com`)

A **decoupled, static** marketing landing for My Daily Dignity. It is
intentionally separate from the authed web app (`web/`): its own package, its
own Vite build, its own nginx image. It shares **no** bundle, **no** auth, and
touches **no** PHI or member data.

## What's here

- **Vite + React + TypeScript** (no Tailwind — the approved design uses a
  CSS-custom-property token system with light **and** dark themes, kept intact
  in `src/styles.css`).
- The **benefits helper** in the hero is a **MOCK** (`src/components/BenefitsHelper.tsx`):
  canned, cited Q&A + a freemium gate. No network calls.
- The integration seam for the future real widget is `src/lib/knowledgeApi.ts` —
  a typed `KnowledgeApi` contract with a `MockKnowledgeApi` today. Phase 2 swaps
  in an HTTP implementation of the same interface; components don't change. The
  wire shape of the real public endpoint must be defined by backend-core first.

## Develop

```bash
cd landing
npm install
npm run dev      # http://localhost:5174
npm run lint     # tsc --noEmit
npm run build    # -> dist/ (static, crawlable)
```

## Serve / deploy

Static `dist/` is served by nginx (`infrastructure/Dockerfile.landing` +
`infrastructure/nginx.landing.conf`), mirroring the web-app serving pattern
(single-stage, bundle built outside Docker). In-cluster manifests live in
`companion-gitops` and route `www.mydailydignity.com` via the existing
Cloudflare wildcard tunnel → Traefik. **Not** applied to the cluster yet —
pending owner sign-off.

## Accessibility + SEO

WCAG-AA contrast, full keyboard nav, visible `:focus-visible` states, a skip
link, `aria-live` on the answer region, real alt/aria labels, and
`prefers-reduced-motion` respected. SEO: real `<title>`/description, Open
Graph + Twitter tags, canonical, `robots.txt`, favicon, semantic HTML, and a
documented stub for future pre-rendered canonical Q&A pages
(`src/content/questions/`).
