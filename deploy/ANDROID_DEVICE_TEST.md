# Android device test (before giving the APK to the pilot group)

Install from `https://fieldwise.promaninfi.com/download` exactly as a user would. One tester per phone, a normal working day, a real route.
Record: phone model, Android version, pass/fail, notes. **Minimum set:** one Xiaomi/Redmi (MIUI/HyperOS), one Oppo or Realme (ColorOS), one Vivo (Funtouch/OriginOS), one Samsung, one Pixel or Motorola. Android 11 to 16.

## Setup per phone (the steps on the download page)
1. Install the APK (allow unknown apps, Play Protect "Install anyway"). Open it: it should go straight to the sign-in screen with the server already filled in.
2. Sign in. When asked for location choose **Allow all the time**; allow notifications and camera.
3. Battery: Settings, Apps, Fieldwise, Battery = Unrestricted. Xiaomi/Oppo/Vivo: turn on Autostart and lock the app in recents.

## Tests
| # | Test | Pass when |
|---|---|---|
| 1 | Check in, lock the screen, leave it 8 to 10 hours while moving around | Pings arrive all day: gap between points never over ~10 min; the persistent "Tracking on duty" notification stays |
| 2 | Open the manager's live map and the day route at the end of the day | Route is continuous, not just the first few minutes |
| 3 | Battery drain over the day | Fieldwise under about 8 to 10 percent of the day's usage (Settings, Battery) |
| 4 | Reboot the phone while checked in | Tracking resumes after the user opens the app (note if it needs a tap) |
| 5 | Airplane mode for 20 min, then back online | Offline points upload; no duplicates |
| 6 | Fake-GPS app on (developer option "mock location app") | Pings flagged `mock`, server blocks or flags as configured |
| 7 | Fingerprint / face check-in | Prompt appears; check-in records `device_biometric` |
| 8 | Camera: photo on a visit, PO scan | Opens camera, uploads |
| 9 | Open a PDF (invoice, quotation) | PDF opens in the viewer |
| 10 | Swipe the app away from recents (do not Force stop) | Tracking continues; note per brand |
| 11 | Update prompt: publish a build with a higher versionCode | Phone shows "Update available"; Download opens the browser; installing keeps sign-in |
| 12 | Check out | Tracking stops and the notification disappears |

## Known to verify on this build specifically
This build moved from Android 14 to Android 16 targeting and Capacitor 6 to 8. Watch for: status bar / notch overlap on the top bar (Android 15+ draws edge-to-edge), the back button behaviour, and the foreground-service notification appearing at check-in on Android 14+.
