/**
 * Direct calls to the self-hosted Authentik BFF login endpoints.
 *
 * These are used only when AUTH_PROVIDER === 'authentik'. Login is a pre-auth
 * request, so it does NOT go through the shared `api()` client (which would try
 * to attach a bearer we do not have yet). Logout does carry the bearer.
 *
 * Contract (shipped by backend-core, currently inert while the backend runs
 * auth_provider=firebase — /auth/login returns 404 until the flip):
 *   POST /auth/login  body {username, password, mobile: true}
 *     200 -> {status: 'ok', session_token: '<opaque sid>', csrf_token: '<...>'}
 *     401 -> bad username/password
 *     403 -> not invited / deactivated / email unverified
 *     429 -> rate limited
 *   POST /auth/logout -> invalidates the session server-side.
 *
 * The session_token is an OPAQUE session id, NOT a JWT. Never decode it.
 * csrf_token is ignored: a bearer token is non-ambient and needs no CSRF.
 */
import { API_BASE } from '../api/client'

/** Error thrown by `authentikLogin` carrying the HTTP status for copy mapping. */
export class AuthLoginError extends Error {
  status: number | null
  constructor(status: number | null, message?: string) {
    super(message ?? `Auth login failed (${status ?? 'network'})`)
    this.name = 'AuthLoginError'
    this.status = status
  }
}

interface LoginResponse {
  status: string
  session_token: string
  csrf_token?: string
}

/**
 * POST /auth/login. Returns the opaque session token on success.
 * Throws `AuthLoginError` with `.status` (401 / 403 / 429 / other / null for
 * network failures) so the caller can show calm, plain-language copy.
 */
export async function authentikLogin(username: string, password: string): Promise<string> {
  let res: Response
  try {
    res = await fetch(`${API_BASE}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password, mobile: true }),
    })
  } catch {
    // Network error (offline, DNS, TLS). No HTTP status available.
    throw new AuthLoginError(null)
  }

  if (!res.ok) {
    throw new AuthLoginError(res.status)
  }

  const data = (await res.json()) as LoginResponse
  if (!data?.session_token) {
    throw new AuthLoginError(null, 'Login response missing session token')
  }
  return data.session_token
}

/**
 * Account-activation endpoints (self-hosted / Authentik path only).
 *
 * A member created under Authentik gets an email with a link. Tapping it opens
 * the app to a "set your password" screen. These two calls back that screen:
 *
 *   GET  /api/v1/activation/validate?token=<t>
 *     200 -> {valid, email, name}  (link is good; greet + prefill email)
 *     404 -> link is unknown/expired
 *   POST /api/v1/activation/set-password  body {token, password}
 *     200 -> {ok, email}
 *     400 -> invalid / expired token
 *     502 -> the identity system failed (retryable)
 *
 * Under auth_provider=firebase the backend returns 404 for both, so this whole
 * flow is inert (the app never routes to the screen in Firebase mode anyway).
 *
 * Like login, these are PRE-auth requests, so they do NOT go through the shared
 * `api()` client (there is no bearer yet). The password is never logged.
 */
export interface ActivationDetails {
  valid: boolean
  email: string
  name: string
}

/**
 * GET /api/v1/activation/validate. Resolves the greeting name + email for a
 * good link. Throws `AuthLoginError` with `.status` (404 for a bad/expired
 * link, null for a network failure) so the screen can show a calm state.
 */
export async function validateActivationToken(token: string): Promise<ActivationDetails> {
  let res: Response
  try {
    res = await fetch(
      `${API_BASE}/api/v1/activation/validate?token=${encodeURIComponent(token)}`,
    )
  } catch {
    throw new AuthLoginError(null)
  }
  if (!res.ok) {
    throw new AuthLoginError(res.status)
  }
  return (await res.json()) as ActivationDetails
}

/**
 * POST /api/v1/activation/set-password. Returns the member's email on success
 * so the caller can immediately sign them in via `authentikLogin`.
 * Throws `AuthLoginError` with `.status` (400 bad/expired link, 502 IdP failure,
 * null network) so the screen can pick invalid-vs-retryable copy.
 */
export async function setActivationPassword(token: string, password: string): Promise<string> {
  let res: Response
  try {
    res = await fetch(`${API_BASE}/api/v1/activation/set-password`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token, password }),
    })
  } catch {
    throw new AuthLoginError(null)
  }
  if (!res.ok) {
    // 422 = password-policy rejection: surface the backend's plain message so the
    // screen can tell the member what to fix (distinct from a 400 bad/expired link).
    if (res.status === 422) {
      let detail = ''
      try {
        detail = ((await res.json()) as { detail?: string })?.detail || ''
      } catch {
        // non-JSON body → fall through to a generic 422
      }
      throw new AuthLoginError(422, detail || undefined)
    }
    throw new AuthLoginError(res.status)
  }
  const data = (await res.json()) as { ok: boolean; email: string }
  if (!data?.email) {
    throw new AuthLoginError(null, 'Set-password response missing email')
  }
  return data.email
}

/**
 * POST /auth/logout. Best-effort: invalidates the session server-side.
 * The caller clears local storage regardless of the result.
 */
export async function authentikLogout(sessionToken: string | null): Promise<void> {
  if (!sessionToken) return
  await fetch(`${API_BASE}/auth/logout`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${sessionToken}`,
    },
  })
}
