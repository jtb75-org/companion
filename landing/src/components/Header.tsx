import { HeartMark } from './icons';
import { CREATE_ACCOUNT_URL, SIGN_IN_URL } from '../lib/config';

export function Header() {
  return (
    <header>
      <nav className="nav wrap" aria-label="Primary">
        <span className="brand">
          <span className="mark" aria-hidden="true">
            <HeartMark />
          </span>
          My Daily Dignity
        </span>
        <span className="navlinks">
          <a href="#does">What it does</a>
          <a href="#resource">Benefits helper</a>
          <a href={SIGN_IN_URL} className="btn btn-ghost">
            Sign in
          </a>
          <a href={CREATE_ACCOUNT_URL} className="btn btn-primary">
            Create free account
          </a>
        </span>
      </nav>
    </header>
  );
}
