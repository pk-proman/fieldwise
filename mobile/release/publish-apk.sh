#!/usr/bin/env bash
# Publishes a built release APK to the server so /download and the in-app update prompt pick it up. No restart needed.
#   ./release/publish-apk.sh root@187.127.182.29 [--dir /opt/fieldwise] [--min-code N]
# Reads versionCode/versionName/releaseNotes from release/version.json and the APK from dist/Fieldwise-<versionName>.apk.
# --min-code N  makes every install older than build N show a blocking "Update required" screen (leave out for a normal, dismissible prompt).
set -euo pipefail
cd "$(dirname "$0")/.."
TARGET=""; DIR="/opt/fieldwise"; MIN=""
while [ $# -gt 0 ]; do case "$1" in --dir) DIR="$2"; shift 2;; --min-code) MIN="$2"; shift 2;; -*) echo "unknown option $1"; exit 1;; *) TARGET="$1"; shift;; esac; done
[ -n "$TARGET" ] || { echo "usage: $0 user@host [--dir /opt/fieldwise] [--min-code N]"; exit 1; }
VER="$(node -p "require('./release/version.json').versionName")"; APK="dist/Fieldwise-$VER.apk"
[ -f "$APK" ] || { echo "Missing $APK - run ./scripts/build-apk.sh release first."; exit 1; }
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
SHA="$(shasum -a 256 "$APK" | cut -d' ' -f1)"; SIZE="$(stat -f%z "$APK")"
APK="$APK" SHA="$SHA" SIZE="$SIZE" MIN="$MIN" node -e '
  const v=require("./release/version.json"), fs=require("fs");
  fs.writeFileSync(process.argv[1], JSON.stringify({versionCode:v.versionCode, versionName:v.versionName, minVersionCode: process.env.MIN?+process.env.MIN:(v.minVersionCode||1),
    releaseNotes:v.releaseNotes||"", apk:"Fieldwise-"+v.versionName+".apk", sha256:process.env.SHA, size:+process.env.SIZE, published:new Date().toISOString()}, null, 2));' "$TMP/latest.json"
echo "Publishing Fieldwise $VER (build $(node -p "require('./release/version.json').versionCode")) sha256 $SHA to $TARGET:$DIR/releases"
ssh "$TARGET" "mkdir -p $DIR/releases"
scp -q "$APK" "$TARGET:$DIR/releases/Fieldwise-$VER.apk"        # APK first, then the pointer, so /download never points at a missing file
scp -q "$TMP/latest.json" "$TARGET:$DIR/releases/latest.json"
echo "Done. Check https://fieldwise.promaninfi.com/download and https://fieldwise.promaninfi.com/api/app/version"
