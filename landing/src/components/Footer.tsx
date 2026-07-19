import { HeartMark } from './icons';
import { SIGN_IN_URL } from '../lib/config';

export function Footer() {
  return (
    <footer>
      <div className="wrap">
        <div className="foot-grid">
          <span className="brand" style={{ fontSize: '1.12rem' }}>
            <span className="mark" aria-hidden="true">
              <HeartMark />
            </span>
            My Daily Dignity
          </span>
          <nav className="foot-links" aria-label="Footer">
            <div>
              <span className="k">Product</span>
              <a href="#tool">Benefits helper</a>
              <a href="#more">For caregivers</a>
              <a href={SIGN_IN_URL}>Sign in</a>
            </div>
            <div>
              <span className="k">Company</span>
              <a href="#">About</a>
              <a href="#">Privacy</a>
              <a href="#">Contact</a>
            </div>
            <div>
              <span className="k">Learn</span>
              <a href="#">The SSDI process</a>
              <a href="#">Common questions</a>
            </div>
          </nav>
        </div>
        <p className="legal">
          My Daily Dignity is not affiliated with, endorsed by, or connected to the Social Security
          Administration or any government agency. The benefits helper provides general information
          drawn from public federal regulations to help you understand the process — it is not legal
          or financial advice. For decisions about your specific situation, contact the SSA or a
          qualified professional.
        </p>
      </div>
    </footer>
  );
}
