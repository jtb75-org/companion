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
  async set(value: string): Promise<boolean> {
    // Returns whether the durable write succeeded. Keychain WRITES can throw on the iOS
    // Simulator and some locked-device states (reads/removes already swallow this). We do
    // NOT rethrow: failing the whole login because the secret couldn't be persisted is
    // worse than a session that simply won't survive an app restart. The caller keeps the
    // token in the in-memory cache either way.
    try {
      await Keychain.setGenericPassword(ACCOUNT, value, { service: KEYCHAIN_SERVICE })
      return true
    } catch {
      return false
    }
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

/** Store the token after a successful login (keychain if possible, always memory). */
export async function persistSessionToken(token: string): Promise<void> {
  // Cache in memory unconditionally so the bearer is usable for THIS app run even when
  // the durable Keychain write fails (e.g. the iOS Simulator) — otherwise login itself
  // throws and the user is stuck on "Something went wrong" despite a valid session. A
  // failed persist only means the session won't survive an app restart.
  const persisted = await backend.set(token)
  cachedToken = token
  if (!persisted) {
    console.warn(
      '[sessionToken] Keychain write failed; session held in memory only (will not survive restart)',
    )
  }
}

/** Clear the token on logout (memory + keychain). */
export async function clearSessionToken(): Promise<void> {
  cachedToken = null
  await backend.remove()
}
