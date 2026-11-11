# Android release plan: direct APK now, Google Play next

Why the native app matters: the PWA can only track while it is open. All-day tracking with the screen off needs the Android app (background location service).
Both phases need the same technical groundwork, so we do it once. Time estimates assume decisions below are made quickly.

## 0. Decisions (made 2026-11-11)
| Decision | Answer |
|---|---|
| **Package name** | `com.promaninfi.fieldwise` (permanent once on Play) |
| **Publisher name** | Prashant Kumar (shown to users on Play) |
| **Play account type** | Organization account intended; Proman's D-U-N-S number is **applied for, not yet issued**. Verification cannot start until it arrives. |
| **Support contact** | support@promaninfi.com (create this mailbox before launch; it is on the download page and the Play listing) |
| **Default server** | `https://fieldwise.promaninfi.com` is built into the app (changeable under "Server" on the sign-in screen) |

Note: the Play listing shows the developer name from the verified account. For an organization account Google shows the legal organization name, so "Prashant Kumar" appears only on a personal account. See Phase 2.

## Phase 1: direct APK download (target: 1-2 weeks of work + device testing)
**Status 2026-11-11:** steps 1-3, 5 and 6 are built (signed release APK, target API 36, `/download`, in-app update prompt). Step 4 (real-phone testing, see ANDROID_DEVICE_TEST.md) and step 7 (rollout) remain. How to release: mobile/release/README.md.

1. **Upgrade the Android project** to current Capacitor and Android tooling, and target **Android 16 (API 36)** (the app targets API 34 today). Google requires API 36 for new apps and updates since 31 Aug 2026, so doing it now avoids rework for Play.
2. **Create the release signing key once.** Keep it offline in two places plus a password manager. Never commit it. Losing it means users cannot update the app in place.
3. **Build a signed release APK** (not the debug build): version code/name scheme, default server baked in, cleartext traffic off, permissions audited.
4. **Test on real phones** (at least Samsung, Xiaomi/Redmi, Oppo/Vivo, Pixel or Motorola; Android 11-16): background tracking with the screen locked for a full day, battery use, reboot, airplane-mode recovery, mock-location detection, fingerprint, camera/PO scan, PDF download.
5. **Download page on the app's own domain** (e.g. `/download`): current version, release notes, QR code, SHA-256 of the file for verification, and step-by-step install help (allow installs from this source; "Allow all the time" location; battery optimisation off; Xiaomi/Oppo/Vivo autostart).
6. **In-app update check:** the app asks the server for the latest and minimum supported version and prompts users to update (blocking if it is too old). This replaces Play's auto-update for sideloaded installs.
7. **Roll out** to the pilot group first (WhatsApp/email link or your MDM), then everyone.

Known cost of sideloading: Android shows "unknown source" and Play Protect prompts. These are normal, and they disappear on Play.

## Phase 2: Google Play (run in parallel; 4-6 weeks end to end, mostly waiting)
**A. Account (start now: it is the long pole)**
1. Confirm Proman's **D-U-N-S number** (free from Dun & Bradstreet; reported at 5 to 30 days if you need to request one).
2. Create the **Play Console organization account** (one-time US$25 fee) using a company role email, not a personal one. Complete Google's identity and business verification.

**B. Policy material (we prepare, Proman publishes)**
3. **Privacy policy** on a public URL, covering location tracking (when, why, who sees it, retention), photos, contacts and deletion rights.
4. **Account-deletion page** URL (Google requires one for apps with sign-in). The app already has erase-user and delete-workspace; we add the public page.
5. **Data safety form:** precise and background location, name/email/phone, photos, device information, expense data; encrypted in transit; deletion available.
6. **Content rating** questionnaire, target audience 18+, category Business, no ads.

**C. The hard part: background location approval**
7. Add a **prominent in-app disclosure screen** shown before the system location prompt: what is collected, that it is collected in the background, why, and "Accept / Not now".
8. Fill in the **Permissions Declaration Form** (Play Console → App content → Sensitive app permissions) explaining why background location is core to field-force attendance and visit verification.
9. Record a short **demo video** (guides suggest about 30 seconds) showing the disclosure, the permission prompt and the feature that needs it. Reviewers reject marketing videos and unclear disclosures.
10. Declare the location **foreground service** type in the manifest and in the console; keep the persistent "tracking is on" notification.
11. Expect questions or a first rejection; plan a resubmission buffer of a week or two.

**D. Build and listing**
12. Build a signed **Android App Bundle (.aab)**. Enrol in **Play App Signing**, and choose to **upload the same key** used for the sideloaded APK so existing installs update in place.
13. Store listing: name, short (80-char) and full descriptions, 512×512 icon (ready), **1024×500 feature graphic**, at least 2 phone screenshots (we aim for 6-8), contact email, privacy policy URL.
14. **App access:** reviewers need a demo login. Provide a demo workspace with sample data, MFA off, and written steps.

**E. Release path**
15. **Internal testing** (up to 100 testers, available within minutes) → fix issues.
16. **Closed testing** with the pilot group (optional for organization accounts, still recommended).
17. **Production with staged rollout** (for example 10% → 50% → 100%) while watching Play's crash and battery reports.
18. After launch: yearly target-API bump (every August), policy updates, crash monitoring, review replies.

## iPhone (later)
Needs a Mac with Xcode and an Apple Developer account (US$99/year). Until then iPhone users use the PWA (no background tracking).

## Risks
| Risk | Mitigation |
|---|---|
| Background-location rejection | Clear disclosure, video, honest purpose text; foreground notification; retention and privacy policy in place |
| D-U-N-S / verification delays | Start immediately; ship Phase 1 APK meanwhile |
| Lost signing key | Two offline copies; enrol in Play App Signing |
| OEM battery killers (Xiaomi, Oppo, Vivo) | In-app checklist screen and training; test on those brands |
| API-level or policy changes | Yearly upgrade budget; keep Capacitor current |
