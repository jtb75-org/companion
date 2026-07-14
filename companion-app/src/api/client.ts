import auth from '@react-native-firebase/auth'
import { AUTH_PROVIDER } from '../auth/authConfig'
import { getSessionTokenSync } from '../auth/sessionToken'

// Self-hosted prod backend (staging Cloud Run is retired). Point dev at a
// local/LAN backend here if you run one.
export const API_BASE = __DEV__
  ? 'https://api.mydailydignity.com'
  : 'https://api.mydailydignity.com'

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  }

  if (AUTH_PROVIDER === 'authentik') {
    // Self-hosted BFF: attach the opaque session token as a non-ambient bearer.
    const sessionToken = getSessionTokenSync()
    if (sessionToken) {
      headers.Authorization = `Bearer ${sessionToken}`
    }
  } else {
    // Firebase (default, live path) — unchanged behavior.
    const user = auth().currentUser
    if (user) {
      const token = await user.getIdToken()
      headers.Authorization = `Bearer ${token}`
    }
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
