import { BenefitsHelper } from './BenefitsHelper';
import { CREATE_ACCOUNT_URL } from '../lib/config';
import { Check } from './icons';

export function Hero() {
  return (
    <section className="hero" id="tool">
      <div className="wrap hero-grid">
        <div className="reveal">
          <span className="eyebrow">Free · answers cited to official SSA rules</span>
          <h1>Disability benefits, explained without the runaround.</h1>
          <p className="lead">
            Explore plain-language answers to common Social Security and SSDI questions — each one
            drawn straight from the official regulations, with the source cited. When you’re ready, a
            free account lets you ask your own.
          </p>
          <div className="hero-cta">
            <a href={CREATE_ACCOUNT_URL} className="btn btn-primary btn-lg">
              Ask your own question, free
            </a>
            <a href="#how" className="btn btn-ghost btn-lg">
              See how it works
            </a>
          </div>
          <div className="assurance">
            <span>
              <Check className="tick" aria-hidden="true" />
              No account to start
            </span>
            <span>
              <Check className="tick" aria-hidden="true" />
              Every answer cited
            </span>
            <span>
              <Check className="tick" aria-hidden="true" />
              Plain, respectful language
            </span>
          </div>
        </div>

        <div aria-label="Benefits helper demo">
          <BenefitsHelper />
        </div>
      </div>
    </section>
  );
}
