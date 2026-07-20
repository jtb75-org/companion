import { BenefitsHelper } from './BenefitsHelper';

/**
 * The benefits helper as a FREE RESOURCE — a section lower down the page, no
 * longer the hero. Framing: untangling Social Security & disability benefits is
 * one of the hardest parts of living independently, so here's a free helper.
 *
 * The interactive widget is the wired `BenefitsHelper` (real
 * `POST /public/knowledge/ask` client via `knowledgeApi`, XSS-safe plain-text
 * rendering, honest free-limit / sign-up gate). The Http/Mock choice stays in
 * `knowledgeApi` behind the env flag (VITE_KNOWLEDGE_USE_MOCK /
 * VITE_KNOWLEDGE_API_BASE) — this section does not reintroduce a hardcoded mock.
 */
export function BenefitsResource() {
  return (
    <section className="resource" id="resource">
      <div className="wrap res-grid">
        <div className="res-copy reveal">
          <span className="badge">Free resource</span>
          <h2>Untangling Social Security &amp; disability benefits.</h2>
          <p>
            Sorting out benefits is one of the most confusing parts of living independently. So we
            built a free helper for it — ask a plain-language question and get a clear answer, with
            the official rule cited. No account needed to try it.
          </p>
        </div>
        <BenefitsHelper />
      </div>
    </section>
  );
}
