#!/usr/bin/env bash
#
# Fill the Android Digital Asset Links SHA-256 fingerprint(s) into
# web/public/.well-known/assetlinks.json — the last mobile-cutover step for Android
# App Links (opening https://app.mydailydignity.com/activate?token=... directly in the
# app instead of the browser).
#
# WHERE THE SHA COMES FROM
#   Recommended (Play App Signing): Google re-signs your app, so the fingerprint that
#   matters is the APP SIGNING key, NOT your upload key. Get it from:
#     Play Console -> your app -> Test and release -> App integrity ->
#       App signing key certificate -> "SHA-256 certificate fingerprint"
#   You may ALSO add your UPLOAD key's SHA (harmless, and needed for pre-Play internal
#   installs). Get an upload/release keystore's SHA with:
#     keytool -list -v -keystore <release.keystore> -alias <alias> | grep 'SHA256:'
#   (The colon-separated hex after "SHA256:" is exactly what this script wants.)
#
# USAGE
#   scripts/android-fill-assetlinks-sha.sh <SHA256> [<SHA256> ...]
#   e.g. scripts/android-fill-assetlinks-sha.sh AB:CD:...:EF 12:34:...:90
#
# Then rebuild + deploy the web bundle (push to main) so the file is served, and verify:
#   https://developers.google.com/digital-asset-links/tools/generator
#   curl -s https://app.mydailydignity.com/.well-known/assetlinks.json
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ASSETLINKS="$ROOT/web/public/.well-known/assetlinks.json"

if [ "$#" -lt 1 ]; then
  echo "usage: $0 <SHA256_colon_hex> [<SHA256_colon_hex> ...]" >&2
  echo "  (SHA-256 = 32 bytes, colon-separated hex, e.g. AB:CD:...:EF)" >&2
  exit 2
fi

# Validate every fingerprint BEFORE touching the file.
sha_re='^([0-9A-Fa-f]{2}:){31}[0-9A-Fa-f]{2}$'
fps=()
for fp in "$@"; do
  if ! [[ "$fp" =~ $sha_re ]]; then
    echo "error: '$fp' is not a SHA-256 fingerprint (need 32 colon-separated hex bytes)." >&2
    echo "       Did you paste a SHA-1 (only 20 bytes) or miss some colons?" >&2
    exit 1
  fi
  fps+=("$(printf '%s' "$fp" | tr '[:lower:]' '[:upper:]')")
done

[ -f "$ASSETLINKS" ] || { echo "error: $ASSETLINKS not found" >&2; exit 1; }

# Rewrite the sha256_cert_fingerprints array in place (JSON-safe, preserves structure).
FPS_JSON="$(printf '%s\n' "${fps[@]}" | python3 -c 'import json,sys; print(json.dumps([l.strip() for l in sys.stdin if l.strip()]))')" \
python3 - "$ASSETLINKS" <<'PY'
import json, os, sys
path = sys.argv[1]
fps = json.loads(os.environ["FPS_JSON"])
with open(path) as f:
    data = json.load(f)
n = 0
for entry in data:
    tgt = entry.get("target", {})
    if tgt.get("namespace") == "android_app":
        tgt["sha256_cert_fingerprints"] = fps
        n += 1
if n == 0:
    print("error: no android_app target found in assetlinks.json", file=sys.stderr)
    sys.exit(1)
with open(path, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
print(f"Updated {n} android_app target(s) with {len(fps)} fingerprint(s):")
for fp in fps:
    print(f"  {fp}")
PY

echo
echo "Next: commit, rebuild + deploy the web bundle (push to main), then verify:"
echo "  curl -s https://app.mydailydignity.com/.well-known/assetlinks.json"
