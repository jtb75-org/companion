/**
 * Post-login access decision for the MEMBER app.
 *
 * This app is the member app. After sign-in AppNavigator asks the backend who
 * the caller is and routes accordingly. The subtlety is that authentication
 * (`/auth/login`) admits every cohort — members, caregivers, and admins all
 * hold a valid session — but only MEMBERS have a `users` row, and the member
 * profile endpoint (`GET /api/v1/me`) resolves the session as a member and 401s
 * for anyone without that row. A caregiver/admin who signs in here therefore
 * gets a valid session and then a `/me` 401 that, historically, looked exactly
 * like an expired session and bounced them silently back to the login screen —
 * indistinguishable from a broken login.
 *
 * To tell "a non-member on the wrong app" apart from "a genuinely invalid or
 * expired session", we fall back to the ROLE-AWARE `GET /api/v1/auth/check`,
 * which answers for every cohort:
 *   - member (has a `users` row but no admin/caregiver row) -> 403 (unauthorized)
 *   - caregiver -> 200 { role: 'caregiver', ... }
 *   - admin     -> 200 { role: 'admin', ... }
 *   - no/expired session -> 401
 *
 * Backend signals confirmed against:
 *   - backend/app/api/v1/profile.py (`/api/v1/me` -> resolve_session_principal,
 *     which raises 401 "Session does not map to a known member" for a subject
 *     with no member row, and 403 for a deactivated member).
 *   - backend/app/api/v1/auth_check.py + app/auth/authorize.py (`/auth/check` ->
 *     resolve_session_email + authorize_by_email: admin/caregiver -> 200 with
 *     role, plain member -> 403, no session -> 401).
 *
 * The decision is a pure function of the two responses so it can be unit-tested
 * without a device: it takes an injected `apiFn` (the shared `api()` client, or
 * a fake in tests) and never touches native modules directly.
 */
import { ApiError } from '../api/client'

/** The `/api/v1/me` shape we care about (both the structured and raw-user forms). */
interface MeResponse {
  exists?: boolean
  profile_complete?: boolean
  first_name?: string
  last_name?: string
}

/** The `/api/v1/auth/check` fields we branch on. */
interface AuthCheckResponse {
  role?: string
}

/**
 * The routing outcome for the signed-in caller:
 *   - `member`     — a real member; proceed to onboarding/tabs (carries whether
 *                    the profile is complete, same rule AppNavigator used before).
 *   - `nonMember`  — a caregiver or admin on the member app; show the calm
 *                    "this app is for members" gate instead of bouncing to login.
 *   - `invalid`    — a genuinely invalid/expired session; sign out (as before).
 *   - `unknown`    — a transient/network/5xx failure we can't classify; treat as
 *                    an incomplete profile so the user isn't stranded on a spinner
 *                    (unchanged from the previous non-auth-error fallback).
 */
export type AppAccess =
  | { kind: 'member'; profileComplete: boolean }
  | { kind: 'nonMember' }
  | { kind: 'invalid' }
  | { kind: 'unknown' }

type ApiFn = <T>(path: string, options?: RequestInit) => Promise<T>

function memberProfileComplete(data: MeResponse): boolean {
  // Handle both the structured response ({exists, profile_complete}) and the raw
  // user model ({first_name, last_name}) — mirrors the original AppNavigator logic.
  if ('profile_complete' in data) {
    return data.exists !== false && data.profile_complete === true
  }
  return Boolean(data.first_name && data.last_name)
}

/**
 * Decide how to route the signed-in caller. See {@link AppAccess}.
 *
 * `apiFn` throws {@link ApiError} (carrying the HTTP status) on a non-2xx, exactly
 * like the shared `api()` client, so this works with the real client in the app
 * and a small fake in tests.
 */
export async function resolveAppAccess(apiFn: ApiFn): Promise<AppAccess> {
  try {
    const data = await apiFn<MeResponse>('/api/v1/me')
    return { kind: 'member', profileComplete: memberProfileComplete(data) }
  } catch (err) {
    // Only a 401/403 on /me is ambiguous (non-member vs invalid session). Any
    // other failure (network / 5xx) is transient and unclassifiable here.
    if (!(err instanceof ApiError) || (err.status !== 401 && err.status !== 403)) {
      return { kind: 'unknown' }
    }

    // Ask the role-aware check who this actually is.
    try {
      const check = await apiFn<AuthCheckResponse>('/api/v1/auth/check')
      // A 200 with an admin/caregiver role means a non-member signed in on the
      // member app — show the gate, don't sign them out into the login loop.
      if (check && (check.role === 'admin' || check.role === 'caregiver')) {
        return { kind: 'nonMember' }
      }
      // A 200 with any other/absent role is unexpected here; the safe, honest
      // outcome is to sign out rather than trap the user on a gate they can't act on.
      return { kind: 'invalid' }
    } catch {
      // /auth/check itself 401/403'd (expired session, or a plain member whose
      // /me 401 was a real auth failure) -> genuinely invalid session.
      return { kind: 'invalid' }
    }
  }
}
