import { useEffect, useRef, useState } from 'react';
import {
  knowledgeApi,
  FREE_QUESTIONS,
  type BenefitsAnswer,
} from '../lib/knowledgeApi';
import { CREATE_ACCOUNT_URL } from '../lib/config';
import { Arrow, CiteIcon } from './icons';

/** Client-side cap mirroring the endpoint's request bound (a courtesy limit;
 *  the server is authoritative). */
const MAX_QUESTION_CHARS = 1000;

/**
 * The benefits-helper widget (Phase 2 — wired to the real public knowledge
 * endpoint via `knowledgeApi`).
 *
 * A visitor can type their OWN disability-benefits question and get a cited
 * answer grounded in public federal regulations. The endpoint meters a small
 * number of free questions per anonymous session (server-side, via an httpOnly
 * cookie) and then GATES with a sign-up invitation — we render that gate, never
 * a fabricated answer.
 *
 * Rendering rule: `answer` text, citations, provenance, and the disclaimer are
 * RUNTIME server data and are rendered as PLAIN TEXT nodes only. No
 * dangerouslySetInnerHTML on server content — ever.
 */
export function BenefitsHelper() {
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [selected, setSelected] = useState<number>(-1);
  const [answer, setAnswer] = useState<BenefitsAnswer | null>(null);
  const [remaining, setRemaining] = useState<number>(FREE_QUESTIONS);
  const [gated, setGated] = useState<boolean>(false);
  const [gateMessage, setGateMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [errored, setErrored] = useState<boolean>(false);
  const [asked, setAsked] = useState<boolean>(false);
  const [fading, setFading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // Load the suggestion chips on mount. We intentionally do NOT ask a question
  // here — every real ask spends a server-side free question, so the first one
  // must be the visitor's own action.
  useEffect(() => {
    let live = true;
    (async () => {
      const s = await knowledgeApi.suggestions();
      if (!live) return;
      setSuggestions(s);
    })();
    return () => {
      live = false;
    };
  }, []);

  async function ask(question: string, index: number) {
    const trimmed = question.trim();
    if (!trimmed || loading || gated) return;
    setSelected(index);
    setErrored(false);
    setLoading(true);
    setFading(true);
    try {
      const result = await knowledgeApi.ask({ question: trimmed });
      setAsked(true);
      setRemaining(result.questionsRemaining);
      if (result.gated) {
        setGated(true);
        setGateMessage(result.gateMessage ?? null);
        setAnswer(null);
      } else {
        setAnswer(result.answer);
      }
    } catch {
      // Calm, non-alarming retry message — never a stack trace, never a
      // fabricated answer. Keep any prior answer visible beneath the notice.
      setErrored(true);
    } finally {
      setLoading(false);
      setFading(false);
    }
  }

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const question = inputRef.current?.value.trim() ?? '';
    if (!question) return;
    const idx = suggestions.findIndex((s) => s.toLowerCase() === question.toLowerCase());
    void ask(question, idx);
  }

  function onChip(question: string, index: number) {
    if (inputRef.current) inputRef.current.value = question;
    void ask(question, index);
  }

  const showInitialHint = !asked && !answer && !loading && !errored;

  return (
    <div className="tool reveal d2">
      <div className="tool-head">
        <span className="dot" aria-hidden="true" />
        <b>Benefits helper</b>
        <span className="free">{FREE_QUESTIONS} free questions</span>
      </div>
      <div className="tool-body">
        <form className="ask" onSubmit={onSubmit}>
          <input
            ref={inputRef}
            id="q"
            type="text"
            maxLength={MAX_QUESTION_CHARS}
            placeholder="Ask your own question about SSDI or SSI…"
            aria-label="Ask your own question about SSDI, SSI, or Social Security disability"
            disabled={gated}
          />
          <button className="go" type="submit" aria-label="Ask" disabled={loading || gated}>
            <Arrow width={20} height={20} aria-hidden="true" />
          </button>
        </form>

        <div className="chips" role="list" aria-label="Example questions">
          {suggestions.map((s, i) => (
            <button
              key={s}
              className="chip"
              type="button"
              role="listitem"
              aria-pressed={selected === i}
              disabled={loading || gated}
              onClick={() => onChip(s, i)}
            >
              {s}
            </button>
          ))}
        </div>

        <div
          className="answer"
          id="answer"
          aria-live="polite"
          aria-busy={loading}
          style={{ opacity: fading ? 0.35 : 1 }}
        >
          {loading && <div className="meta">Finding your answer…</div>}

          {errored && !loading && (
            <div className="disclaimer">
              Something went wrong reaching the benefits helper. Please check your connection and try
              again in a moment.
            </div>
          )}

          {showInitialHint && (
            <div className="meta">
              Type a question above, or pick an example, to see a cited answer from the official
              disability regulations.
            </div>
          )}

          {answer && !loading && (
            <>
              <div className="q">
                <span className="who">You</span>
                {answer.question}
              </div>
              <div className="a">
                {answer.paragraphs.map((p, i) => (
                  // Server-returned runtime text — rendered as a PLAIN TEXT node.
                  <p key={i}>{p}</p>
                ))}
                {answer.citations.length > 0 && (
                  <div className="cites">
                    {answer.citations.map((label) => (
                      <span key={label} className="cite">
                        <CiteIcon aria-hidden="true" />
                        {label}
                      </span>
                    ))}
                  </div>
                )}
                {answer.provenance && (
                  <div className="meta">
                    <span className="asof">{answer.provenance}</span>
                  </div>
                )}
                {answer.disclaimer && <div className="disclaimer">{answer.disclaimer}</div>}
              </div>
            </>
          )}
        </div>

        <div className="gate">
          {gated ? (
            <>
              <b>{gateMessage ?? 'That’s your free questions for now.'}</b>{' '}
              <a href={CREATE_ACCOUNT_URL}>Create a free account</a> to keep going.
            </>
          ) : !asked ? (
            <>
              Ask your own question — <b>{FREE_QUESTIONS} free</b>, no account needed.
            </>
          ) : (
            <>
              <b>
                {remaining} free question{remaining === 1 ? '' : 's'} left.
              </b>{' '}
              <a href={CREATE_ACCOUNT_URL}>Create a free account</a> to keep going and save your
              answers.
            </>
          )}
        </div>
      </div>
    </div>
  );
}
