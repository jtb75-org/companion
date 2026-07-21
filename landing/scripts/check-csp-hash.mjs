// CSP JSON-LD hash guard for the public landing.
//
// The landing's CSP keeps `script-src` strict (no 'unsafe-inline'). The inline
// <script type="application/ld+json"> Organization block in index.html IS
// matched against script-src by Safari/WebKit, so we allow it via an exact
// content hash pinned in infrastructure/nginx.landing.conf. If the ld+json
// content changes, that hash must change too — otherwise Safari logs a CSP
// "Refused to execute a script" error and the structured data is dropped.
//
// This script runs as `postbuild` (see package.json). It recomputes the hash
// from the freshly built dist/index.html (the exact bytes we serve) and fails
// the build if it does not match the hash in the nginx config — so the hash
// can't silently rot. It changes no files; it only verifies.

import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const distIndex = resolve(here, '..', 'dist', 'index.html')
const nginxConf = resolve(here, '..', '..', 'infrastructure', 'nginx.landing.conf')

function fail(msg) {
  console.error(`\n[check-csp-hash] ${msg}\n`)
  process.exit(1)
}

let html
try {
  html = readFileSync(distIndex, 'utf8')
} catch {
  fail(`could not read ${distIndex} — run \`npm run build\` first.`)
}

const match = html.match(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/)
if (!match) {
  fail('no inline ld+json <script> block found in dist/index.html — if it was intentionally removed, drop the sha256 from script-src in nginx.landing.conf and delete this guard.')
}

// CSP source hashes cover the element's text content EXACTLY (including the
// surrounding whitespace), UTF-8 encoded.
const expected = 'sha256-' + createHash('sha256').update(match[1], 'utf8').digest('base64')

let conf
try {
  conf = readFileSync(nginxConf, 'utf8')
} catch {
  fail(`could not read ${nginxConf}.`)
}

// Every CSP header instance must carry the hash and they must all agree.
const pinned = [...conf.matchAll(/script-src[^;"]*?'(sha256-[A-Za-z0-9+/=]+)'/g)].map((m) => m[1])
if (pinned.length === 0) {
  fail(`script-src in nginx.landing.conf has no sha256 hash, but dist/index.html serves an inline ld+json block that needs ${expected}.`)
}

const mismatched = pinned.filter((h) => h !== expected)
if (mismatched.length > 0) {
  fail(
    `ld+json CSP hash drift.\n` +
    `  built dist/index.html content hashes to: ${expected}\n` +
    `  nginx.landing.conf script-src pins:       ${[...new Set(pinned)].join(', ')}\n` +
    `Update the 'sha256-...' value in ALL script-src directives in ` +
    `infrastructure/nginx.landing.conf to ${expected} (keep the three copies byte-identical).`,
  )
}

console.log(`[check-csp-hash] OK — ld+json hash matches nginx.landing.conf (${expected}).`)
