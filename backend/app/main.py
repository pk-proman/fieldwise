import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from . import config, hub, scheduler
from .db import Base, engine
from .migrate import ensure_schema, backfill
from . import models  # noqa: F401  (register tables)
from .routers import auth, attendance, tracking, crm, tasks, visits, leaves, expenses, admin, reports, erp, sales, orders, beats, crmx, docs, comms, intake, people, purchase, ai, field, fieldx, compliance, team, aiq, appdist

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_schema()
    backfill()
    hub.set_loop(asyncio.get_running_loop())
    task = asyncio.create_task(scheduler.loop()) if config.ENABLE_SCHEDULER else None
    yield
    if task:
        task.cancel()


_prod = config.ENV == "production"
app = FastAPI(title="Fieldwise API", version="1.0.0", lifespan=lifespan, docs_url=None if _prod else "/docs", redoc_url=None if _prod else "/redoc", openapi_url=None if _prod else "/openapi.json")
# The native app's web view runs on its own origin (capacitor://localhost, https://localhost) and calls the API with a Bearer token (no cookies).
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_methods=["*"], allow_headers=["Authorization", "Content-Type"], allow_credentials=False, max_age=600)

for r in (auth, attendance, tracking, crm, tasks, visits, leaves, expenses, admin, reports, erp, sales, orders, beats, crmx, docs, comms, intake, people, purchase, ai, field, fieldx, compliance, team, aiq):
    app.include_router(r.router, prefix="/api")
app.include_router(appdist.router)  # /download page and /api/app/version (no prefix: paths are spelled out)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/ready")
def ready():
    """Readiness: the database answers. Point load balancers / uptime monitors here."""
    from sqlalchemy import text
    try:
        with engine.connect() as cx:
            cx.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception:
        logging.getLogger("fieldwise").exception("readiness check failed")
        return JSONResponse({"status": "database unavailable"}, status_code=503)


from . import ratelimit  # noqa: E402
app.middleware("http")(ratelimit.middleware)

import time as _time, uuid as _uuid  # noqa: E402
_access = logging.getLogger("fieldwise.access")


@app.middleware("http")
async def access_log(request: Request, call_next):
    """One log line per API call with a request id (also returned as X-Request-ID) so a user's problem can be found in the logs. No bodies, tokens or query strings are logged."""
    rid, t0 = _uuid.uuid4().hex[:12], _time.time()
    resp = await call_next(request)
    ms = (_time.time() - t0) * 1000
    resp.headers["X-Request-ID"] = rid
    if request.url.path.startswith("/api/") and request.url.path not in ("/api/health", "/api/ready"):
        (_access.warning if ms > 2000 or resp.status_code >= 500 else _access.info)("%s %s %s %d %.0fms", rid, request.method, request.url.path, resp.status_code, ms)
    return resp

CSP = ("default-src 'self'; script-src 'self' 'wasm-unsafe-eval' https://cdnjs.cloudflare.com https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
       "img-src 'self' data: blob: https://*.tile.openstreetmap.org https://*.basemaps.cartocdn.com https://server.arcgisonline.com https://cdnjs.cloudflare.com; "
       "connect-src 'self' ws: wss: https://cdn.jsdelivr.net https://tessdata.projectnaptha.com; worker-src 'self' blob: https://cdn.jsdelivr.net; media-src 'self' blob:; font-src 'self' data:; "
       "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("Content-Security-Policy", CSP)
    resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if request.url.path.startswith("/api/") and "/pdf" not in request.url.path:
        resp.headers.setdefault("Cache-Control", "no-store")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("Permissions-Policy", "geolocation=(self), camera=(self)")
    if request.url.path in ("/sw.js", "/index.html", "/") or request.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    logging.getLogger("fieldwise").exception("Unhandled error on %s", request.url.path)
    return JSONResponse({"detail": "Something went wrong on our side. Please try again."}, status_code=500)


if config.FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=config.FRONTEND_DIR), name="static")

    @app.get("/sw.js", include_in_schema=False)
    def sw():
        return FileResponse(config.FRONTEND_DIR / "sw.js", media_type="application/javascript")

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def manifest():
        return FileResponse(config.FRONTEND_DIR / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = (config.FRONTEND_DIR / path).resolve()
        if path and f.is_file() and config.FRONTEND_DIR.resolve() in f.parents:
            return FileResponse(f)
        return FileResponse(config.FRONTEND_DIR / "index.html")
