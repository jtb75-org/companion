# `.well-known/` — mobile deep-link (Universal / App Links) association

These files let the D.D. mobile app claim `https://app.mydailydignity.com/activate`
so a member's activation email opens the **app** (to the branded set-password screen)
instead of the web page. They must be served from the site root of
`app.mydailydignity.com`.

## Files
- **`apple-app-site-association`** (iOS) — no file extension by design. `appID` =
  `<TeamID>.<bundleID>` = `2NQD86RATH.com.mydailydignity.companion`; claims the
  `/activate` path. Complete — no owner value needed.
- **`assetlinks.json`** (Android) — `package_name` = `com.companionapp`.
  **OWNER TODO:** replace `REPLACE_WITH_RELEASE_SIGNING_SHA256` with the SHA-256
  fingerprint of the **release** signing certificate (Play App Signing key if
  enrolled), e.g. `keytool -list -v -keystore <release.keystore> -alias <alias>` →
  the `SHA256:` line (colon-separated hex). Multiple entries allowed (upload + Play
  signing keys).

### Android App Link verification is DEFERRED (status)
The manifest ships `android:autoVerify="true"`, but Android verification **cannot
succeed with the placeholder SHA-256** — and the real release SHA does not exist
until the app is release-built/signed (tracked separately under mobile build/signing).
**Until then, Android `/activate` links fall back to the browser** (the web page) —
safe and functional, just not the in-app screen. iOS Universal Links are complete
(the AASA carries real values) once the owner enables the capability + hosts this
file. So: **iOS in-app deep link = ready-on-owner-hosting; Android in-app deep link =
deferred to the release-signing milestone (fill the SHA here then).**

## OWNER / INFRA serving requirements (deep links won't verify until these hold)
1. **Serve from the site root**, not the SPA fallback. `GET /.well-known/apple-app-site-association`
   and `GET /.well-known/assetlinks.json` must return THESE files — the SPA history
   fallback (everything → `index.html`) must EXCLUDE `/.well-known/*`, or iOS/Android
   get HTML and verification fails. (Vite copies `public/.well-known/` into `dist/`;
   confirm the static server / ingress rewrite rules don't rewrite `/.well-known/*`.)
2. **Content-Type** — serve both as `application/json`. The AASA file has no
   extension, so the static server must be told to send `application/json` for it
   (Apple is lenient post-iOS 9.3 but this is best practice).
3. **HTTPS, no redirect** — must be reachable at `https://app.mydailydignity.com/.well-known/...`
   with a 200 (no redirect) and a valid TLS cert.
4. **iOS provisioning** — the `applinks:app.mydailydignity.com` Associated Domain must
   also be enabled on the App ID / provisioning profile in the Apple Developer portal
   (the app entitlement alone isn't enough).

After the owner supplies the Android SHA-256 and the serving rules are in place,
validate with Apple's AASA validator + Android's Digital Asset Links API / `adb`
app-links verification.
