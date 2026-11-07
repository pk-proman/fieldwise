"""Tenant isolation sweep: a signed-in owner of workspace B must not be able to read or change anything in other workspaces,
whatever endpoint and id they try. Runs last so the shared test database already holds many other workspaces."""
import hashlib
import json
import re
from conftest import signup
from test_app import mk_product


def _dump_foreign(tenant_id):
    from app.db import SessionLocal, Base
    from sqlalchemy import text
    db = SessionLocal()
    h = hashlib.sha256()
    n = 0
    try:
        for t in sorted(Base.metadata.sorted_tables, key=lambda x: x.name):
            if "tenant_id" not in t.c:
                continue
            rows = db.execute(text(f'SELECT * FROM {t.name} WHERE tenant_id != :t ORDER BY id'), {"t": tenant_id}).all()
            n += len(rows)
            for r in rows:
                h.update(repr(tuple(r)).encode())
    finally:
        db.close()
    return h.hexdigest(), n


def _routes(client):
    from app.main import app
    out = []
    for path, ops in app.openapi()["paths"].items():
        for m in ops:
            if m.upper() in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                out.append((m.upper(), path))
    return out


def test_cross_tenant_sweep(client):
    ws, h = signup(client, "Isolation Victim Co", "o@victim.com")
    pid = mk_product(client, h, "V-1")
    client.post("/api/crm/leads", headers=h, json={"name": "Victim", "phone": "+919000012345", "product_id": pid})
    ws2, hb = signup(client, "Isolation Attacker Co", "o@attacker.com")
    me = client.get("/api/auth/me", headers=hb).json()
    btid = me["tenant"]["id"]
    before = _dump_foreign(btid)
    assert before[1] > 50, "the sweep needs a database with other workspaces in it"
    routes = _routes(client)
    skip = re.compile(r"/(auth/logout|auth/login|auth/signup|ws/|intake/|public/|health|files/|erp/webhook)")
    leaks, hits = [], 0
    for method, path in routes:
        if skip.search(path) or "{" not in path:
            continue
        for i in (1, 2, 3, 5, 8, 13, 21, 34):
            url = re.sub(r"\{[^}]*:?path\}", "x", path)
            url = re.sub(r"\{[^}]+\}", str(i), url)
            r = client.request(method, url, headers=hb, json={} if method in ("POST", "PUT", "PATCH") else None)
            hits += 1
            if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
                txt = r.text
                for m in re.finditer(r'"tenant_id":\s*(\d+)', txt):
                    if int(m.group(1)) != btid:
                        leaks.append((method, url, r.status_code))
                        break
            if r.status_code >= 500:
                leaks.append((method, url, "5xx"))
    assert hits > 300
    assert not leaks, f"cross-tenant exposure or server errors: {leaks[:10]}"
    after = _dump_foreign(btid)
    assert after == before, "another workspace's data changed while signed in as a different workspace"
