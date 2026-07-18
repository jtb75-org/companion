/**
 * Deep-link parsing for the account-activation universal / app link.
 *
 * A member's email contains a link to the SAME URL the web page uses:
 *
 *   https://app.mydailydignity.com/activate?token=<t>          (activation)
 *   https://app.mydailydignity.com/activate?token=<t>&reset=1  (password reset)
 *
 * Both links open the SAME screen and redeem through the SAME endpoint. The
 * `reset=1` marker only swaps the copy from activation-flavored ("make a
 * password to start using D.D.") to reset-flavored ("set a new password"), so a
 * member resetting an existing password isn't greeted as brand new.
 *
 * When the app is installed, iOS Associated Domains / Android App Links open
 * the app directly on this URL; otherwise it falls back to the web page.
 *
 * This module only PARSES the URL — routing lives in AppNavigator. Parsing is
 * deliberately defensive: a malformed
 * or unrelated link returns null (no throw, no crash), and the caller shows the
 * calm "this link did not work" state.
 */
export const ACTIVATE_HOST = 'app.mydailydignity.com'
export const ACTIVATE_PATH = '/activate'

/** A parsed /activate link: the token, plus whether it is a password reset. */
export interface ActivationLink {
  token: string
  /** True only for `reset=1`. Copy-only marker — it never changes what we call. */
  reset: boolean
}

/**
 * Parse an activation / reset link into {token, reset}, or null.
 *
 * Returns null for: a null/empty input, a URL that is not our host+path, or a
 * link with no usable `token`. We hand-parse rather than rely on the RN `URL`
 * polyfill (its `searchParams` support has historically been incomplete).
 *
 * `reset` is a COSMETIC flag: it selects copy only. It is never sent anywhere and
 * grants nothing, so a tampered `reset=1` can at worst show reset wording on an
 * activation link. The token remains the only thing that carries authority, and
 * the backend is its sole judge.
 */
export function parseActivationLink(url: string | null | undefined): ActivationLink | null {
  if (!url) return null

  const queryStart = url.indexOf('?')
  const base = queryStart >= 0 ? url.slice(0, queryStart) : url

  // Must be our activation link: EXACT host + path (not a substring match, so a
  // crafted `app.mydailydignity.com.evil.com/activate` can't slip through — belt-
  // and-suspenders; the OS only delivers the OS-verified domain here anyway).
  const m = base.match(/^https?:\/\/([^/]+)(\/[^?]*)?$/i)
  if (!m) return null
  const host = m[1].toLowerCase()
  const path = m[2] || ''
  if (host !== ACTIVATE_HOST) return null
  if (path !== ACTIVATE_PATH && !path.startsWith(`${ACTIVATE_PATH}/`)) return null
  if (queryStart < 0) return null

  let token: string | null = null
  let reset = false

  const query = url.slice(queryStart + 1)
  for (const pair of query.split('&')) {
    const eq = pair.indexOf('=')
    if (eq < 0) continue
    const key = pair.slice(0, eq)
    const value = pair.slice(eq + 1)
    if (key === 'token' && value && token === null) {
      try {
        token = decodeURIComponent(value)
      } catch {
        token = value
      }
    } else if (key === 'reset' && value === '1') {
      reset = true
    }
  }

  return token ? { token, reset } : null
}

/**
 * Extract just the `token` from an activation link, or null.
 * Thin wrapper over `parseActivationLink` for callers that ignore the flavor.
 */
export function parseActivationToken(url: string | null | undefined): string | null {
  return parseActivationLink(url)?.token ?? null
}
