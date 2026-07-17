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

export const RESET_PASSWORD_COPY = {
  // Reset-flavored copy for the set-password landing page (?reset=1).
  // `title` is the heading (NOT "Reset your password" — that is the request
  // form's heading, and reusing it made the two screens indistinguishable).
  title: 'Set a new password',
  // The greeting is KEPT on the reset path: this page already shows the
  // account's email in the form below, so suppressing the name protects
  // nothing and only reads colder at a stressful moment. Rendered as
  // "Welcome back, <name>. Make a new password for your account."
  greetingPrefix: 'Welcome back,',
  promptSuffix: 'Make a new password for your account.',
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
