/**
 * Session-token storage for the Authentik (self-hosted) login path.
 *
 * The opaque session id returned by POST /auth/login is a bearer secret,
 * equivalent to a password, so it is stored in the device Keychain / Keystore
 * (react-native-keychain), NOT in plain AsyncStorage.
 *
 * Everything else in the app reads the token through this module (via the
 * in-memory cache), so the storage backend is isolated to this one file.
 *
 * This is authentik-mode-only. In Firebase mode none of these functions are
 * called, so the keychain is never touched.
 */
import * as Keychain from 'react-native-keychain'

// Dedicated keychain service so this secret is isolated from anything else.
const KEYCHAIN_SERVICE = 'com.dd.companion.authSession'
// The keychain entry stores username/password pairs; we only need the secret,
// so we use a fixed account label and put the session id in the password field.
const ACCOUNT = 'session'

const backend = {
  async get(): Promise<string | null> {
    try {
      const creds = await Keychain.getGenericPassword({ service: KEYCHAIN_SERVICE })
      // `false` when there is no stored entry.
      return creds ? creds.password : null
    } catch {
      // Keychain reads can throw (locked device, first run, biometric denial).
      // Treat any failure as "no session" so app start never crashes.
      return null
    }
  },
  async set(value: string): Promise<void> {
    await Keychain.setGenericPassword(ACCOUNT, value, { service: KEYCHAIN_SERVICE })
  },
  async remove(): Promise<void> {
    try {
      await Keychain.resetGenericPassword({ service: KEYCHAIN_SERVICE })
    } catch {
      // Best-effort clear; ignore keychain errors on logout.
    }
  },
}

// In-memory cache so the API client can attach the bearer header
// synchronously on every request without an async keychain read per call.
let cachedToken: string | null = null

/**
 * Read the token synchronously from the in-memory cache. Returns null until
 * `loadSessionToken()` has run (on app start) or `persistSessionToken()` has
 * been called. Used by the API client to attach the bearer header.
 */
export function getSessionTokenSync(): string | null {
  return cachedToken
}

/**
 * Restore the token from the keychain into memory. Call once on app start.
 * A missing or failed keychain read resolves to null (unauthenticated) and
 * never throws.
 */
export async function loadSessionToken(): Promise<string | null> {
  cachedToken = await backend.get()
  return cachedToken
}

/** Store the token after a successful login (keychain first, then memory). */
export async function persistSessionToken(token: string): Promise<void> {
  // Write to the keychain BEFORE caching in memory: if the write throws, we do
  // not want the in-memory cache holding a sid that was never persisted.
  await backend.set(token)
  cachedToken = token
}

/** Clear the token on logout (memory + keychain). */
export async function clearSessionToken(): Promise<void> {
  cachedToken = null
  await backend.remove()
}
