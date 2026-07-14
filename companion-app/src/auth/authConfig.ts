/**
 * Auth provider selection for the mobile app.
 *
 * This is the SINGLE client-side flag that switches the app between the two
 * login systems during the Firebase -> Authentik migration.
 *
 *   'firebase'  (DEFAULT) -> the live, shipping login. Firebase email/password
 *                            + Google sign-in. The Authentik screen and the
 *                            bearer-token API path are completely inert.
 *   'authentik'           -> the new self-hosted BFF login. Username/password
 *                            against POST /auth/login, opaque session token
 *                            stored on device, `Authorization: Bearer` on every
 *                            request.
 *
 * KEEP THIS ON 'firebase' until the backend is flipped to `auth_provider=authentik`.
 * While the backend runs Firebase, POST /auth/login returns 404, so the
 * Authentik path must never be the default.
 */
export type AuthProvider = 'firebase' | 'authentik'

export const AUTH_PROVIDER: AuthProvider = 'firebase'
