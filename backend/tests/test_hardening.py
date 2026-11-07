import base64
import io
import json
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timedelta, date
import pytest
from conftest import signup, login
from test_app import mk_user, mk_product


def test_password_policy_and_reset_flow(client):
    r = client.post("/api/auth/signup", json={"company": "Weak Pw Co", "name": "Owner", "email": "o@weak.com", "password": "password", "country": "India"})
    assert r.status_code == 422 and "letters and numbers" in r.json()["detail"]
    assert client.post("/api/auth/signup", json={"company": "Weak Pw Co", "name": "Owner", "email": "o@weak.com", "password": "Password123", "country": "India"}).status_code == 422  # common
    ws, h = signup(client, "Reset Co", "o@reset.com")
    assert client.post("/api/users", headers=h, json={"name": "Weak User", "email": "w@reset.com", "password": "12345678"}).status_code == 422
    # forgot never reveals whether the account exists
    a = client.post("/api/auth/forgot", json={"workspace": ws, "email": "o@reset.com"}).json()
    b = client.post("/api/auth/forgot", json={"workspace": ws, "email": "nobody@reset.com"}).json()
    assert a == b
    from app.security import make_reset_token
    from app.db import SessionLocal
    from app.models import User, Tenant
    from sqlalchemy import select
    db = SessionLocal()
    u = db.scalar(select(User).join(Tenant, Tenant.id == User.tenant_id).where(User.email == "o@reset.com", Tenant.slug == ws))
    tok = make_reset_token(u.id, u.tenant_id, u.password_hash)
    expired = make_reset_token(u.id, u.tenant_id, u.password_hash, ttl_min=-5)
    db.close()
    assert client.post("/api/auth/reset", json={"token": expired, "new_password": "BrandNew9pass"}).status_code == 400
    assert client.post("/api/auth/reset", json={"token": tok + "x", "new_password": "BrandNew9pass"}).status_code == 400
    assert client.post("/api/auth/reset", json={"token": tok, "new_password": "short"}).status_code == 422
    assert client.post("/api/auth/reset", json={"token": tok, "new_password": "BrandNew9pass"}).status_code == 200
    assert client.post("/api/auth/reset", json={"token": tok, "new_password": "AnotherNew9pass"}).status_code == 400   # single use
    assert client.get("/api/auth/me", headers=h).status_code == 401                                                       # old sessions are signed out
    assert client.post("/api/auth/login", json={"workspace": ws, "email": "o@reset.com", "password": "BrandNew9pass"}).status_code == 200


def test_consent_gates_tracking(client):
    ws, h = signup(client, "Consent Co", "o@consent.com")
    client.post("/api/branches", headers=h, json={"name": "HQ", "lat": 19.11, "lng": 72.87, "radius_m": 300})
    emp = mk_user(client, h, "e@consent.com", "employee")
    client.put("/api/settings", headers=h, json={"require_tracking_consent": True})
    eh = login(client, ws, "e@consent.com")
    me = client.get("/api/auth/me", headers=eh).json()
    assert me["consent_required"] and "location" in me["tracking_policy"]
    r = client.post("/api/attendance/check-in", headers=eh, json={"lat": 19.11, "lng": 72.87})
    assert r.status_code == 428 and r.json()["detail"] == "CONSENT_REQUIRED"
    assert client.post("/api/auth/consent", headers=eh).status_code == 200
    assert not client.get("/api/auth/me", headers=eh).json()["consent_required"]
    assert client.post("/api/attendance/check-in", headers=eh, json={"lat": 19.11, "lng": 72.87}).status_code == 200
    # a new policy version asks again
    client.put("/api/settings", headers=h, json={"tracking_policy_version": "2"})
    assert client.get("/api/auth/me", headers=eh).json()["consent_required"]
    assert any(a["action"] == "consent.accept" for a in client.get("/api/compliance/audit", headers=h).json())


def test_uploads_cannot_carry_active_content(client):
    ws, h = signup(client, "Upload Co", "o@upload.com")
    base = {"day": date.today().isoformat(), "category": "food", "amount": "100"}
    for name, data in (("r.html", b"<script>alert(1)</script>"), ("r.svg", b"<svg onload=alert(1)/>"), ("r.exe", b"MZ")):
        r = client.post("/api/expenses", headers=h, data=base, files={"receipt": (name, data, "text/html")})
        assert r.status_code == 422, name
    svg = "data:image/svg+xml;base64," + base64.b64encode(b"<svg xmlns='http://www.w3.org/2000/svg' onload='alert(1)'/>").decode()
    assert client.post("/api/company/files", headers=h, json={"kind": "logo", "data": svg}).status_code == 422
    ok = client.post("/api/expenses", headers=h, data=base, files={"receipt": ("r.png", b"\x89PNG\r\n\x1a\nxx", "image/png")})
    assert ok.status_code == 200 and ok.json()["receipt"].endswith(".jpg") or ok.json()["receipt"].endswith(".png")
    path = ok.json()["receipt"]
    f = client.get(f"/api/files/{path}?t={h['Authorization'][7:]}")
    assert f.status_code == 200 and "sandbox" in f.headers["content-security-policy"] and f.headers["x-content-type-options"] == "nosniff"


def test_security_headers(client):
    r = client.get("/api/health")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"] and r.headers["cache-control"] == "no-store" and r.headers["x-frame-options"] == "DENY"
    assert "script-src 'self'" in client.get("/").headers["content-security-policy"]
    assert "unsafe-inline" not in client.get("/").headers["content-security-policy"].split("script-src")[1].split(";")[0]


def test_ssrf_guard(monkeypatch):
    from app import netguard
    monkeypatch.delenv("NETGUARD_DISABLE", raising=False)
    monkeypatch.delenv("ALLOW_PRIVATE_URLS", raising=False)
    for bad in ("http://example.com", "https://127.0.0.1/x", "https://localhost:8000", "https://169.254.169.254/latest/meta-data", "https://10.0.0.5", "https://192.168.1.10", "https://[::1]/", "https://user:pw@example.com", "ftp://x.com", "https://0.0.0.0"):
        with pytest.raises(ValueError):
            netguard.check_url(bad)
    assert netguard.check_url("https://8.8.8.8/api") == "https://8.8.8.8/api"
    monkeypatch.setenv("ALLOW_PRIVATE_URLS", "1")       # self-hosted ERPNext on a private network: explicit opt-in, metadata/loopback still refused
    assert netguard.check_url("https://10.1.2.3") == "https://10.1.2.3"
    with pytest.raises(ValueError):
        netguard.check_url("https://169.254.169.254")


def test_internal_urls_rejected_in_settings(client, monkeypatch):
    ws, h = signup(client, "Ssrf Co", "o@ssrf.com")
    monkeypatch.delenv("NETGUARD_DISABLE", raising=False)
    r = client.put("/api/erp/config", headers=h, json={"enabled": True, "url": "https://169.254.169.254", "api_key": "k", "api_secret": "s"})
    assert r.status_code == 422 and "private or internal" in r.json()["detail"]
    r = client.put("/api/integrations/channels", headers=h, json={"gateways": {"sms": {"enabled": True, "url": "https://127.0.0.1:9000/hook"}}})
    assert r.status_code == 422
    assert client.post("/api/erp/sync/not-a-domain", headers=h).status_code == 404


def test_rate_limits(client, monkeypatch):
    from app import ratelimit
    monkeypatch.setenv("RATELIMIT_DISABLE", "0")
    ratelimit._hits.clear()
    codes = [client.post("/api/auth/login", json={"workspace": "nope", "email": "a@b.com", "password": "x"}).status_code for _ in range(33)]
    assert codes.count(429) >= 3 and codes[0] == 401
    r = client.post("/api/auth/login", json={"workspace": "nope", "email": "a@b.com", "password": "x"})
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    ratelimit._hits.clear()
    s = [client.post("/api/auth/signup", json={"company": f"Spam {i}", "name": "Spammer", "email": f"s{i}@spam.com", "password": "Spammer12345"}).status_code for i in range(7)]
    assert s.count(429) == 2
    ratelimit._hits.clear()


def test_cross_workspace_ids_are_rejected(client):
    wsa, ha = signup(client, "Refs Victim", "o@refsv.com")
    pa = mk_product(client, ha, "RV-1")
    la = client.post("/api/crm/leads", headers=ha, json={"name": "A lead", "phone": "+919111100001", "product_id": pa}).json()
    ca = client.post("/api/crm/customers", headers=ha, json={"name": "A cust", "phone": "+919111100002", "lat": 19.1, "lng": 72.8}).json()
    ua = client.get("/api/auth/me", headers=ha).json()["user"]
    br = client.post("/api/branches", headers=ha, json={"name": "A branch", "lat": 1, "lng": 2}).json()
    wsb, hb = signup(client, "Refs Attacker", "o@refsb.com")
    pb = mk_product(client, hb, "RB-1")
    assert client.post("/api/crm/leads", headers=hb, json={"name": "B", "phone": "+919111100003", "product_id": pa}).status_code == 422      # foreign product
    assert client.post("/api/crm/leads", headers=hb, json={"name": "B", "phone": "+919111100004", "product_id": pb, "owner_id": ua["id"]}).status_code == 422
    assert client.post("/api/crm/leads", headers=hb, json={"name": "B", "phone": "+919111100005", "product_id": pb, "observers": [ua["id"]]}).status_code == 422
    lb = client.post("/api/crm/leads", headers=hb, json={"name": "B", "phone": "+919111100006", "product_id": pb}).json()
    assert client.patch(f"/api/crm/leads/{lb['id']}", headers=hb, json={"owner_id": ua["id"]}).status_code == 422
    assert client.post("/api/crm/contacts", headers=hb, json={"name": "C", "customer_id": ca["id"]}).status_code == 422
    assert client.post("/api/crm/customers", headers=hb, json={"name": "Kid", "parent_id": ca["id"]}).status_code == 422
    assert client.post("/api/users", headers=hb, json={"name": "Foreign Branch", "email": "fb@refsb.com", "password": "Passw0rd!x", "branch_id": br["id"]}).status_code == 422
    uid = client.post("/api/users", headers=hb, json={"name": "Local", "email": "l@refsb.com", "password": "Passw0rd!x"}).json()["id"]
    assert client.patch(f"/api/users/{uid}", headers=hb, json={"shift_id": 1, "department_id": 9999}).status_code == 422
    assert client.post("/api/tasks", headers=hb, json={"title": "t", "customer_id": ca["id"], "lat": 1.0, "lng": 1.0}).status_code == 422       # even with its own coordinates
    assert client.post("/api/calls", headers=hb, json={"number": "9111100001", "lead_id": la["id"]}).status_code == 404
    assert client.post("/api/events", headers=hb, json={"name": "Expo", "start_date": date.today().isoformat(), "end_date": date.today().isoformat(), "owner_id": ua["id"]}).status_code == 422
    # a customer can't be its own ancestor
    c1 = client.post("/api/crm/customers", headers=hb, json={"name": "Parent"}).json()
    c2 = client.post("/api/crm/customers", headers=hb, json={"name": "Child", "parent_id": c1["id"]}).json()
    assert client.patch(f"/api/crm/customers/{c1['id']}", headers=hb, json={"parent_id": c2["id"]}).status_code == 422


def test_export_erase_delete_audit_retention(client):
    ws, h = signup(client, "Gdpr Co", "o@gdpr.com")
    pid = mk_product(client, h, "G-1")
    client.put("/api/erp/config", headers=h, json={"enabled": False, "url": "https://erp.example", "api_key": "KEY", "api_secret": "TOPSECRET"})
    client.post("/api/crm/leads", headers=h, json={"name": "Export Me", "phone": "+919222200001", "product_id": pid})
    emp = mk_user(client, h, "e@gdpr.com", "employee")
    eh = login(client, ws, "e@gdpr.com")
    now = datetime.utcnow()
    client.post("/api/tracking/pings", headers=eh, json=[{"ts": (now - timedelta(minutes=i)).isoformat(), "lat": 19.1, "lng": 72.8, "network": "online", "gps_on": True, "mock": False} for i in range(5)])
    assert client.get("/api/compliance/export", headers=eh).status_code == 403
    z = client.get("/api/compliance/export", headers=h)
    zf = zipfile.ZipFile(io.BytesIO(z.content))
    names = zf.namelist()
    assert "leads.json" in names and "users.json" in names and "location_pings.json" in names
    blob = b"".join(zf.read(n) for n in names)
    assert b"Export Me" in blob and b"TOPSECRET" not in blob and b"password_hash" not in blob and b"mfa_secret" not in blob
    # retention: old breadcrumbs go, recent ones stay
    from app.db import SessionLocal
    from app.models import LocationPing, Tenant
    from app.routers.compliance import purge_old_data
    from sqlalchemy import select, func
    db = SessionLocal()
    t = db.scalar(select(Tenant).where(Tenant.slug == ws))
    db.add(LocationPing(tenant_id=t.id, user_id=emp["id"], ts=now - timedelta(days=400), lat=1, lng=1))
    db.commit()
    n_before = db.scalar(select(func.count()).where(LocationPing.tenant_id == t.id))
    purge_old_data(db)
    assert db.scalar(select(func.count()).where(LocationPing.tenant_id == t.id)) == n_before - 1
    db.close()
    # erase one person: identity and trail gone, account unusable
    assert client.post(f"/api/compliance/erase-user/{emp['id']}", headers=h).json()["erased"]
    assert client.get("/api/auth/me", headers=eh).status_code == 401
    db = SessionLocal()
    assert db.scalar(select(func.count()).where(LocationPing.tenant_id == t.id, LocationPing.user_id == emp["id"])) == 0
    db.close()
    acts = [a["action"] for a in client.get("/api/compliance/audit", headers=h).json()]
    assert {"user.create", "workspace.export", "user.erase", "integration.erp_config"} <= set(acts)
    assert client.get("/api/compliance/audit", headers=eh).status_code in (401, 403)
    # delete the whole workspace: needs the exact name and the owner's password
    assert client.post("/api/compliance/delete-workspace", headers=h, json={"confirm_slug": "wrong", "password": "Passw0rd!x"}).status_code == 422
    assert client.post("/api/compliance/delete-workspace", headers=h, json={"confirm_slug": ws, "password": "nope"}).status_code == 403
    assert client.post("/api/compliance/delete-workspace", headers=h, json={"confirm_slug": ws, "password": "Passw0rd!x"}).json()["deleted"]
    assert client.post("/api/auth/login", json={"workspace": ws, "email": "o@gdpr.com", "password": "Passw0rd!x"}).status_code == 401
    db = SessionLocal()
    assert db.scalar(select(func.count()).where(LocationPing.tenant_id == t.id)) == 0
    db.close()


def test_scheduler_has_one_leader():
    from app import scheduler
    from app.db import SessionLocal
    from app.models import JobLock
    db = SessionLocal()
    db.query(JobLock).delete()          # a lease left by an earlier run must not matter
    db.commit()
    db.close()
    assert scheduler.acquire_leader() is True
    assert scheduler.acquire_leader() is True                       # same process keeps the lease
    me = scheduler._HOLDER
    scheduler._HOLDER = "another-instance:1"
    try:
        assert scheduler.acquire_leader() is False                  # a second process must not run jobs
    finally:
        scheduler._HOLDER = me


def test_production_refuses_default_secret():
    code = "import os; os.environ['ENV']='production'; os.environ.pop('SECRET_KEY', None); import app.config"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(__import__("pathlib").Path(__file__).resolve().parent.parent))
    assert r.returncode != 0 and "SECRET_KEY" in r.stderr
    ok = subprocess.run([sys.executable, "-c", "import os; os.environ['ENV']='production'; os.environ['SECRET_KEY']='x'*40; import app.config; print('fine')"], capture_output=True, text=True,
                        cwd=str(__import__("pathlib").Path(__file__).resolve().parent.parent))
    assert ok.returncode == 0 and "fine" in ok.stdout


def test_production_never_reveals_visit_otp(client, monkeypatch):
    from app import config
    ws, h = signup(client, "Otp Co", "o@otp.com")
    cust = client.post("/api/crm/customers", headers=h, json={"name": "Cust", "phone": "+919333312345"}).json()
    dev = client.post("/api/visits/start", headers=h, json={"customer_id": cust["id"]}).json()
    assert dev["otp_required"] and "dev_otp" in dev                   # development: shown so the flow can be tried
    client.post(f"/api/visits/{dev['visit']['id']}/verify-otp", headers=h, json={"otp": dev["dev_otp"]})
    client.post(f"/api/visits/{dev['visit']['id']}/end", headers=h, json={"mom": "ok"})
    monkeypatch.setattr(config, "ENV", "production")
    r = client.post("/api/visits/start", headers=h, json={"customer_id": cust["id"]})
    assert r.status_code == 503 and "SMS gateway" in r.json()["detail"] and "dev_otp" not in r.text
    assert client.get("/api/visits/active", headers=h).json() is None   # the half-created visit was not kept
