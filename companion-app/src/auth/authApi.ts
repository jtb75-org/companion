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
