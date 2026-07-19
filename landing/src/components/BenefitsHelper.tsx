import { useEffect, useRef, useState } from 'react';
import { knowledgeApi, type BenefitsAnswer } from '../lib/knowledgeApi';
import { CREATE_ACCOUNT_URL } from '../lib/config';
import { Arrow, CiteIcon } from './icons';

const FREE_QUESTIONS = 3;

/**
 * The benefits-helper widget. This is the MOCK: it renders canned, cited
 * answers for the sample questions and a freemium gate. It talks only to
 * `knowledgeApi` (the integration seam) — never to a real endpoint. Free-text
 * questions are intentionally NOT answered (no fake answers); the visitor is
 * invited to create an account instead.
 */
export function BenefitsHelper() {
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [selected, setSelected] = useState<number>(0);
  const [answer, setAnswer] = useState<BenefitsAnswer | null>(null);
  const [previewNote, setPreviewNote] = useState<string | null>(null);
  const [used, setUsed] = useState<number>(0);
  const [fading, setFading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const remaining = Math.max(FREE_QUESTIONS - used, 0);
  const exhausted = remaining <= 0;

  // Load suggestions + render the first answer on mount (matches the concept,
  // which shows question 0 answered on load; the initial render does not spend
  // a free question).
  useEffect(() => {
    let live = true;
    (async () => {
      const s = await knowledgeApi.suggestions();
      if (!live) return;
      setSuggestions(s);
      const first = await knowledgeApi.ask({ question: s[0] });
      if (!live) return;
      setAnswer(first);
    })();
    return () => {
      live = false;
    };
  }, []);

  async function renderAnswer(question: string, index: number, spend: boolean) {
    if (spend && exhausted) return;
    setPreviewNote(null);
    setFading(true);
    if (inputRef.current) inputRef.current.value = question;
    setSelected(index);
    const result = await knowledgeApi.ask({ question });
    // Brief fade matching the concept's 120ms swap.
    window.setTimeout(() => {
      setAnswer(result);
      setFading(false);
      if (spend) setUsed((u) => u + 1);
    }, 120);
  }

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const question = inputRef.current?.value.trim() ?? '';
    if (!question) return;
    const idx = suggestions.findIndex((s) => s.toLowerCase() === question.toLowerCase());
    if (idx >= 0) {
      void renderAnswer(suggestions[idx], idx, true);
      return;
    }
    // Free-text is a preview only — never fabricate an answer.
    setAnswer(null);
    setFading(false);
    setPreviewNote(
      'This preview answers the sample questions above. Create a free account to ask your own questions and get cited answers.',
    );
  }

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
            placeholder="Ask about Social Security or SSDI…"
            aria-label="Ask a question about Social Security or SSDI"
            defaultValue={suggestions[0] ?? ''}
          />
          <button className="go" type="submit" aria-label="Ask">
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
              onClick={() => renderAnswer(s, i, true)}
            >
              {s}
            </button>
          ))}
        </div>

        <div className="answer" id="answer" aria-live="polite" style={{ opacity: fading ? 0 : 1 }}>
          {answer && (
            <>
              <div className="q">
                <span className="who">You</span>
                {answer.question}
              </div>
              <div className="a">
                {answer.paragraphs.map((p, i) => (
                  // Trusted, constant canned content (see knowledgeApi contract).
                  <p key={i} dangerouslySetInnerHTML={{ __html: p }} />
                ))}
                <div className="cites">
                  {answer.citations.map((c) => {
                    const inner = (
                      <>
                        <CiteIcon aria-hidden="true" />
                        {c.label}
                      </>
                    );
                    return c.url ? (
                      <a key={c.label} className="cite" href={c.url} rel="noopener">
                        {inner}
                      </a>
                    ) : (
                      <span key={c.label} className="cite">
                        {inner}
                      </span>
                    );
                  })}
                </div>
                <div className="meta">
                  <span className="asof">{answer.asOf}</span>
                </div>
                <div className="disclaimer">{answer.disclaimer}</div>
              </div>
            </>
          )}
          {previewNote && <div className="disclaimer">{previewNote}</div>}
        </div>

        <div className="gate">
          {exhausted ? (
            <>
              <b>That’s your 3 free questions.</b>{' '}
              <a href={CREATE_ACCOUNT_URL}>Create a free account</a> to keep going.
            </>
          ) : (
            <>
              You have{' '}
              <b>
                {remaining} of {FREE_QUESTIONS}
              </b>{' '}
              free questions left. <a href={CREATE_ACCOUNT_URL}>Create a free account</a> to keep
              going.
            </>
          )}
        </div>
      </div>
    </div>
  );
}
