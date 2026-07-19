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
  // Client-side check before we send: password too short. Matches the backend
  // floor (config password_min_length, default 10) so the guidance is consistent.
  activateTooShort: 'Please use at least 10 letters or numbers.',
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

  // --- Create-account screen (member self-signup) --------------------------
  // A new member makes their own account here. We only take a name + email;
  // they finish by tapping a link we email them (the /activate screen above).
  // MEMBER-facing — keep it warm, short, and plain. Avoid the words
  // "activation", "credentials", "verify", "account setup".

  // Small text button under Sign In that opens this screen.
  signupLink: 'New here? Create an account',
  // Form header.
  signupTitle: 'Create your account',
  // Under the title — tells them what happens next.
  signupSubtitle: "We'll email you a link to finish setting up.",
  // Field placeholders.
  signupNamePlaceholder: 'Your name',
  signupEmailPlaceholder: 'Your email',
  // The button that sends the request.
  signupButton: 'Create Account',
  // Client-side checks (before we send anything).
  signupMissingName: 'Please type your name.',
  signupMissingEmail: 'Please type your email.',
  signupBadEmail: 'Please check your email and try again.',
  // 429 = too many signups from this network right now.
  signupTooManyTries: 'Too many tries right now. Please wait a minute and try again.',
  // Network / unknown / anything else.
  signupError: 'Something went wrong. Please try again.',
  // Confirmation screen (after a successful send). The member's email is added
  // after this line in the app, so end the body with a space + <email>.
  signupSentTitle: 'Check your email',
  // Shown before the email address on the confirmation card.
  signupSentBodyPrefix: 'We sent a link to',
  // Shown after the email address on the confirmation card.
  signupSentBodySuffix: 'Tap it to finish setting up your account.',
  // Button that returns to the Sign In screen from the form or confirmation.
  signupBackButton: 'Back to Sign In',
  // On the "check your email" card: go back to the form to send the link again.
  signupResendLink: "Didn't get the email? Try again.",

  // --- Forgot-password screen (self-service password reset) ----------------
  // A member who cannot sign in types their email here, and we email them a link
  // to make a new password (the link opens the /activate screen above, in its
  // reset flavor).
  //
  // ANTI-ENUMERATION — the most important rule on this screen: the confirmation
  // MUST read exactly the same whether or not the email belongs to a real
  // account. Never say "we sent it" as a fact, and never say "no account found".
  // The conditional "If we have that email" wording below is what keeps this
  // honest for both cases. Do not edit it without re-review.

  // Small text button under Sign In that opens this screen.
  forgotLink: 'Forgot password?',
  // Form header.
  forgotTitle: 'Reset your password',
  // Under the title — tells them what happens next.
  forgotSubtitle: 'Type your email. We will send you a link to make a new password.',
  // Field placeholder.
  forgotEmailPlaceholder: 'Your email',
  // The button that sends the request.
  forgotButton: 'Send Link',
  // Client-side checks (before we send anything).
  forgotMissingEmail: 'Please type your email.',
  // NOTE: must NOT reuse "Check your email" — that is `forgotSentTitle` (the
  // SUCCESS card, meaning "go look in your inbox"). The same words one screen
  // away meaning "what you typed is wrong" is a comprehension trap.
  forgotBadEmail: 'That email does not look right. Please try again.',
  // 429 = too many reset tries from this network right now.
  forgotTooManyTries: 'Too many tries right now. Please wait a minute and try again.',
  // Network / unknown / anything else.
  forgotError: 'Something went wrong. Please try again.',
  // Confirmation card, shown for ANY successful send. Deliberately does NOT
  // repeat the email back or promise delivery — that would leak whether the
  // account exists.
  forgotSentTitle: 'Check your email',
  forgotSentBody:
    'If we have that email, we sent a link to it. Tap the link to make a new password.',
  // Button that returns to the Sign In screen from the form or confirmation.
  forgotBackButton: 'Back to Sign In',
  // On the "check your email" card: go back to the form to send the link again.
  forgotResendLink: "Didn't get the email? Try again.",

  // --- Reset flavor of the set-your-password screen (?reset=1) -------------
  // Same screen, same button action, same endpoint as the activation flow above
  // — ONLY the words change. Shown when the emailed link carries `reset=1`, so a
  // member resetting a password they already have is not told to "start using
  // D.D." as if they were brand new.

  // The "Hi <name>" greeting is KEPT on the reset path — this screen already
  // shows the account's email below, so dropping the name protects nothing and
  // only reads colder at a stressful moment. Only the prompt + button change.
  // Replaces `activatePrompt`.
  activateResetPrompt: 'Make a new password for your account.',
  // Replaces `activateCreateButton`. Same verb ("Make") as the prompt above so
  // the one action reads consistently (guidelines §3.3).
  activateResetCreateButton: 'Make New Password',
  // Replaces `activateInvalidBody` when the reset link is old or wrong: points
  // them at the forgot-password screen instead of at an invite email.
  activateResetInvalidBody:
    'This link is old or is not right. Please go back and ask for a new link.',

  // --- Member-only gate screen ---------------------------------------------
  // Shown AFTER a successful sign-in when the person is NOT a member — they are
  // a family helper or admin, who belong on the web page, not in this app.
  // (Their login worked; the member app just isn't for them.) The screen must
  // be calm and blame-free: they did nothing wrong, so no error wording, no
  // "denied", no mention of "role"/"caregiver dashboard". Tell them plainly
  // where to go instead, and give them a way out (Sign Out).
  //
  // Warm, short, ~6th-grade. Every string is safety-reviewed like the rest.

  // Header. Explains, gently, who this app is for.
  gateTitle: 'This app is for members',
  // Body. Names the audience warmly ("family and helpers") and says what they
  // do instead — no blame, no jargon.
  gateBody: 'It looks like you help take care of someone. Family and helpers do that from a web page, not in this app.',
  // Small line above the web address, telling them what to do next.
  gateWebPrompt: 'To sign in and help, open this web page in a browser:',
  // The web address to type in a browser. Kept as its own string so it can be
  // shown big and clear.
  gateWebAddress: 'app.mydailydignity.com',
  // The button that signs them out of this app.
  gateSignOutButton: 'Sign Out',
} as const

export type AuthStringKey = keyof typeof authStrings
