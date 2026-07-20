import { CREATE_ACCOUNT_URL } from '../lib/config';
import { Check, ClockIcon } from './icons';

/**
 * Companion-first hero — the D.D. companion app is the STAR. The phone mockup
 * below is built in pure CSS/HTML (self-contained: the Artifact/site CSP blocks
 * remote images, so no external screenshot can load here).
 *
 * NOTE: the CSS phone mockup is a PLACEHOLDER for a real app screenshot later.
 * When product screenshots are ready, swap the `.phone` block for an <img>.
 */
export function Hero() {
  return (
    <section className="hero" id="top">
      <div className="wrap hero-grid">
        <div className="reveal">
          <span className="eyebrow">Independent living, with dignity — at any age or ability</span>
          <h1>A companion for living life on your own terms.</h1>
          <p className="lead">
            My Daily Dignity helps make sense of the mail, keeps medications and appointments on
            track, and shares the load with the people who help — for you, or someone you support.
          </p>
          <div className="hero-cta">
            <a href={CREATE_ACCOUNT_URL} className="btn btn-primary btn-lg">
              Create your free account
            </a>
            <a href="#does" className="btn btn-ghost btn-lg">
              See what it does
            </a>
          </div>
          <div className="assurance">
            <span>
              <Check className="tick" aria-hidden="true" />
              Private &amp; encrypted
            </span>
            <span>
              <Check className="tick" aria-hidden="true" />
              Built for real life
            </span>
          </div>
        </div>

        {/*
          PLACEHOLDER: pure-CSS phone mockup standing in for a real app
          screenshot. Everything here is self-contained (no remote assets), so it
          renders under the strict CSP. role="img" collapses the fake UI into a
          single labelled image for assistive tech.
        */}
        <div
          className="phone reveal d1"
          role="img"
          aria-label="The My Daily Dignity companion app: a warm good-morning greeting, a water bill read aloud with the amount due and due date pulled out, and a reminder set."
        >
          <div className="screen">
            <div className="app-top">
              <span className="av" aria-hidden="true">
                D
              </span>
              <span className="app-who">
                <b>D.D.</b>
                <small>Your companion</small>
              </span>
            </div>
            <div className="app-body">
              <div className="bubble">
                Good morning, Alex. You have one new letter, and your medication is at 8:00. Want me
                to read the letter?
              </div>
              <div className="doccard">
                <span className="k">I read it for you</span>
                <b>Water bill</b>
                <div className="row">
                  <span>Amount due</span>
                  <span>$58.20</span>
                </div>
                <div className="row">
                  <span>Due date</span>
                  <span>Aug 2</span>
                </div>
                <div className="doccard-foot">
                  <span className="pill">
                    <ClockIcon aria-hidden="true" />
                    Reminder set
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
