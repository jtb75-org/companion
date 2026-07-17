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
  prompt: 'Enter your email and we will send you a link to make a new password.',
  emailPlaceholder: 'Email',
  submit: 'Send reset link',
  submitting: 'Sending…',
  backToLogin: 'Back to sign in',

  // The confirmation card. Shown for ANY successful (2xx) request — it must not
  // depend on whether the email matched an account.
  sentTitle: 'Check your email',
  sentBody:
    'If that email is on file, we have sent a link to reset your password. Please check your inbox.',

  // Errors.
  rateLimited: 'Too many tries. Please wait a minute and try again.',
  genericError: 'Something went wrong. Please try again.',
} as const

export const RESET_PASSWORD_COPY = {
  // Reset-flavored copy for the set-password landing page (?reset=1).
  subtitle: 'Reset your password',
  title: 'Set a new password',
  prompt: 'Choose a new password for your account.',
  passwordPlaceholder: 'New password',
  submit: 'Save new password',
  submitting: 'Saving…',
  invalidLink:
    'This reset link is invalid or has expired. Please request a new one.',
} as const
