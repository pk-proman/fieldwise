"""Direct APK distribution: /download page (QR + checksum + install help) and /api/app/version for the in-app update prompt.
The release is published by mobile/release/publish-apk.sh as releases/latest.json + the APK; nothing here needs a restart."""
import html
import io
import json
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
import segno
from .. import config

router = APIRouter()


def _latest():
    try:
        d = json.loads((config.RELEASES_DIR / "latest.json").read_text())
        f = (config.RELEASES_DIR / d["apk"]).resolve()
        if config.RELEASES_DIR.resolve() not in f.parents or not f.is_file():
            return None, None
        return d, f
    except (OSError, ValueError, KeyError):
        return None, None


def _base(request: Request) -> str:
    return (config.PUBLIC_URL or str(request.base_url)).rstrip("/")


@router.get("/api/app/version")
def app_version(request: Request):
    """Public (the check runs before sign-in). versionCode is Android's integer build number; below minVersionCode the app must update."""
    d, _ = _latest()
    if not d:
        return JSONResponse({"available": False}, headers={"Cache-Control": "no-store"})
    return JSONResponse({"available": True, "versionCode": d["versionCode"], "versionName": d["versionName"], "minVersionCode": d.get("minVersionCode", 1),
                         "releaseNotes": d.get("releaseNotes", ""), "sha256": d["sha256"], "size": d["size"], "apkUrl": f"{_base(request)}/download/fieldwise"},
                        headers={"Cache-Control": "no-store"})


@router.api_route("/download/fieldwise", methods=["GET", "HEAD"], include_in_schema=False)
def download_apk():
    d, f = _latest()
    if not d:
        raise HTTPException(404, "No app release has been published yet.")
    return FileResponse(f, media_type="application/vnd.android.package-archive", filename=f"Fieldwise-{d['versionName']}.apk", headers={"Cache-Control": "no-store"})


_CSS = """:root{--bg:#f6f8f8;--card:#fff;--ink:#0f1f1d;--mut:#51635f;--line:#dbe4e2;--pri:#0f766e;--pri-ink:#fff;--warn:#fff7e0;--warn-line:#f0d58a}
@media(prefers-color-scheme:dark){:root{--bg:#0c1413;--card:#131f1d;--ink:#e8f1ef;--mut:#9bb0ab;--line:#24383554;--pri:#2dd4bf;--pri-ink:#04201c;--warn:#2a2410;--warn-line:#6b5a1e}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:760px;margin:0 auto;padding:24px 16px 56px}h1{font-size:1.6rem;margin:.2em 0}h2{font-size:1.1rem;margin:1.6em 0 .4em}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px}.row{display:flex;gap:20px;flex-wrap:wrap;align-items:center}
.btn{display:inline-block;background:var(--pri);color:var(--pri-ink);text-decoration:none;font-weight:600;padding:13px 22px;border-radius:10px}
.mut{color:var(--mut)}.small{font-size:.85rem}code{font-family:ui-monospace,Menlo,monospace;font-size:.82rem;word-break:break-all}
.qr{width:160px;height:160px;background:#fff;padding:8px;border-radius:10px}ol,ul{padding-left:1.2em}li{margin:.35em 0}
@media(max-width:600px){.qr,.pc{display:none}}
.note{background:var(--warn);border:1px solid var(--warn-line);border-radius:10px;padding:10px 14px}details{margin:.5em 0}summary{cursor:pointer;font-weight:600}"""


@router.api_route("/download", methods=["GET", "HEAD"], response_class=HTMLResponse, include_in_schema=False)
def download_page(request: Request):
    d, _ = _latest()
    e = html.escape
    sup = e(config.SUPPORT_EMAIL)
    if not d:
        body = f'<div class="card"><p>The Android app has not been published yet. Please check back soon, or write to <a href="mailto:{sup}">{sup}</a>.</p></div>'
    else:
        url = f"{_base(request)}/download/fieldwise"
        qr = io.BytesIO()
        segno.make(url, error="m").save(qr, kind="svg", scale=4, border=1, xmldecl=False, svgns=True, nl=False)
        mb = d["size"] / 1048576
        notes = "".join(f"<li>{e(x)}</li>" for x in str(d.get("releaseNotes", "")).split("\n") if x.strip())
        body = f"""<div class="card row"><div style="flex:1;min-width:230px">
<p class="mut small" style="margin:0">Version {e(str(d['versionName']))} &middot; {mb:.1f} MB &middot; Android 7 or newer</p>
<p style="margin:.7em 0"><a class="btn" href="/download/fieldwise">Download the Android app</a></p>
<p class="mut small pc" style="margin:0">On a computer? Scan the code with your phone's camera.</p></div>
<div class="qr">{qr.getvalue().decode()}</div></div>
<h2>What's in this version</h2><ul>{notes}</ul>
<h2>Install in 4 steps</h2><ol>
<li>Open the downloaded file. If Android asks, allow your browser (or Files app) to <b>install unknown apps</b> &ndash; this is normal for apps shared outside Google Play.</li>
<li>If <b>Play Protect</b> shows a warning, choose <b>More details &rarr; Install anyway</b>.</li>
<li>Open Fieldwise and sign in with your workspace name, email and password.</li>
<li>When asked for location, choose <b>Allow all the time</b>. Without it, your route cannot be recorded when the screen is off.</li></ol>
<div class="note"><b>Keep tracking alive on Xiaomi / Redmi / Oppo / Realme / Vivo / Samsung.</b> These phones stop background apps to save battery. Open Settings &rarr; Apps &rarr; Fieldwise and set Battery to <b>Unrestricted / No restrictions</b>, turn on <b>Autostart</b> (Xiaomi, Oppo, Vivo) and lock the app in the recent-apps list.</div>
<h2>Check the file (optional)</h2><p class="mut small">The SHA-256 checksum of the file you downloaded must match this value. On a computer: <code>shasum -a 256 Fieldwise-{e(str(d['versionName']))}.apk</code></p>
<p><code>{e(d['sha256'])}</code></p>"""
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light dark">
<title>Download Fieldwise for Android</title><style>{_CSS}</style></head><body><main><h1>Fieldwise for Android</h1>
<p class="mut" style="margin-top:0">Attendance, visits and live route tracking for the field team.</p>{body}
<h2>Need help?</h2><p>Write to <a href="mailto:{sup}">{sup}</a>.</p></main></body></html>"""
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})
