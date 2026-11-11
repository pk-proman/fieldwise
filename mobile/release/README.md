# Releasing the Android app (direct APK)

**Identity:** package `com.promaninfi.fieldwise` (permanent once on Google Play) · developer name "Prashant Kumar" · support `support@promaninfi.com` · server baked in: `https://fieldwise.promaninfi.com`.

## One-time: the signing key
```bash
cd mobile && ./release/make-keystore.sh
```
Creates `~/.fieldwise-signing/fieldwise-release.jks` and `keystore.properties` (random password) **outside the repository**. Every APK/AAB for the life of the app must be signed with this key, so **back up the whole `~/.fieldwise-signing` folder** to two offline places and your password manager. If it is lost, installed phones cannot be updated in place (users would have to uninstall).
Certificate fingerprint (SHA-256) of the current key: see `apksigner verify --print-certs dist/Fieldwise-<ver>.apk`.

## Every release
1. Edit `release/version.json`: **raise `versionCode` by 1** (Android refuses to install a lower or equal code over an existing install), set `versionName` and `releaseNotes` (one line per bullet). `minVersionCode` is the lowest build still allowed; raise it only to force an update.
2. Build: `cd mobile && ./scripts/build-apk.sh` → `dist/Fieldwise-<versionName>.apk` (signed, target Android 16 / API 36). First run downloads JDK 21 and the Android SDK into `mobile/.tools`.
3. Publish: `./release/publish-apk.sh root@187.127.182.29` (uploads the APK and `latest.json`; the checksum is computed for you).
4. Open `https://fieldwise.promaninfi.com/download`, scan the QR with a phone, install. Phones already on an older build see "Update available" next time they open the app.
The server itself must be deployed once with the `/download` code (`./deploy/deploy.sh ...`); after that, releases never need a server deploy.

## Google Play later
`./scripts/build-apk.sh aab` produces the signed bundle. In Play Console choose Play App Signing and **upload this same key** (or enrol it) so sideloaded installs keep updating in place.
