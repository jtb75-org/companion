/**
 * Public benefits-helper knowledge API — CONTRACT + MOCK.
 *
 * This is the clean integration seam for Phase 2. Today the benefits helper on
 * the landing page is a MOCK: canned Q&A, no network, no auth, no PHI. When the
 * real public knowledge endpoint exists, implement `KnowledgeApi` with an HTTP
 * client (e.g. `HttpKnowledgeApi` hitting the public read-only endpoint) and
 * swap the export at the bottom — no component changes required.
 *
 * Invariants the real implementation MUST preserve:
 *  - This surface is PUBLIC and unauthenticated. It must never accept, echo, or
 *    return member data / PHI. It answers general questions about public
 *    federal regulations only.
 *  - Every answer carries citations and a "not legal advice" disclaimer.
 *  - The contract shape below is owned here for the mock, but a real endpoint
 *    shape MUST be defined by backend-core first — do not invent the wire
 *    format here. Adapt the response into these types in the HTTP client.
 */

export interface Citation {
  /** Human-readable label, e.g. "20 CFR § 404.1520". */
  label: string;
  /** Optional canonical URL to the cited regulation/section. */
  url?: string;
}

export interface BenefitsAnswer {
  /** The question this answer responds to (as asked). */
  question: string;
  /**
   * Answer body as an ordered list of paragraphs. Paragraphs may contain a
   * small, fixed set of inline emphasis tags (<b>) in the mock. A real
   * implementation MUST return sanitized/trusted content or plain text — the
   * renderer treats these as trusted, so untrusted HTML must never flow here.
   */
  paragraphs: string[];
  /** Sources backing the answer. Never empty for a real answer. */
  citations: Citation[];
  /** Human phrase describing recency, e.g. "Reflecting current federal rules". */
  asOf: string;
  /** The "not legal advice" disclaimer shown with every answer. */
  disclaimer: string;
}

export interface AskQuery {
  question: string;
}

export interface KnowledgeApi {
  /** Returns the sample/suggested questions shown as chips. */
  suggestions(): Promise<string[]>;
  /**
   * Answers a question. In the mock, only the canned sample questions resolve
   * to a cited answer; anything else resolves to `null` (the UI then invites
   * the visitor to create an account to ask their own questions).
   */
  ask(query: AskQuery): Promise<BenefitsAnswer | null>;
}

const DISCLAIMER =
  'General information to help you understand the process — not legal advice. Verify specifics with the SSA.';

const AS_OF = 'Reflecting current federal rules';

/**
 * Canned content for the mock. Kept deliberately factual and plain-language.
 * Copy here is public-facing and was reviewed alongside the page copy.
 */
const CANNED: BenefitsAnswer[] = [
  {
    question: 'How long does an SSDI decision take?',
    paragraphs: [
      'Most first-time SSDI decisions take about <b>6 to 8 months</b>. Your application goes to a state office called Disability Determination Services, which gathers your medical records and decides whether your condition meets Social Security’s rules.',
      'If it’s denied, you don’t start over — you ask them to look again, and can request a hearing after that.',
    ],
    citations: [{ label: '20 CFR § 404.1503' }, { label: 'POMS DI 11010.001' }],
    asOf: AS_OF,
    disclaimer: DISCLAIMER,
  },
  {
    question: 'What is the “five-step” evaluation?',
    paragraphs: [
      'Social Security decides disability with a fixed <b>five-step sequence</b>: (1) Are you working above a set earnings level? (2) Is your condition “severe”? (3) Does it meet a listed impairment? (4) Can you do your past work? (5) Can you adjust to other work?',
      'You’re found disabled as soon as a step settles it — the review stops there.',
    ],
    citations: [{ label: '20 CFR § 404.1520' }],
    asOf: AS_OF,
    disclaimer: DISCLAIMER,
  },
  {
    question: 'What if my claim is denied?',
    paragraphs: [
      'A denial is not the end — most claims are appealed. There are four levels, in order: <b>reconsideration</b>, a <b>hearing</b> before an administrative law judge, the <b>Appeals Council</b>, and finally federal court.',
      'Each step has its own deadline (usually 60 days), so the date on your denial letter matters.',
    ],
    citations: [{ label: '20 CFR § 404.900' }, { label: 'HALLEX I-2-0-1' }],
    asOf: AS_OF,
    disclaimer: DISCLAIMER,
  },
];

class MockKnowledgeApi implements KnowledgeApi {
  async suggestions(): Promise<string[]> {
    return CANNED.map((c) => c.question);
  }

  async ask({ question }: AskQuery): Promise<BenefitsAnswer | null> {
    const normalized = question.trim().toLowerCase();
    const match = CANNED.find((c) => c.question.toLowerCase() === normalized);
    return match ?? null;
  }
}

/**
 * The active client. Phase 2: replace with `new HttpKnowledgeApi(...)`.
 * Components import only the `KnowledgeApi` interface + this instance, so the
 * swap is isolated to this file.
 */
export const knowledgeApi: KnowledgeApi = new MockKnowledgeApi();
