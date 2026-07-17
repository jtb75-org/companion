/**
 * Centralized user-facing copy for the password-recovery surfaces.
 *
 * Keep this text calm and Easy-Read (short, plain sentences). All strings here
 * are reviewed by safety-privacy for reading level and — critically — the
 * anti-enumeration property: nothing may reveal whether a given email belongs
 * to a real account. The "forgot password" confirmation must read identically
 * whether or not the address exists.
 */

export const FORGOT_PASSWORD_COPY = {
  // Link shown on the sign-in page.
  loginLink: 'Forgot password?',

  // The request form.
  title: 'Reset your password',
  prompt: 'Type your email. We will send you a link to make a new password.',
  emailPlaceholder: 'Email',
  submit: 'Send reset link',
  submitting: 'Sending…',
  backToLogin: 'Back to sign in',

  // The confirmation card. Shown for ANY successful (2xx) request — it must not
  // depend on whether the email matched an account. The conditional "If we have
  // that email" is deliberate: it stays true when no account exists, and the
  // address is NOT echoed back (echoing it would read as confirmation). Kept
  // word-for-word in sync with the mobile `forgotSentBody`.
  sentTitle: 'Check your email',
  sentBody:
    'If we have that email, we sent a link to it. Tap the link to make a new password.',

  // Errors.
  rateLimited: 'Too many tries. Please wait a minute and try again.',
  genericError: 'Something went wrong. Please try again.',
} as const

/**
 * Shared by BOTH flavors of the set-password page (activation AND reset).
 *
 * Without this the input's `minLength` let the BROWSER speak — "Please lengthen
 * this text to 10 characters or more" — which is not our voice and is well above
 * the reading bar, at the exact moment a member has already failed once. The
 * check now runs in `handleSubmit` and this string is shown instead. Kept
 * word-for-word in sync with the mobile `activateTooShort`, and with the backend
 * floor (config `password_min_length`, default 10).
 */
export const SET_PASSWORD_COPY = {
  minLength: 10,
  tooShort: 'Please use at least 10 letters or numbers.',
} as const

export const RESET_PASSWORD_COPY = {
  // Reset-flavored copy for the set-password landing page (?reset=1).
  // Each slot does ONE job, and every slot uses the same verb ("Make"):
  //   title    -> the task            "Make a new password"
  //   greeting -> warmth only         "Welcome back, <name>."
  //   submit   -> the action          "Make New Password"
  // `title` must stay distinct from the request form's 'Reset your password'
  // heading, or the two screens become indistinguishable. Web needs a `title`
  // at all only because its heading slot is taken by the brand name — mobile's
  // heading IS the greeting, so it needs no equivalent. Same meaning, different
  // layouts.
  title: 'Make a new password',
  // The greeting is KEPT on the reset path: this page already shows the
  // account's email in the form below, so suppressing the name protects
  // nothing and only reads colder at a stressful moment.
  greetingPrefix: 'Welcome back,',
  passwordPlaceholder: 'New password',
  // Same verb as the prompt ("Make"), per the Easy-Read guidelines.
  submit: 'Make New Password',
  submitting: 'Saving…',
  // Plain words, no "invalid/expired/request" paperwork register — and the page
  // MUST render `invalidLinkAction` alongside this, or it tells the member to do
  // something the screen gives them no way to do.
  invalidLink: 'This link is old or is not right. You can ask for a new link.',
  invalidLinkAction: 'Ask for a new link',
} as const
