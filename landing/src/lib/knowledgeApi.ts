/**
 * Public benefits-helper knowledge API — CONTRACT + HTTP CLIENT + MOCK.
 *
 * Phase 2: the benefits helper now asks the REAL public knowledge endpoint
 * (`POST /public/knowledge/ask`, unauthenticated, no PHI) so a visitor can type
 * their OWN question and get a cited answer grounded in public federal
 * regulations. The endpoint contract is owned by backend-core (PR #151) and is
 * treated as fixed here; this file adapts that wire shape into the view types the
 * widget renders.
 *
 * Two implementations share the `KnowledgeApi` interface:
 *  - `HttpKnowledgeApi` — the real client (default in production / when an API
 *    base is configured).
 *  - `MockKnowledgeApi` — canned, no-network answers for local/offline dev and
 *    tests. Selected when no API base is configured (see the export at the
 *    bottom).
 *
 * Invariants preserved from Phase 1:
 *  - This surface is PUBLIC and unauthenticated. It never accepts, echoes, or
 *    returns member data / PHI. It answers general questions about public
 *    federal regulations only.
 *  - Every answer carries citations and a "not legal advice" disclaimer.
 *  - Server-returned `answer` text is RUNTIME data and MUST be rendered as plain
 *    text by the component — never via dangerouslySetInnerHTML.
 */

export interface BenefitsAnswer {
  /** The question this answer responds to (as asked). */
  question: string;
  /**
   * Answer body split into paragraphs for layout. Each entry is PLAIN TEXT and
   * MUST be rendered as a text node — the server returns runtime, untrusted-shape
   * content, so no HTML is ever interpreted from here.
   */
  paragraphs: string[];
  /**
   * Citation labels backing the answer, e.g. "20 CFR § 404.1520". Plain-text
   * labels, rendered as text. Empty for a non-grounded (refusal) answer.
   */
  citations: string[];
  /** Server-computed provenance / as-of line, e.g. "Reflecting current federal rules". */
  provenance: string;
  /** The "not legal advice" disclaimer shown with every answer. */
  disclaimer: string;
  /** False when no regulation chunk cleared retrieval (no citation / refusal). */
  grounded: boolean;
}

/**
 * The result of an `ask`. Carries the anonymous free-question quota state so the
 * widget can surface "N free questions left" and render the sign-up gate warmly.
 */
export interface AskResult {
  /**
   * The cited answer, or `null` when gated (free allowance exhausted) — the UI
   * then renders the sign-up call-to-action instead of a fabricated answer.
   */
  answer: BenefitsAnswer | null;
  /** Free questions left for this anonymous session AFTER this call. */
  questionsRemaining: number;
  /** True when the free allowance is exhausted; `answer` is null and `gateMessage` is set. */
  gated: boolean;
  /** Warm sign-up invitation shown when `gated` is true (server-provided). */
  gateMessage?: string;
}

export interface AskQuery {
  question: string;
  /** Optional program filter passed straight through to the endpoint. */
  program?: 'SSDI' | 'SSI' | 'Both';
}

export interface KnowledgeApi {
  /** Returns the sample/suggested questions shown as chips. */
  suggestions(): Promise<string[]>;
  /**
   * Answers a question against the public regulation corpus. Each real call
   * spends one anonymous free question SERVER-SIDE; the returned
   * `questionsRemaining` / `gated` reflect the authoritative server count.
   */
  ask(query: AskQuery): Promise<AskResult>;
}

/** Sample questions shown as chips. Client-side constants (there is no
 *  suggestions endpoint) — purely a UI affordance, not answers. */
const SUGGESTED_QUESTIONS: string[] = [
  'What is the five-step evaluation?',
  'What counts as substantial gainful activity?',
  'What is a continuing disability review?',
];

/** Free-question allowance. Mirrors the server default; the server is
 *  authoritative and the widget always trusts its `questionsRemaining`. */
export const FREE_QUESTIONS = 3;

/**
 * Split a server plain-text answer into paragraphs on blank lines (falling back
 * to single newlines), trimming empties. Pure text splitting — no HTML.
 */
function toParagraphs(answer: string): string[] {
  const byBlank = answer
    .split(/\n\s*\n/)
    .map((p) => p.trim())
    .filter(Boolean);
  if (byBlank.length > 0) return byBlank;
  const single = answer.trim();
  return single ? [single] : [];
}

// ── Wire types (mirror backend PR #151 PublicKnowledgeAskResponse) ─────────────

interface PublicAskResponseWire {
  answer: string;
  provenance: string;
  disclaimer: string;
  citations: string[];
  grounded: boolean;
  // `sources` is returned by the endpoint but intentionally not surfaced by the
  // widget; citations (plain labels) are what we render.
  sources?: unknown[];
  questions_remaining: number;
  gated: boolean;
}

/**
 * Real client for `POST /public/knowledge/ask`.
 *
 * - `baseUrl` is same-origin by default (empty string → a relative `/public/...`
 *   fetch). An absolute base (e.g. a cross-origin API host) may be supplied via
 *   config.
 * - `credentials: 'include'` so the httpOnly `dd_anon_kb` anonymous-quota cookie
 *   round-trips (works same-origin and, when the server allows it, cross-origin).
 */
export class HttpKnowledgeApi implements KnowledgeApi {
  private readonly endpoint: string;

  constructor(baseUrl = '') {
    const base = baseUrl.replace(/\/+$/, '');
    this.endpoint = `${base}/public/knowledge/ask`;
  }

  async suggestions(): Promise<string[]> {
    // No suggestions endpoint in the contract — the chips are a fixed UI
    // affordance, not server data.
    return SUGGESTED_QUESTIONS;
  }

  async ask({ question, program }: AskQuery): Promise<AskResult> {
    const body: { question: string; program?: string } = { question };
    if (program) body.program = program;

    const res = await fetch(this.endpoint, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });

    if (!res.ok) {
      // Surface a typed error the component turns into a calm retry message.
      // Never leak status text or a body into the UI.
      throw new Error(`knowledge_ask_failed:${res.status}`);
    }

    const data = (await res.json()) as PublicAskResponseWire;

    if (data.gated) {
      return {
        answer: null,
        questionsRemaining: Math.max(0, data.questions_remaining ?? 0),
        gated: true,
        gateMessage: data.answer,
      };
    }

    return {
      answer: {
        question,
        paragraphs: toParagraphs(data.answer ?? ''),
        citations: Array.isArray(data.citations) ? data.citations : [],
        provenance: data.provenance ?? '',
        disclaimer: data.disclaimer ?? '',
        grounded: Boolean(data.grounded),
      },
      questionsRemaining: Math.max(0, data.questions_remaining ?? 0),
      gated: false,
    };
  }
}

// ── Mock (local/offline dev + tests) ───────────────────────────────────────────

const MOCK_DISCLAIMER =
  'General information to help you understand the process — not legal advice. Verify specifics with the SSA.';

const MOCK_PROVENANCE = 'Reflecting current federal rules';

const MOCK_GATE_MESSAGE =
  "You've used your free disability-benefits questions. Create a free account to keep asking and to save your answers.";

/** Canned content for the mock. Plain-language, plain-text (NO markup). */
const CANNED: Record<string, { paragraphs: string[]; citations: string[] }> = {
  'how long does an ssdi decision take?': {
    paragraphs: [
      'Most first-time SSDI decisions take about 6 to 8 months. Your application goes to a state office called Disability Determination Services, which gathers your medical records and decides whether your condition meets Social Security’s rules.',
      'If it’s denied, you don’t start over — you ask them to look again, and can request a hearing after that.',
    ],
    citations: ['20 CFR § 404.1503'],
  },
  'what is the “five-step” evaluation?': {
    paragraphs: [
      'Social Security decides disability with a fixed five-step sequence: (1) Are you working above a set earnings level? (2) Is your condition “severe”? (3) Does it meet a listed impairment? (4) Can you do your past work? (5) Can you adjust to other work?',
      'You’re found disabled as soon as a step settles it — the review stops there.',
    ],
    citations: ['20 CFR § 404.1520'],
  },
  'what if my claim is denied?': {
    paragraphs: [
      'A denial is not the end — most claims are appealed. There are four levels, in order: reconsideration, a hearing before an administrative law judge, the Appeals Council, and finally federal court.',
      'Each step has its own deadline (usually 60 days), so the date on your denial letter matters.',
    ],
    citations: ['20 CFR § 404.900'],
  },
};

/**
 * Offline stand-in for the real endpoint. Tracks an in-memory free-question
 * count so the gate + "N free left" flow can be exercised in dev without a
 * backend. Answers the canned samples verbatim and gives a clearly-labeled
 * generic reply for free text.
 */
class MockKnowledgeApi implements KnowledgeApi {
  private used = 0;

  async suggestions(): Promise<string[]> {
    return SUGGESTED_QUESTIONS;
  }

  async ask({ question }: AskQuery): Promise<AskResult> {
    if (this.used >= FREE_QUESTIONS) {
      return { answer: null, questionsRemaining: 0, gated: true, gateMessage: MOCK_GATE_MESSAGE };
    }
    this.used += 1;
    const remaining = Math.max(0, FREE_QUESTIONS - this.used);
    const canned = CANNED[question.trim().toLowerCase()];
    const answer: BenefitsAnswer = canned
      ? {
          question,
          paragraphs: canned.paragraphs,
          citations: canned.citations,
          provenance: MOCK_PROVENANCE,
          disclaimer: MOCK_DISCLAIMER,
          grounded: true,
        }
      : {
          question,
          paragraphs: [
            'This is a local preview answer (the mock client). Connect the public knowledge endpoint to get a real cited answer to your own question.',
          ],
          citations: ['20 CFR Part 404'],
          provenance: MOCK_PROVENANCE,
          disclaimer: MOCK_DISCLAIMER,
          grounded: true,
        };
    return { answer, questionsRemaining: remaining, gated: false };
  }
}

// ── Client selection ───────────────────────────────────────────────────────────
//
// FAIL-SAFE by design: the MOCK is the default EVERYWHERE, INCLUDING production.
// The real HTTP client is used ONLY on an explicit, affirmative opt-in. This is
// deliberate: the public `/public/knowledge/ask` endpoint is still launch-gated
// (backend contract PR #151 not merged; no Cloudflare edge protection yet), so a
// normal production build must NOT hit it. The deployed landing therefore serves
// the mock (canned, cited, disclaimered answers) until someone deliberately flips
// the launch switch in the build/deploy env — the moment #151 + edge protection
// are live.
//
// Selection order:
//   1. VITE_KNOWLEDGE_USE_MOCK === 'true'  → Mock (redundant explicit force-mock).
//   2. VITE_KNOWLEDGE_API_BASE non-empty   → Http(base)  (explicit base opt-in;
//                                            may be cross-origin).
//   3. VITE_KNOWLEDGE_ENABLE_HTTP === 'true' → Http('')  (explicit same-origin
//                                            opt-in — THE launch switch).
//   4. anything else (incl. PROD with no env) → Mock.
//
// There is intentionally NO "PROD implies Http" branch: production alone never
// enables the live endpoint.

function selectClient(): KnowledgeApi {
  const env = import.meta.env;
  const base = env.VITE_KNOWLEDGE_API_BASE?.trim();

  if (env.VITE_KNOWLEDGE_USE_MOCK === 'true') {
    // Explicit force-mock, anywhere.
    return new MockKnowledgeApi();
  }
  if (base) {
    // Explicit base opt-in (may be cross-origin).
    return new HttpKnowledgeApi(base);
  }
  if (env.VITE_KNOWLEDGE_ENABLE_HTTP === 'true') {
    // Explicit same-origin opt-in — the launch switch, flipped once the public
    // endpoint + edge protection are live.
    return new HttpKnowledgeApi('');
  }
  // Default EVERYWHERE (including production builds with no env set): offline,
  // fail-safe mock. Never silently reaches a launch-gated live endpoint.
  return new MockKnowledgeApi();
}

/**
 * The active client. Components import only the `KnowledgeApi` interface + this
 * instance, so the Http/Mock choice is isolated to this file.
 */
export const knowledgeApi: KnowledgeApi = selectClient();
