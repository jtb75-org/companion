/**
 * Turn a citation label (as produced by the backend, e.g. "20 CFR § 404.1520"
 * or "20 CFR Pt. 404, Subpt. P, App. 1, § 12.04") into a human-readable eCFR
 * page URL, so a reader can verify the source on ecfr.gov.
 *
 * The backend stores a `source_url`, but it's the eCFR *API-renderer* endpoint at
 * *part* level — not something to send a person to. The citation labels are
 * regularly formatted, so we derive the real public page URL from the label here.
 *
 * Verified live: `.../current/title-20/section-404.1520` and
 * `.../current/title-20/part-404/appendix-<name>` both resolve (real → 200,
 * bogus → 404). Returns null when the label doesn't match a known shape (the UI
 * then renders a plain, non-linked chip).
 */
export function citationToEcfrUrl(label: string): string | null {
  const base = 'https://www.ecfr.gov/current';

  // Appendix listing, e.g. "20 CFR Pt. 404, Subpt. P, App. 1, § 12.04" — the
  // Blue Book / Listings live in an appendix; link to the appendix page (eCFR
  // has no per-listing deep link). Match the appendix shape BEFORE the section
  // regex (this label also contains a "§").
  const app = label.match(
    /(\d+)\s*CFR\s*Pt\.\s*(\d+),\s*Subpt\.\s*([A-Za-z]+),\s*App\.\s*(\d+)/i,
  );
  if (app) {
    const [, title, part, subpart, appNum] = app;
    const name = `Appendix ${appNum} to Subpart ${subpart.toUpperCase()} of Part ${part}`;
    return `${base}/title-${title}/part-${part}/appendix-${encodeURIComponent(name)}`;
  }

  // Section, e.g. "20 CFR § 404.1520". Capture the section id (digits.digits with
  // an optional letter suffix like 404.1520a); a trailing subsection such as
  // "(a)(4)" naturally falls outside the match, so we link at section level.
  const sec = label.match(/(\d+)\s*CFR\s*§\s*(\d+\.\d+[A-Za-z]?)/);
  if (sec) {
    const [, title, section] = sec;
    return `${base}/title-${title}/section-${section}`;
  }

  return null;
}
