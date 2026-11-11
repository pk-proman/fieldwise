#!/usr/bin/env bash
# Builds the Android app without needing Android Studio.
#   ./scripts/build-apk.sh            signed RELEASE apk  -> dist/Fieldwise-<version>.apk   (needs the release key: release/make-keystore.sh, once)
#   ./scripts/build-apk.sh debug      debug apk           -> dist/Fieldwise-debug.apk
#   ./scripts/build-apk.sh aab        signed bundle for Google Play -> dist/Fieldwise-<version>.aab
# Installs JDK 21 and the Android SDK (platform 36) into mobile/.tools on first run (delete that folder to remove them).
# Running this accepts Google's Android SDK licence on your behalf: https://developer.android.com/studio/terms
set -euo pipefail
cd "$(dirname "$0")/.."
KIND="${1:-release}"
ROOT="$(pwd)"; TOOLS="$ROOT/.tools"; mkdir -p "$TOOLS"
ARCH="$(uname -m)"; [ "$ARCH" = "arm64" ] && JARCH="aarch64" || JARCH="x64"

JDK_OK=0; [ -x "$TOOLS/jdk/Contents/Home/bin/java" ] && "$TOOLS/jdk/Contents/Home/bin/java" -version 2>&1 | grep -q '"21\.' && JDK_OK=1
if [ "$JDK_OK" = 0 ]; then
  echo "==> Downloading JDK 21 ($JARCH)"
  rm -rf "$TOOLS/jdk"
  curl -fL --retry 3 -o "$TOOLS/jdk.tar.gz" "https://api.adoptium.net/v3/binary/latest/21/ga/mac/$JARCH/jdk/hotspot/normal/eclipse"
  mkdir -p "$TOOLS/jdk-extract" && tar -xzf "$TOOLS/jdk.tar.gz" -C "$TOOLS/jdk-extract"
  mv "$TOOLS/jdk-extract/$(ls "$TOOLS/jdk-extract" | head -1)" "$TOOLS/jdk" && rm -rf "$TOOLS/jdk-extract" "$TOOLS/jdk.tar.gz"
fi
export JAVA_HOME="$TOOLS/jdk/Contents/Home"; export PATH="$JAVA_HOME/bin:$PATH"

export ANDROID_HOME="$TOOLS/android-sdk"; export ANDROID_SDK_ROOT="$ANDROID_HOME"
if [ ! -d "$ANDROID_HOME/cmdline-tools/latest/lib" ]; then
  echo "==> Downloading Android command-line tools"
  mkdir -p "$ANDROID_HOME/cmdline-tools"
  curl -fL --retry 3 -o "$TOOLS/cmdline.zip" "https://dl.google.com/android/repository/commandlinetools-mac-11076708_latest.zip"
  unzip -q -o "$TOOLS/cmdline.zip" -d "$ANDROID_HOME/cmdline-tools" && mv "$ANDROID_HOME/cmdline-tools/cmdline-tools" "$ANDROID_HOME/cmdline-tools/latest" && rm "$TOOLS/cmdline.zip"
fi
# Call the Java entry point directly: the sdkmanager shell wrapper breaks on folder names containing spaces.
SDKM() { java -Dcom.android.sdklib.toolsdir="$ANDROID_HOME/cmdline-tools/latest" -classpath "$ANDROID_HOME/cmdline-tools/latest/lib/*" com.android.sdklib.tool.sdkmanager.SdkManagerCli --sdk_root="$ANDROID_HOME" "$@"; }
if [ ! -d "$ANDROID_HOME/platforms/android-36" ] || [ ! -d "$ANDROID_HOME/build-tools/36.0.0" ]; then
  echo "==> Accepting SDK licences and installing platform 36 + build tools"
  yes | SDKM --licenses >/dev/null || true
  SDKM "platform-tools" "platforms;android-36" "build-tools;36.0.0"
fi

echo "sdk.dir=$ANDROID_HOME" > "$ROOT/android/local.properties"
echo "==> Bundling the web app and syncing"
node scripts/build-www.mjs && npx cap sync android
VER="$(node -p "require('./release/version.json').versionName")"
cd android; mkdir -p "$ROOT/dist"
case "$KIND" in
  debug)   ./gradlew --no-daemon assembleDebug;   OUT="$ROOT/dist/Fieldwise-debug.apk"; cp app/build/outputs/apk/debug/app-debug.apk "$OUT";;
  release) [ -f "$ROOT/release/keystore.properties" ] || { echo "No release key yet: run mobile/release/make-keystore.sh once."; exit 1; }
           ./gradlew --no-daemon assembleRelease; OUT="$ROOT/dist/Fieldwise-$VER.apk"; cp app/build/outputs/apk/release/app-release.apk "$OUT";;
  aab)     [ -f "$ROOT/release/keystore.properties" ] || { echo "No release key yet: run mobile/release/make-keystore.sh once."; exit 1; }
           ./gradlew --no-daemon bundleRelease;   OUT="$ROOT/dist/Fieldwise-$VER.aab"; cp app/build/outputs/bundle/release/app-release.aab "$OUT";;
  *) echo "usage: $0 [release|debug|aab]"; exit 1;;
esac
echo "==> Done: $OUT"
[ "$KIND" = debug ] || shasum -a 256 "$OUT"
