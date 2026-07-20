import { CREATE_ACCOUNT_URL } from '../lib/config';

export function CTA() {
  return (
    <section className="wrap">
      <div className="cta">
        <h2>Independence, made a little easier.</h2>
        <p>
          Create a free account and let My Daily Dignity carry the hard parts — for you, or someone
          you support.
        </p>
        <a href={CREATE_ACCOUNT_URL} className="btn btn-primary btn-lg">
          Create your free account
        </a>
        <p className="tiny">No card required · Your information stays private</p>
      </div>
    </section>
  );
}
