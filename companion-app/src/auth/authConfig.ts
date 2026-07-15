/**
 * Auth provider selection for the mobile app.
 *
 * This is the SINGLE client-side flag that switches the app between the two
 * login systems during the Firebase -> Authentik migration.
 *
 *   'authentik' (CURRENT) -> the self-hosted BFF login. Username/password against
 *                            POST /auth/login, opaque session token stored on
 *                            device, `Authorization: Bearer` on every request.
 *   'firebase'            -> the legacy login (Firebase email/password + Google
 *                            sign-in). Kept as the rollback target; when selected
 *                            the Authentik screen and the bearer-token API path
 *                            are inert.
 *
 * The backend is DUAL-RUN (Authentik BFF live + validated end-to-end on web for
 * admin, member self-signup, and caregiver invite/accept as of 2026-07-15), so
 * POST /auth/login is live. The mobile app is now cut over to Authentik.
 *
 * ROLLBACK: set this back to 'firebase' and re-ship (app-store round-trip) — the
 * backend never moves, so the flip is client-only. Validate hard in TestFlight /
 * Play internal before promoting to production.
 */
export type AuthProvider = 'firebase' | 'authentik'

export const AUTH_PROVIDER: AuthProvider = 'authentik'
