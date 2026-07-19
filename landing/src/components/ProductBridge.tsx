import { DocIcon, ClockIcon, PeopleIcon } from './icons';

export function ProductBridge() {
  return (
    <section id="more">
      <div className="wrap bridge">
        <div className="bridge-copy reveal">
          <span className="eyebrow">Beyond benefits</span>
          <h2>Caring for someone is a full-time job. This is the part we can carry.</h2>
          <p>
            My Daily Dignity keeps the paperwork, appointments, and decisions of caring for an aging
            parent in one calm place — so you can spend your energy on them, not on the admin.
          </p>
          <ul className="feat">
            <li>
              <span className="ic" aria-hidden="true">
                <DocIcon />
              </span>
              <span className="txt">
                <b>Understand any document</b>A bill or a benefits letter, read aloud in plain words
                — with the important dates pulled out for you.
              </span>
            </li>
            <li>
              <span className="ic" aria-hidden="true">
                <ClockIcon />
              </span>
              <span className="txt">
                <b>Never miss what matters</b>Medications, appointments, and deadlines, gently kept
                on track.
              </span>
            </li>
            <li>
              <span className="ic" aria-hidden="true">
                <PeopleIcon />
              </span>
              <span className="txt">
                <b>Share the load</b>Invite siblings or trusted caregivers, each with the right
                level of access.
              </span>
            </li>
          </ul>
        </div>
        <div className="portrait reveal d2" aria-hidden="true">
          <div className="card2">
            <span className="k">Application status</span>
            <b>Reconsideration filed</b>
            <span style={{ color: 'var(--muted)', fontSize: '.9rem' }}>
              Next: hearing request window opens Aug 2
            </span>
            <div className="bar a" />
            <div className="bar b" />
            <div className="bar c" />
          </div>
        </div>
      </div>
    </section>
  );
}
