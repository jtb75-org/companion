import { Fragment, type ReactNode } from 'react';

/**
 * SafeMarkdown — a tightly-scoped, XSS-safe markdown renderer for the public
 * benefits helper.
 *
 * WHY THIS EXISTS
 * The benefits-helper `answer` is RUNTIME, LLM-generated content served on a
 * PUBLIC surface. The model emits light markdown (`**bold**`, `1.`/`-` lists,
 * paragraphs, line breaks) which previously showed as literal asterisks because
 * we rendered it as plain text. We now render that markdown as formatted output.
 *
 * SAFETY INVARIANT (unchanged from the plain-text era)
 *  - There is NO `dangerouslySetInnerHTML` here and NO HTML string is ever
 *    constructed. This renderer parses text and emits a React ELEMENT TREE only
 *    (`<p>`, `<strong>`, `<em>`, `<ul>`, `<ol>`, `<li>`, `<br>`). Every leaf is a
 *    JS string passed as a React child, so React escapes it. Any raw HTML the
 *    model emits — e.g. `<script>…</script>` or `<img onerror=…>` — is not
 *    matched by the grammar below and therefore renders as inert, escaped
 *    literal text. It is never parsed or executed.
 *  - The renderer only recognises a small, fixed grammar. Anything it does not
 *    understand degrades to visible plain text — never to markup.
 *
 * SUPPORTED SUBSET
 *  - Paragraphs (blocks are already split on blank lines upstream).
 *  - Ordered lists (`1. item`) and unordered lists (`- `, `* `, `+ `).
 *  - Inline bold (`**text**` / `__text__`) and inline italic (`*text*`).
 *  - Hard line breaks between consecutive text lines inside a block.
 *  - `#`..`######` headings degrade to a bold line (so a stray `##` never shows).
 */

const ORDERED = /^\s*\d+\.\s+(.*)$/;
const UNORDERED = /^\s*[-*+]\s+(.*)$/;
const HEADING = /^\s*#{1,6}\s+(.*)$/;

/**
 * Parse a single line's INLINE markup (bold / italic) into React nodes.
 *
 * Only emphasis is recognised. `**` / `__` → <strong>, single `*` → <em>. The
 * captured content is a plain string rendered as a React child (escaped by
 * React), so no markup can be injected through it.
 */
function renderInline(text: string): ReactNode {
  // Order matters: try the two-char bold delimiters before the one-char italic.
  const pattern = /\*\*([^*]+)\*\*|__([^_]+)__|\*([^*\s][^*]*?)\*/g;
  const nodes: ReactNode[] = [];
  let lastIndex = 0;
  let key = 0;
  let m: RegExpExecArray | null;

  while ((m = pattern.exec(text)) !== null) {
    if (m.index > lastIndex) nodes.push(text.slice(lastIndex, m.index));
    const bold = m[1] ?? m[2];
    if (bold !== undefined) {
      nodes.push(<strong key={key++}>{bold}</strong>);
    } else {
      nodes.push(<em key={key++}>{m[3]}</em>);
    }
    lastIndex = pattern.lastIndex;
  }
  if (lastIndex < text.length) nodes.push(text.slice(lastIndex));

  // A block with no emphasis collapses to its single string child.
  if (nodes.length === 0) return text;
  if (nodes.length === 1 && typeof nodes[0] === 'string') return nodes[0];
  return nodes;
}

/**
 * Render ONE upstream paragraph block (a string that may contain single
 * newlines forming a list or hard line breaks) into a fragment of block-level
 * React elements.
 */
export function MarkdownBlock({ source }: { source: string }): ReactNode {
  const lines = source.split('\n');
  const out: ReactNode[] = [];
  let i = 0;
  let key = 0;

  while (i < lines.length) {
    const line = lines[i];

    if (line.trim() === '') {
      i++;
      continue;
    }

    if (ORDERED.test(line)) {
      const items: ReactNode[] = [];
      while (i < lines.length) {
        const mm = lines[i].match(ORDERED);
        if (!mm) break;
        items.push(<li key={items.length}>{renderInline(mm[1])}</li>);
        i++;
      }
      out.push(
        <ol key={key++} className="md-list">
          {items}
        </ol>,
      );
      continue;
    }

    if (UNORDERED.test(line)) {
      const items: ReactNode[] = [];
      while (i < lines.length) {
        const mm = lines[i].match(UNORDERED);
        if (!mm) break;
        items.push(<li key={items.length}>{renderInline(mm[1])}</li>);
        i++;
      }
      out.push(
        <ul key={key++} className="md-list">
          {items}
        </ul>,
      );
      continue;
    }

    const heading = line.match(HEADING);
    if (heading) {
      out.push(
        <p key={key++} className="md-heading">
          <strong>{renderInline(heading[1])}</strong>
        </p>,
      );
      i++;
      continue;
    }

    // A run of ordinary text lines → one paragraph with hard line breaks.
    const textLines: string[] = [];
    while (i < lines.length) {
      const l = lines[i];
      if (l.trim() === '' || ORDERED.test(l) || UNORDERED.test(l) || HEADING.test(l)) break;
      textLines.push(l);
      i++;
    }
    out.push(
      <p key={key++}>
        {textLines.map((t, k) => (
          <Fragment key={k}>
            {k > 0 && <br />}
            {renderInline(t)}
          </Fragment>
        ))}
      </p>,
    );
  }

  return <>{out}</>;
}
