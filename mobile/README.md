# Fieldwise mobile (Android / iOS)

The Fieldwise web app packaged as a native app with Capacitor. The web files are **bundled inside the app** (so the screens open instantly and offline punches/breadcrumbs queue on the phone). People type their company's Fieldwise address once on the sign-in screen.

| Capability | Why it matters | Where |
|---|---|---|
| **Background GPS** (foreground service on Android, background location on iOS) | The route keeps recording with the screen off | `frontend/js/native.js` → `BackgroundGeolocation` → `/api/tracking/pings` (offline queue included) |
| **Real mock-location flag** (`simulated`) | Browsers can't see fake-GPS apps | sent as `mock: true`; the server blocks/flags it |
| **Fingerprint / face unlock** | Verifiable on-device biometrics for attendance | `verifyBiometric()` → `device_biometric: true` on check-in/out |

## Build an Android APK (no Android Studio needed)
```bash
cd mobile && npm install
./scripts/build-apk.sh debug      # test build -> dist/Fieldwise-debug.apk
./scripts/build-apk.sh            # signed release build -> dist/Fieldwise-<version>.apk (needs the release key)
```
First run downloads JDK 21 and the Android SDK (platform 36) into `mobile/.tools` and **accepts Google's Android SDK licence on your behalf**; delete that folder to remove them.
The app targets Android 16 (API 36), package `com.promaninfi.fieldwise`, and opens on `https://fieldwise.promaninfi.com` (change in `frontend/js/config.js`).
Creating the signing key, version bumps, publishing to the server's `/download` page and the in-app update prompt: see [release/README.md](release/README.md). Real-phone test plan: [../deploy/ANDROID_DEVICE_TEST.md](../deploy/ANDROID_DEVICE_TEST.md).

## Build for iPhone
Needs a Mac with Xcode, CocoaPods and an Apple developer account.
```bash
cd mobile && npm install && npm run sync     # bundles the web app and runs pod install
npm run ios                                  # opens Xcode: choose your team under Signing, run on a device
```
The project is generated and configured (location "always", Face ID, camera strings, location background mode) but **has not been compiled** because this machine has no Xcode. `.github/workflows/mobile.yml` builds both platforms in the cloud if you'd rather not set up a Mac.

## Server settings
- The server must be reachable over **HTTPS** from the phone. CORS already allows the app's origins (`capacitor://localhost`, `https://localhost`); change them with the `CORS_ORIGINS` env var.
- After changing anything under `frontend/`, run `npm run sync` again to refresh the bundled copy.

## On real devices
- Some Android makers (Xiaomi, Oppo, Vivo, Samsung "sleeping apps") kill background services: add Fieldwise to the battery-optimisation allow-list, and choose **Allow all the time** for location when asked.
- Tracking people raises consent and notice duties (e.g. India's DPDP Act, GCC data laws). Tracking already stops at check-out.
