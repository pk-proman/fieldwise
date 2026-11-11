#!/usr/bin/env bash
# One-time: creates the release signing key OUTSIDE the repository (~/.fieldwise-signing) plus mobile/release/keystore.properties (git-ignored).
# The key signs every APK/AAB forever: losing it means installed apps can never update in place. BACK IT UP (two offline copies + password manager).
set -euo pipefail
cd "$(dirname "$0")/.."
export JAVA_HOME="$PWD/.tools/jdk/Contents/Home"; export PATH="$JAVA_HOME/bin:$PATH"
DIR="$HOME/.fieldwise-signing"; KS="$DIR/fieldwise-release.jks"
[ -e "$KS" ] && { echo "Keystore already exists at $KS - not overwriting."; exit 1; }
mkdir -p "$DIR" && chmod 700 "$DIR"
PW="$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-24)"
keytool -genkeypair -v -keystore "$KS" -alias fieldwise -keyalg RSA -keysize 4096 -validity 10950 \
  -storepass "$PW" -keypass "$PW" -dname "CN=Fieldwise, O=Proman Infrastructure Services, C=IN" >/dev/null
chmod 600 "$KS"
printf 'storeFile=%s\nstorePassword=%s\nkeyAlias=fieldwise\nkeyPassword=%s\n' "$KS" "$PW" "$PW" > release/keystore.properties
chmod 600 release/keystore.properties
cp release/keystore.properties "$DIR/keystore.properties"
echo "Created $KS"
echo "BACK UP the whole folder $DIR (the .jks and keystore.properties) to two offline places and your password manager."
keytool -list -keystore "$KS" -storepass "$PW" | sed -n 1,6p
