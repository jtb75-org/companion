import { auth } from '../auth/firebase'

const API_BASE = import.meta.env.VITE_API_BASE_URL || ''
const AUTH_PROVIDER = import.meta.env.VITE_AUTH_PROVIDER || 'firebase'

// Read a cookie value by name from document.cookie (used for the CSRF
// double-submit token in Authentik BFF mode).
function readCookie(name: string): string | null {
  const match = document.cookie.match(
    new RegExp('(?:^|; )' + name.replace(/([.$?*|{}()[\]\\/+^])/g, '\\$1') + '=([^;]*)')
  )
  return match ? decodeURIComponent(match[1]) : null
}

// The CSRF double-submit token, delivered in the /auth/login and /auth/check response
// BODIES and held here. The web app is on a different subdomain than the API, so the
// host-only companion_csrf cookie set by the API is unreadable to document.cookie —
// reading it from the body is how the SPA can echo X-CSRF-Token on writes. AuthProvider
// sets this on login and on session check, and clears it on logout.
let csrfToken: string | null = null
export function setCsrfToken(token: string | null): void {
  csrfToken = token
}
// For raw fetches outside api() (e.g. logout) that still need the double-submit header.
export function getCsrfToken(): string | null {
  return csrfToken ?? readCookie('companion_csrf')
}

export async function api<T>(
  path: string,
  options?: RequestInit
): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  }

  const user = AUTH_PROVIDER === 'firebase' ? auth.currentUser : null
  if (AUTH_PROVIDER === 'firebase' && user) {
    const token = await user.getIdToken()
    headers['Authorization'] = `Bearer ${token}`
  }

  // Authentik BFF: cookie session is ambient. Attach the CSRF double-submit
  // header on unsafe methods (backend compares it to the companion_csrf cookie).
  if (AUTH_PROVIDER === 'authentik') {
    const method = (options?.method || 'GET').toUpperCase()
    if (method !== 'GET' && method !== 'HEAD' && method !== 'OPTIONS') {
      // Prefer the body-delivered token (cross-subdomain); fall back to the readable
      // cookie for any same-origin deployment.
      const csrf = csrfToken ?? readCookie('companion_csrf')
      if (csrf) {
        headers['X-CSRF-Token'] = csrf
      }
    }
  }

  // Spread options FIRST so our credentials/headers below always win — a
  // caller's options.headers merges on top of the defaults, but must never
  // clobber the ambient-cookie credentials mode or drop the CSRF/auth headers.
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    credentials: 'include',
    signal: options?.signal,
    headers: { ...headers, ...options?.headers },
  })

  // Firebase mode: on 401, try refreshing the token and retry once.
  if (res.status === 401 && AUTH_PROVIDER === 'firebase' && user) {
    const freshToken = await user.getIdToken(true) // force refresh
    headers['Authorization'] = `Bearer ${freshToken}`
    const retry = await fetch(`${API_BASE}${path}`, {
      ...options,
      credentials: 'include',
      headers: { ...headers, ...options?.headers },
    })
    if (!retry.ok) {
      throw new Error(`API error: ${retry.status}`)
    }
    return retry.json()
  }

  // Authentik mode: a 401 on an authenticated request means the ambient cookie
  // session expired mid-use (login/check use raw fetch, not this client, so they
  // never reach here). Signal the AuthProvider to drop session state so the
  // privileged shell can't linger; ProtectedRoute then redirects to /login.
  if (res.status === 401 && AUTH_PROVIDER === 'authentik') {
    window.dispatchEvent(new Event('companion:session-expired'))
  }

  if (!res.ok) {
    // Surface the backend's `detail` (e.g. a plain password-policy message on a
    // 422) so callers can show it directly, rather than a generic "API error".
    // Fall back to the status when there's no JSON detail. Attach the status for
    // callers that branch on it.
    let detail = ''
    try {
      detail = ((await res.json()) as { detail?: string })?.detail || ''
    } catch {
      // non-JSON body → keep the generic message
    }
    const err = new Error(detail || `API error: ${res.status}`) as Error & {
      status?: number
    }
    err.status = res.status
    throw err
  }

  if (res.status === 204) {
    return {} as T
  }

  return res.json()
}

// Request a password-reset email. The backend ALWAYS returns 200 {"status":"ok"}
// for a valid request shape (anti-enumeration: it never reveals whether the
// address exists), throws with `.status === 429` when rate-limited, and 422 on
// an invalid email. Callers must show the same confirmation for any success.
export async function forgotPassword(email: string): Promise<{ status: string }> {
  return api<{ status: string }>('/auth/forgot-password', {
    method: 'POST',
    body: JSON.stringify({ email }),
  })
}
