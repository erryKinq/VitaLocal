#!/bin/bash
# Uploads android/dist/VitaLocal.apk to the VitaLocal server, where the WebUI offers it for download
# (Data page). Server and token come from the repo's .env: VITALOCAL_PUBLISH_URL (e.g.
# http://192.168.1.10:8000) and VITALOCAL_API_TOKEN. build.sh calls this when the URL is set.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
ENV="$ROOT/../.env"
val() { grep -E "^$1=" "$ENV" 2>/dev/null | tail -1 | cut -d= -f2-; }
URL="${VITALOCAL_PUBLISH_URL:-$(val VITALOCAL_PUBLISH_URL)}"
TOKEN="${VITALOCAL_API_TOKEN:-$(val VITALOCAL_API_TOKEN)}"
APK="$ROOT/dist/VitaLocal.apk"
[ -n "$URL" ] || { echo "VITALOCAL_PUBLISH_URL not set (.env)."; exit 1; }
[ -n "$TOKEN" ] || { echo "VITALOCAL_API_TOKEN not set (.env)."; exit 1; }
[ -f "$APK" ] || { echo "$APK missing, run ./build.sh first."; exit 1; }
GRADLE="$ROOT/app/build.gradle.kts"
VERSION=$(sed -nE 's/.*versionName = "([^"]+)".*/\1/p' "$GRADLE")
CODE=$(sed -nE 's/.*versionCode = ([0-9]+).*/\1/p' "$GRADLE")
# the token goes in a header read from stdin, so it never shows up in the process list
printf 'header = "Authorization: Bearer %s"\n' "$TOKEN" | curl -fsS -K - -X PUT "${URL%/}/api/v1/app/android" \
  -F "file=@$APK;type=application/vnd.android.package-archive" -F "version=$VERSION" -F "version_code=$CODE" >/dev/null
echo "Published VitaLocal $VERSION ($CODE) to $URL"
