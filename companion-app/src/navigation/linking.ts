/**
 * Deep-link parsing for the account-activation universal / app link.
 *
 * A member's email contains a link to the SAME URL the web page uses:
 *
 *   https://app.mydailydignity.com/activate?token=<t>
 *
 * When the app is installed, iOS Associated Domains / Android App Links open
 * the app directly on this URL; otherwise it falls back to the web page.
 *
 * This module only PARSES the URL — routing lives in AppNavigator, gated on
 * AUTH_PROVIDER === 'authentik'. Parsing is deliberately defensive: a malformed
 * or unrelated link returns null (no throw, no crash), and the caller shows the
 * calm "this link did not work" state.
 */
export const ACTIVATE_HOST = 'app.mydailydignity.com'
export const ACTIVATE_PATH = '/activate'

/**
 * Extract the `token` query param from an activation link, or null.
 *
 * Returns null for: a null/empty input, a URL that is not our host+path, or a
 * link with no usable `token`. We hand-parse rather than rely on the RN `URL`
 * polyfill (its `searchParams` support has historically been incomplete).
 */
export function parseActivationToken(url: string | null | undefined): string | null {
  if (!url) return null

  const queryStart = url.indexOf('?')
  const base = queryStart >= 0 ? url.slice(0, queryStart) : url

  // Must be our activation link: right host AND right path.
  if (!base.includes(ACTIVATE_HOST)) return null
  if (!base.includes(ACTIVATE_PATH)) return null
  if (queryStart < 0) return null

  const query = url.slice(queryStart + 1)
  for (const pair of query.split('&')) {
    const eq = pair.indexOf('=')
    if (eq < 0) continue
    const key = pair.slice(0, eq)
    const value = pair.slice(eq + 1)
    if (key === 'token' && value) {
      try {
        return decodeURIComponent(value)
      } catch {
        return value
      }
    }
  }
  return null
}
