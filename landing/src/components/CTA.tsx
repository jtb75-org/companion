import { CREATE_ACCOUNT_URL } from '../lib/config';

export function CTA() {
  return (
    <section className="wrap">
      <div className="cta">
        <h2>Start with one question. Keep the peace of mind.</h2>
        <p>
          Ask the benefits helper anything, free. When you’re ready, a free account keeps it all in
          one dignified place.
        </p>
        <a href={CREATE_ACCOUNT_URL} className="btn btn-primary btn-lg">
          Create your free account
        </a>
        <p className="tiny">No card required · Cancel anytime · Your information stays private</p>
      </div>
    </section>
  );
}
