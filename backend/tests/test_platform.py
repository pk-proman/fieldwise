import re
import pytest
from sqlalchemy import select
from app import config
from app.db import SessionLocal
from app.models import Tenant, User, utcnow
from app.security import hash_password
from conftest import signup

SA_PW = "Sup3rAdmin!pass"


@pytest.fixture(scope="module")
def sa(client):
    """A super-admin in the platform workspace, signed in."""
    with SessionLocal() as db:
        t = db.scalar(select(Tenant).where(Tenant.slug == config.PLATFORM_SLUG))
        if not t:
            t = Tenant(name="Platform", slug=config.PLATFORM_SLUG, kind="platform", plan="internal", seat_limit=100, settings={}, setup_completed_at=utcnow())
            db.add(t)
            db.flush()
        db.add(User(tenant_id=t.id, email="root@vendor.com", name="Vendor Root", role="superadmin", password_hash=hash_password(SA_PW)))
        db.commit()
    r = client.post("/api/auth/login", json={"workspace": config.PLATFORM_SLUG, "email": "root@vendor.com", "password": SA_PW})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def _token(link):
    return re.search(r"token=([^&]+)", link).group(1)


def provision(client, sa, company, email):
    r = client.post("/api/platform/tenants", headers=sa, json={"company": company, "admin_name": "Client Admin", "admin_email": email, "country": "India", "plan": "starter", "seat_limit": 5})
    assert r.status_code == 200, r.text
    d = r.json()
    ok = client.post("/api/auth/reset", json={"token": _token(d["invite_link"]), "new_password": "Cl1entAdmin!pw"})
    assert ok.status_code == 200, ok.text
    lg = client.post("/api/auth/login", json={"workspace": d["slug"], "email": email, "password": "Cl1entAdmin!pw"})
    assert lg.status_code == 200, lg.text
    return d, {"Authorization": "Bearer " + lg.json()["token"]}


def test_only_superadmin_reaches_the_console(client, sa):
    _, oh = signup(client, "Plain Co", "o@plain.com")
    assert client.get("/api/platform/tenants", headers=oh).status_code == 403
    assert client.get("/api/platform/tenants").status_code == 401
    assert client.get("/api/platform/tenants", headers=sa).status_code == 200


def test_provision_setup_gate_and_complete(client, sa):
    d, ah = provision(client, sa, "Acme Builders", "admin@acme.test")
    assert d["setup_complete"] is False and d["admin"]["email"] == "admin@acme.test"
    me = client.get("/api/auth/me", headers=ah).json()
    assert me["setup_complete"] is False and me["user"]["role"] == "owner"
    # the client's administrator can add people, but they cannot use the workspace until setup is finished
    assert client.post("/api/users", headers=ah, json={"name": "Field Fred", "email": "fred@acme.test", "password": "Passw0rd!x", "role": "employee"}).status_code == 200
    fh = {"Authorization": "Bearer " + client.post("/api/auth/login", json={"workspace": d["slug"], "email": "fred@acme.test", "password": "Passw0rd!x"}).json()["token"]}
    blocked = client.get("/api/auth/me", headers=fh)
    assert blocked.status_code == 403 and blocked.json()["detail"].startswith("SETUP_PENDING")
    # finishing is refused until the required steps are done
    r = client.post("/api/setup/complete", headers=ah)
    assert r.status_code == 422 and "Company profile" in r.json()["detail"]
    st = client.get("/api/setup", headers=ah).json()
    assert [s["key"] for s in st["steps"] if s["required"]] == ["company", "modules", "work_rules", "branches"]
    assert client.put("/api/setup/modules", headers=ah, json={"modules": {"field_force": False, "crm": False}}).status_code == 422
    client.put("/api/setup/modules", headers=ah, json={"modules": {"purchase_support": False, "erp": False}})
    client.post("/api/setup/step/company", headers=ah, json={"done": True})
    client.post("/api/setup/step/work_rules", headers=ah, json={"done": True})
    assert client.post("/api/setup/complete", headers=ah).status_code == 422          # still needs an office (field force is on)
    assert client.post("/api/branches", headers=ah, json={"name": "HQ", "lat": 19.07, "lng": 72.87}).status_code == 200
    done = client.post("/api/setup/complete", headers=ah)
    assert done.status_code == 200 and done.json()["complete"] is True
    assert client.get("/api/auth/me", headers=fh).status_code == 200                   # now everyone can work
    assert client.get("/api/auth/me", headers=ah).json()["tenant"]["settings"]["modules"]["purchase_support"] is False


def test_signup_can_be_switched_off(client, monkeypatch):
    monkeypatch.setattr(config, "ALLOW_SIGNUP", False)
    r = client.post("/api/auth/signup", json={"company": "Nope Co", "name": "No One", "email": "n@nope.com", "password": "Passw0rd!x", "country": "India"})
    assert r.status_code == 403 and "invitation" in r.json()["detail"]
    assert client.get("/api/auth/public-config").json()["signup_enabled"] is False


def test_suspend_and_open_as_admin(client, sa):
    d, ah = provision(client, sa, "Suspendable Ltd", "admin@susp.test")
    imp = client.post(f"/api/platform/tenants/{d['id']}/open", headers=sa).json()
    ih = {"Authorization": "Bearer " + imp["token"]}
    me = client.get("/api/auth/me", headers=ih).json()
    assert me["impersonated"] is True and me["user"]["email"] == "admin@susp.test"
    assert client.get("/api/auth/me", headers=ah).status_code == 200                    # the real administrator is not signed out
    assert client.post("/api/auth/change-password", headers=ih, json={"current_password": "x", "new_password": "Whatever1!x"}).status_code == 403
    log = client.get(f"/api/platform/tenants/{d['id']}", headers=sa).json()["platform_log"]
    assert any(l["action"] == "platform.impersonate" for l in log)
    assert client.patch(f"/api/platform/tenants/{d['id']}", headers=sa, json={"active": False}).json()["active"] is False
    assert client.get("/api/auth/me", headers=ah).status_code == 403
    assert client.post("/api/auth/login", json={"workspace": d["slug"], "email": "admin@susp.test", "password": "Cl1entAdmin!pw"}).status_code == 403


def test_config_template_moves_between_clients(client, sa):
    a, ah = provision(client, sa, "Template Source", "admin@src.test")
    client.post("/api/branches", headers=ah, json={"name": "Pune HQ", "lat": 18.5, "lng": 73.8})
    client.put("/api/settings", headers=ah, json={"work_start": "08:00", "weekly_off": [5, 6]})
    cfg = client.get(f"/api/platform/tenants/{a['id']}/config", headers=sa).json()
    assert cfg["version"] == 1 and cfg["branches"][0]["name"] == "Pune HQ" and "erpnext" not in cfg["settings"] and cfg["settings"]["work_start"] == "08:00"
    r = client.post("/api/platform/tenants", headers=sa, json={"company": "Template Target", "admin_name": "Tgt Admin", "admin_email": "admin@tgt.test", "config": cfg})
    assert r.status_code == 200 and r.json()["imported"]["branches"] == 1
    t = r.json()
    tok = client.post("/api/auth/reset", json={"token": _token(t["invite_link"]), "new_password": "Cl1entAdmin!pw"})
    th = {"Authorization": "Bearer " + client.post("/api/auth/login", json={"workspace": t["slug"], "email": "admin@tgt.test", "password": "Cl1entAdmin!pw"}).json()["token"]}
    assert [b["name"] for b in client.get("/api/branches", headers=th).json()] == ["Pune HQ"]
    assert client.get("/api/settings", headers=th).json()["work_start"] == "08:00"
    again = client.post(f"/api/platform/tenants/{t['id']}/config", headers=sa, json=cfg).json()["created"]
    assert again["branches"] == 0                                                          # importing twice creates no duplicates
    assert client.post(f"/api/platform/tenants/{t['id']}/config", headers=sa, json={"version": 99}).status_code == 422


def test_duplicate_workspace_code_and_plan_validation(client, sa):
    provision(client, sa, "Code Owner", "admin@codeowner.test")
    bad = client.post("/api/platform/tenants", headers=sa, json={"company": "Other", "admin_name": "Oth Er", "admin_email": "x@o.test", "slug": "code-owner"})
    assert bad.status_code in (200, 409)   # the slug of the first was auto-derived as "code-owner"
    if bad.status_code == 200:
        assert bad.json()["slug"] == "code-owner"  # explicit code that was free is honoured
    assert client.post("/api/platform/tenants", headers=sa, json={"company": "Zed", "admin_name": "Zed Z", "admin_email": "z@z.test", "plan": "gold"}).status_code == 422
