/**
 * Static config for the marketing landing. No secrets, no runtime env — these
 * are compile-time constants baked into the static bundle.
 *
 * The CTAs point at the existing authed app. This landing owns NO auth logic;
 * sign-in / create-account happen entirely in the app.
 */
export const APP_BASE_URL = 'https://app.mydailydignity.com';

export const SIGN_IN_URL = `${APP_BASE_URL}/login`;
export const CREATE_ACCOUNT_URL = `${APP_BASE_URL}/signup`;
