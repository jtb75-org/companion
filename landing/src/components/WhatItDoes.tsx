import { DocIcon, ClockIcon, PeopleIcon } from './icons';

/**
 * "What your companion does" — three capability cards. The richer treatment
 * from the approved concept: understand any document / stay on top of every day
 * / share the load. Inclusive, not aging-parent-specific.
 */
export function WhatItDoes() {
  return (
    <section id="does">
      <div className="wrap">
        <div className="head-block">
          <span className="eyebrow">What your companion does</span>
          <h2>The everyday weight of living independently — carried with you.</h2>
          <p>
            The bills, the appointments, the medications, the decisions. My Daily Dignity keeps it
            all in one calm place, so your energy goes to living, not managing.
          </p>
        </div>
        <div className="caps">
          <div className="cap reveal">
            <div className="ic" aria-hidden="true">
              <DocIcon />
            </div>
            <h3>Understand any document</h3>
            <p>
              A bill or a benefits letter, read aloud in plain words — with the important dates and
              amounts pulled out for you.
            </p>
          </div>
          <div className="cap reveal d1">
            <div className="ic" aria-hidden="true">
              <ClockIcon />
            </div>
            <h3>Stay on top of every day</h3>
            <p>
              Medications, appointments, and deadlines — gently kept on track, so nothing important
              slips by.
            </p>
          </div>
          <div className="cap reveal d2">
            <div className="ic" aria-hidden="true">
              <PeopleIcon />
            </div>
            <h3>Share the load</h3>
            <p>
              Bring in family or trusted helpers, each with just the right level of access — support
              without taking over.
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}
