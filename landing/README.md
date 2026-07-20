# Companion landing — public marketing site (`www.mydailydignity.com`)

A **decoupled, static** marketing landing for My Daily Dignity. It is
intentionally separate from the authed web app (`web/`): its own package, its
own Vite build, its own nginx image. It shares **no** bundle, **no** auth, and
touches **no** PHI or member data.

## What's here

- **Vite + React + TypeScript** (no Tailwind — the approved design uses a
  CSS-custom-property token system with light **and** dark themes, kept intact
  in `src/styles.css`).
- The page is **companion-first**: the hero is the D.D. companion app (with a
  pure-CSS phone mockup standing in for a real screenshot). The **benefits
  helper** lives lower down as a **free resource** section
  (`src/components/BenefitsResource.tsx` → `src/components/BenefitsHelper.tsx`).
- `src/lib/knowledgeApi.ts` holds a typed `KnowledgeApi` contract with **two**
  implementations: a real `HttpKnowledgeApi` (calls `POST /public/knowledge/ask`)
  and a `MockKnowledgeApi` (canned, cited, disclaimered answers + a freemium
  gate, no network). Server answer text is always rendered as **plain text** —
  never `dangerouslySetInnerHTML`.
- **Client selection is FAIL-SAFE: the mock is the default everywhere, including
  production builds.** The real HTTP client is used only on an explicit opt-in —
  `VITE_KNOWLEDGE_API_BASE` (a base URL, may be cross-origin) or
  `VITE_KNOWLEDGE_ENABLE_HTTP=true` (same-origin, the launch switch). This is
  deliberate: the public endpoint is still launch-gated (backend #151 not merged,
  no edge protection yet), so a no-env production build must serve the mock, not
  hit a live/broken endpoint. Flip the enable flag in the deploy env once the
  endpoint + Cloudflare edge protection are live — no code change needed.
  (`VITE_KNOWLEDGE_USE_MOCK=true` remains a redundant explicit force-mock.)

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
