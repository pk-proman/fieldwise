#!/usr/bin/env bash
# Builds a debug APK without needing Android Studio. Installs a JDK and the Android SDK into mobile/.tools (delete that folder to remove them).
# Running this accepts Google's Android SDK licence on your behalf: https://developer.android.com/studio/terms
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"; TOOLS="$ROOT/.tools"; mkdir -p "$TOOLS"
ARCH="$(uname -m)"; [ "$ARCH" = "arm64" ] && JARCH="aarch64" || JARCH="x64"

if [ ! -d "$TOOLS/jdk" ]; then
  echo "==> Downloading JDK 17 ($JARCH)"
  curl -fL --retry 3 -o "$TOOLS/jdk.tar.gz" "https://api.adoptium.net/v3/binary/latest/17/ga/mac/$JARCH/jdk/hotspot/normal/eclipse"
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
if [ ! -d "$ANDROID_HOME/platforms/android-34" ] || [ ! -d "$ANDROID_HOME/build-tools/34.0.0" ]; then
  echo "==> Accepting SDK licences and installing platform 34 + build tools"
  yes | SDKM --licenses >/dev/null || true
  SDKM "platform-tools" "platforms;android-34" "build-tools;34.0.0"
fi

echo "sdk.dir=$ANDROID_HOME" > "$ROOT/android/local.properties"
echo "==> Bundling the web app and syncing"
node scripts/build-www.mjs && npx cap sync android
echo "==> Building the debug APK"
cd android && ./gradlew --no-daemon assembleDebug
APK="$ROOT/android/app/build/outputs/apk/debug/app-debug.apk"
mkdir -p "$ROOT/dist" && cp "$APK" "$ROOT/dist/Fieldwise-debug.apk"
echo "==> Done: $ROOT/dist/Fieldwise-debug.apk"
