/**
 * ALL user-facing copy for the Authentik (self-hosted) login screen lives here,
 * in ONE place, so the safety-privacy-reviewer can sign off on every string.
 *
 * Writing guide (matches GEMINI.md / Easy-Read philosophy):
 *   - 4th-6th grade reading level. Short sentences.
 *   - No jargon. Never say "identity provider", "authentication", "session",
 *     "token", "credentials", "server", "Authentik", or "BFF".
 *   - Calm, not alarming. Tell the user what to do next.
 *
 * Every string below is user-visible. If you add or change a string here,
 * it needs safety-privacy-reviewer sign-off before merge.
 */
export const authStrings = {
  // Screen header
  title: 'D.D. Companion',
  subtitle: 'Your daily independence assistant',

  // Field labels / placeholders
  usernamePlaceholder: 'Username',
  passwordPlaceholder: 'Password',

  // Buttons
  signInButton: 'Sign In',

  // Validation (before we send anything)
  missingFields: 'Please type your username and password.',

  // Error messages, mapped from the server response.
  // 401 = wrong username or password
  errorBadCredentials: 'That username or password is not right. Please try again.',
  // 403 = account not allowed in yet. This is a MERGED state (not-invited /
  // deactivated-or-pending-deletion / email-unverified). The copy must stay
  // merged and must NOT split by sub-case, or it would leak account state.
  // Leads with the action true for all three cases (ask your helper) and keeps
  // the invite hint for the common launch case. Wording is safety-approved
  // verbatim — do not edit without re-review.
  errorNotAllowed: 'We cannot sign you in yet. Please ask your helper for help, or check your email for an invite.',
  // 429 = too many tries, slow down
  errorTooManyTries: 'Too many tries. Please wait a minute, then try again.',
  // Network / unknown / anything else
  errorGeneric: 'Something went wrong. Please try again.',
} as const

export type AuthStringKey = keyof typeof authStrings
