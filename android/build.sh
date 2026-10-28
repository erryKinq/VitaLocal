#!/bin/bash
# Builds the signed release APK of the VitaLocal companion into android/dist/.
# Prerequisites (once, needs internet): brew install openjdk@17 && brew install --cask android-commandlinetools
#   sdkmanager --sdk_root="$HOME/Library/Android/sdk" --licenses
#   sdkmanager --sdk_root="$HOME/Library/Android/sdk" "platforms;android-36" "build-tools;36.1.0" "platform-tools"
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
# signing key and its properties live outside the repo (VITALOCAL_SIGNING_DIR to choose where)
if [ "$(uname)" = Darwin ]; then DEFAULT_SUPPORT="$HOME/Library/Application Support/VitaLocal"; else DEFAULT_SUPPORT="$HOME/.config/vitalocal"; fi
SUPPORT="${VITALOCAL_SIGNING_DIR:-$DEFAULT_SUPPORT}"
export VITALOCAL_SIGNING_DIR="$SUPPORT"
if [ -z "${JAVA_HOME:-}" ] && [ -d /opt/homebrew/opt/openjdk@17 ]; then
  export JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home
fi
export JAVA_HOME="${JAVA_HOME:?set JAVA_HOME to a JDK 17}"
if [ "$(uname)" = Darwin ]; then DEFAULT_SDK="$HOME/Library/Android/sdk"; else DEFAULT_SDK="$HOME/Android/Sdk"; fi
export ANDROID_HOME="${ANDROID_HOME:-$DEFAULT_SDK}"
[ -x "$JAVA_HOME/bin/java" ] || { echo "JDK 17 missing (brew install openjdk@17)."; exit 1; }
[ -d "$ANDROID_HOME/platforms" ] || { echo "Android SDK missing under $ANDROID_HOME (see top of this script)."; exit 1; }

# Own signing key outside the repo: the same key lets updates install over the previous version.
PROPS="$SUPPORT/android-signing.properties"
if [ ! -f "$PROPS" ]; then
  mkdir -p "$SUPPORT" && chmod 700 "$SUPPORT"
  PASS=$(openssl rand -hex 24)
  "$JAVA_HOME/bin/keytool" -genkeypair -keystore "$SUPPORT/android-release.jks" -storepass "$PASS" -keypass "$PASS" \
    -alias vitalocal -keyalg EC -groupname secp256r1 -validity 10000 -dname "CN=VitaLocal Companion" >/dev/null
  ( umask 077
    printf 'storeFile=%s\nstorePassword=%s\nkeyAlias=vitalocal\nkeyPassword=%s\n' "$SUPPORT/android-release.jks" "$PASS" "$PASS" > "$PROPS" )
  echo "Signing key created: $SUPPORT/android-release.jks (back it up; without it updates need a reinstall)"
fi

cd "$ROOT"
echo "sdk.dir=$ANDROID_HOME" > local.properties
./gradlew -q testDebugUnitTest assembleRelease
mkdir -p dist
cp app/build/outputs/apk/release/app-release.apk dist/VitaLocal.apk
echo "APK: $ROOT/dist/VitaLocal.apk"
# offer it in the WebUI (Data page) when a server is configured in .env
if grep -qE "^VITALOCAL_PUBLISH_URL=." "$ROOT/../.env" 2>/dev/null || [ -n "${VITALOCAL_PUBLISH_URL:-}" ]; then
  "$ROOT/publish.sh"
fi
