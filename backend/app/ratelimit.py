"""Small in-memory sliding-window rate limiter keyed by client IP and endpoint group. Per process: behind several workers the
effective limit is multiplied, so also set limits at the reverse proxy (see deploy/nginx.conf)."""
import time
from collections import defaultdict, deque
import os
from fastapi import Request
from fastapi.responses import JSONResponse

RULES = [  # (path prefix, method or None, max requests, window seconds, label)
    ("/api/auth/signup", "POST", 5, 3600, "sign-up"),
    ("/api/auth/forgot", "POST", 5, 900, "password reset"),
    ("/api/auth/reset", "POST", 10, 900, "password reset"),
    ("/api/auth/login", "POST", 30, 60, "sign-in"),
    ("/api/auth/activate", "POST", 20, 900, "phone activation"),
    ("/api/intake/", "POST", 1500, 60, "lead intake"),
    ("/api/public/", "GET", 120, 60, "public links"),
    ("/api/", None, 1500, 60, "requests"),
]
_hits: dict = defaultdict(deque)
_last_gc = 0.0


def check(ip: str, path: str, method: str):
    global _last_gc
    now = time.time()
    if now - _last_gc > 300:  # drop idle keys
        for k in [k for k, q in _hits.items() if not q or now - q[-1] > 3700]:
            _hits.pop(k, None)
        _last_gc = now
    for prefix, m, limit, window, label in RULES:
        if path.startswith(prefix) and (m is None or m == method):
            q = _hits[(ip, prefix)]
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                return label, max(1, int(window - (now - q[0])))
            q.append(now)
    return None


async def middleware(request: Request, call_next):
    if request.url.path.startswith("/api/") and os.getenv("RATELIMIT_DISABLE", "0") != "1":
        hit = check(request.client.host if request.client else "?", request.url.path, request.method)
        if hit:
            return JSONResponse({"detail": f"Too many {hit[0]} requests. Please wait {hit[1]} seconds and try again."}, status_code=429, headers={"Retry-After": str(hit[1])})
    return await call_next(request)
