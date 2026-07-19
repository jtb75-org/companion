# Pre-rendered canonical Q&A pages — STUB / roadmap only

This directory is the seam for Phase 2 SEO: statically pre-rendered, crawlable
canonical pages for common benefits questions (e.g.
`/questions/how-long-does-an-ssdi-decision-take`). These do **not** exist yet —
this is intentionally a placeholder per the landing roadmap. Do not build them
out here.

## Intended shape (when built)

- One canonical page per curated question, each with its own `<title>`, meta
  description, `<link rel="canonical">`, and `FAQPage`/`QAPage` JSON-LD.
- Content sourced from the same public knowledge contract as the live widget
  (`src/lib/knowledgeApi.ts`) so answers and citations stay consistent.
- Genuinely static output (pre-rendered at build time, not client-fetched) so
  search engines index the answer text, not an empty shell.
- Linked from the footer "Learn" section and an eventual `/questions` index,
  and listed in `public/sitemap.xml`.

## Why a stub now

The real public knowledge endpoint does not exist yet (the live widget is a
mock). Pre-rendering canned marketing answers as canonical URLs before the real
data source is wired would create SEO pages we'd have to change or retract.
Ship the seam; fill it when the endpoint and curated question set are approved.
