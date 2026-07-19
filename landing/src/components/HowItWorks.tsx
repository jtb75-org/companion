export function HowItWorks() {
  return (
    <section className="band" id="how">
      <div className="wrap">
        <div className="head-block">
          <span className="eyebrow">How it works</span>
          <h2>The benefits maze, one clear step at a time.</h2>
          <p>
            The federal disability process is the same in every state — it’s just written in
            language no one should have to decode alone. We turn the official rules into plain
            answers you can act on.
          </p>
        </div>
        <ol className="steps">
          <li className="step reveal">
            <div className="n" aria-hidden="true" />
            <h3>Ask in your own words</h3>
            <p>
              Type a real question — “how far back can benefits be paid?” — the way you’d ask a
              person, not a form.
            </p>
          </li>
          <li className="step reveal d1">
            <div className="n" aria-hidden="true" />
            <h3>Get a cited answer</h3>
            <p>
              A clear, calm reply drawn from the actual regulations and SSA’s own procedures — with
              the section cited so you can trust it.
            </p>
          </li>
          <li className="step reveal d2">
            <div className="n" aria-hidden="true" />
            <h3>Keep it all together</h3>
            <p>
              Create a free account to save what you learn, track an application, and bring the rest
              of the family into the loop.
            </p>
          </li>
        </ol>
      </div>
    </section>
  );
}
