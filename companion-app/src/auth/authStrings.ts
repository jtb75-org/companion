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

  // --- Set-your-password screen (opened from the email link) ---------------
  // A member taps the link in their email and lands here to make a password.
  // Copy must avoid the words "activation" and "credentials". Warm + short.

  // While we check the link is good.
  activateChecking: 'One moment…',
  // Friendly greeting. The member's name is added after this word in the app.
  activateHello: 'Hi',
  // Under the greeting, tells them what to do.
  activatePrompt: 'Make a password to start using D.D.',
  // Small label above the (read-only) email so they know it is their email.
  activateEmailLabel: 'Your email',
  // New-password field.
  activateNewPasswordPlaceholder: 'New password',
  // The button that saves the new password. Same verb ("Make") as the prompt
  // above, so one action reads consistently for the member (guidelines §3.3).
  activateCreateButton: 'Make Password',
  // Client-side check before we send: password too short.
  activateTooShort: 'Please use at least 8 letters or numbers.',
  // The link is old or wrong (bad token on check OR on save). Calm, no blame.
  activateInvalidTitle: 'This link did not work',
  activateInvalidBody:
    'This link is old or is not right. Please check your email for a new one, or ask your helper.',
  // Saving the password failed for a reason they can retry (server hiccup).
  activateSaveError: 'We could not save your password. Please try again.',
  // Password saved but the auto sign-in did not work. Send them to Sign In.
  activateSavedGoSignIn: 'Your password is saved. Please go back and sign in.',
  // Button that returns to the Sign In screen from the invalid/saved states.
  activateBackButton: 'Back to Sign In',
} as const

export type AuthStringKey = keyof typeof authStrings
