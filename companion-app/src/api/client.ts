import auth from '@react-native-firebase/auth'
import { AUTH_PROVIDER } from '../auth/authConfig'
import { getSessionTokenSync } from '../auth/sessionToken'

// Self-hosted prod backend (staging Cloud Run is retired). Point dev at a
// local/LAN backend here if you run one.
export const API_BASE = __DEV__
  ? 'https://api.mydailydignity.com'
  : 'https://api.mydailydignity.com'

/**
 * The dual-run Authorization header for an authenticated request. Under Authentik it is
 * the opaque session bearer (Keychain); under Firebase the ID token. Returns {} when
 * unauthenticated. This is the SINGLE place the auth scheme is chosen, so every caller —
 * including the multipart document-scan endpoints that can't use api() (they send
 * FormData, not JSON) — stays correct across the cutover.
 */
export async function getAuthHeader(): Promise<Record<string, string>> {
  if (AUTH_PROVIDER === 'authentik') {
    // Self-hosted BFF: attach the opaque session token as a non-ambient bearer.
    const sessionToken = getSessionTokenSync()
    return sessionToken ? { Authorization: `Bearer ${sessionToken}` } : {}
  }
  // Firebase (legacy path) — unchanged behavior.
  const user = auth().currentUser
  if (!user) return {}
  const token = await user.getIdToken()
  return { Authorization: `Bearer ${token}` }
}

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(await getAuthHeader()),
  }

  const res = await fetch(`${API_BASE}${path}`, {
    headers: { ...headers, ...options?.headers },
    ...options,
  })

  if (!res.ok) {
    throw new Error(`API error: ${res.status}`)
  }

  if (res.status === 204) {
    return {} as T
  }

  return res.json()
}
