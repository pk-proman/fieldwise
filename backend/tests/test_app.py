import json, base64, re
from datetime import datetime, timedelta, timezone, date
import httpx
from conftest import signup, login


def mk_product(client, h, code="P-1", price=1000):
    r = client.post("/api/crm/products", headers=h, json={"code": code, "name": "Prod " + code, "price": price})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def mk_user(client, h, email, role="employee", manager_id=None, name=None):
    r = client.post("/api/users", headers=h, json={"name": name or email.split("@")[0].title() + " X", "email": email, "password": "Passw0rd!x", "role": role, "manager_id": manager_id})
    assert r.status_code == 200, r.text
    return r.json()


def test_auth_single_device_and_tenant_isolation(client):
    ws, h = signup(client, "Alpha Co", "a@alpha.com")
    ws2, h2 = signup(client, "Beta Co", "b@beta.com")
    assert ws != ws2
    first = login(client, ws, "a@alpha.com")
    assert client.get("/api/auth/me", headers=first).status_code == 200
    second = login(client, ws, "a@alpha.com")          # second device
    assert client.get("/api/auth/me", headers=first).status_code == 401  # first revoked
    assert client.get("/api/auth/me", headers=second).status_code == 200
    # wrong password
    assert client.post("/api/auth/login", json={"workspace": ws, "email": "a@alpha.com", "password": "nope"}).status_code == 401
    # isolation
    pid = mk_product(client, second)
    lead = client.post("/api/crm/leads", headers=second, json={"name": "Secret Lead", "product_id": pid, "phone": "+919000000001"}).json()
    assert client.get(f"/api/crm/leads/{lead['id']}", headers=h2).status_code == 404
    assert client.get("/api/crm/leads", headers=h2).json()["total"] == 0
    assert client.patch(f"/api/crm/leads/{lead['id']}", headers=h2, json={"name": "hacked"}).status_code == 404


def test_roles_and_hierarchy(client):
    ws, h = signup(client, "Gamma Co", "o@gamma.com")
    mgr = mk_user(client, h, "m@gamma.com", "manager")
    emp = mk_user(client, h, "e@gamma.com", "employee", mgr["id"])
    eh = login(client, ws, "e@gamma.com")
    assert client.post("/api/users", headers=eh, json={"name": "Zed", "email": "z@g.com", "password": "Passw0rd!x"}).status_code == 403
    assert client.patch(f"/api/users/{mgr['id']}", headers=h, json={"manager_id": emp["id"]}).status_code == 422  # loop
    mh = login(client, ws, "m@gamma.com")
    assert [u["id"] for u in client.get("/api/users", headers=mh).json()].count(emp["id"]) == 1
    assert emp["id"] not in [u["id"] for u in client.get("/api/users", headers=eh).json() if u["id"] != emp["id"]]


def test_attendance_geofence_tracking_fuel(client):
    ws, h = signup(client, "Delta Co", "o@delta.com")
    b = client.post("/api/branches", headers=h, json={"name": "HQ", "lat": 19.1136, "lng": 72.8697, "radius_m": 200}).json()
    mgr = mk_user(client, h, "m@delta.com", "manager")
    mk_user(client, h, "e@delta.com", "employee", mgr["id"])
    eh = login(client, ws, "e@delta.com")
    far = client.post("/api/attendance/check-in", headers=eh, json={"lat": 19.2, "lng": 72.9})
    assert far.status_code == 403 and "m from HQ" in far.json()["detail"]
    assert client.post("/api/attendance/check-in", headers=eh, json={"lat": 19.1136, "lng": 72.8697, "mock": True}).status_code == 403
    ok = client.post("/api/attendance/check-in", headers=eh, json={"lat": 19.1137, "lng": 72.8698, "device_biometric": True,
                                                                     "client_ts": (datetime.now(timezone.utc) - timedelta(minutes=70)).replace(tzinfo=None).isoformat()})
    assert ok.status_code == 200 and ok.json()["check_in_method"] == "fingerprint"
    assert client.post("/api/attendance/check-in", headers=eh, json={"lat": 19.1137, "lng": 72.8698}).status_code == 409
    mh = login(client, ws, "m@delta.com")
    assert any(n["title"] == "Mock location detected" for n in client.get("/api/notifications", headers=mh).json()["items"])  # manager alerted
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    pings = [{"ts": (now - timedelta(minutes=60 - i * 10)).isoformat(), "lat": 19.1137 + i * 0.01, "lng": 72.8698, "battery": 80 - i, "network": "online", "gps_on": True} for i in range(6)]
    assert client.post("/api/tracking/pings", headers=eh, json=pings).json()["accepted"] == 6
    assert client.post("/api/tracking/pings", headers=eh, json=pings).json()["accepted"] == 0  # idempotent offline replay
    trail = client.get("/api/tracking/trail", headers=eh).json()
    assert 5 < trail["stats"]["distance_km"] < 6
    out = client.post("/api/attendance/check-out", headers=eh, json={"lat": 19.1137, "lng": 72.8698, "override_reason": "client site"})
    assert out.status_code == 200, out.text
    assert out.json()["total_distance_km"] > 5
    fuel = client.post("/api/expenses/fuel-auto", headers=eh, json={})
    assert fuel.status_code == 200 and fuel.json()["amount"] == round(out.json()["total_distance_km"] * 6, 2)
    assert client.post("/api/expenses/fuel-auto", headers=eh, json={}).status_code == 409
    live = client.get("/api/tracking/live", headers=mh).json()
    assert len(live) == 1


def test_leaves_and_expenses_approval(client):
    ws, h = signup(client, "Eps Co", "o@eps.com")
    mgr = mk_user(client, h, "m@eps.com", "manager")
    emp = mk_user(client, h, "e@eps.com", "employee", mgr["id"])
    other = mk_user(client, h, "x@eps.com", "employee")
    eh, mh = login(client, ws, "e@eps.com"), login(client, ws, "m@eps.com")
    t = client.get("/api/leaves/types", headers=eh).json()[0]
    start = date.today() + timedelta(days=10)
    while start.weekday() == 6:
        start += timedelta(days=1)
    body = {"leave_type_id": t["id"], "from_day": start.isoformat(), "to_day": start.isoformat(), "reason": "family"}
    r = client.post("/api/leaves", headers=eh, json=body)
    assert r.status_code == 200, r.text
    assert client.post("/api/leaves", headers=eh, json=body).status_code == 409
    assert client.post(f"/api/leaves/{r.json()['id']}/decide", headers=eh, json={"decision": "approved"}).status_code == 403
    assert client.post(f"/api/leaves/{r.json()['id']}/decide", headers=mh, json={"decision": "approved"}).status_code == 200
    bal = {b["name"]: b for b in client.get("/api/leaves/balance", headers=eh).json()}
    assert bal[t["name"]]["used"] == 1
    big = {**body, "from_day": (start + timedelta(days=20)).isoformat(), "to_day": (start + timedelta(days=60)).isoformat()}
    assert client.post("/api/leaves", headers=eh, json=big).status_code == 422  # exceeds balance
    # expenses
    ids = []
    for who in (eh, login(client, ws, "x@eps.com")):
        e = client.post("/api/expenses", headers=who, data={"day": date.today().isoformat(), "category": "food", "amount": "250", "description": "lunch"},
                        files={"receipt": ("r.png", b"not-really-an-image", "image/png")})
        assert e.status_code == 200, e.text
        ids.append(e.json()["id"])
    res = client.post("/api/expenses/decide", headers=mh, json={"ids": ids, "decision": "approved"}).json()
    assert res["updated"] == [ids[0]] and res["skipped"] == [ids[1]]  # manager can't approve outside team
    assert client.post("/api/expenses/decide", headers=eh, json={"ids": ids, "decision": "approved"}).status_code == 403
    assert client.post("/api/expenses", headers=eh, data={"day": date.today().isoformat(), "category": "food", "amount": "-5"}).status_code == 422


def test_crm_flow_recycle_custom_fields_ocr(client):
    ws, h = signup(client, "Zeta Co", "o@zeta.com")
    assert client.post("/api/custom-fields", headers=h, json={"entity": "lead", "label": "Budget Code", "type": "text", "required": True}).status_code == 200
    pid = mk_product(client, h)
    assert client.post("/api/crm/leads", headers=h, json={"name": "No custom", "product_id": pid, "phone": "+919000000002"}).status_code == 422
    nop = client.post("/api/crm/leads", headers=h, json={"name": "No product", "custom": {"budget_code": "B1"}, "phone": "+919000000003"})
    assert nop.status_code == 422 and "Product is required" in nop.json()["detail"]
    lead = client.post("/api/crm/leads", headers=h, json={"name": "Ravi", "company": "Apex Ltd", "phone": "+919800000001", "custom": {"budget_code": "B1"}, "product_id": pid})
    assert lead.status_code == 200, lead.text
    lid = lead.json()["id"]
    assert client.patch(f"/api/crm/leads/{lid}", headers=h, json={"status": "qualified"}).status_code == 422  # gate: must qualify
    assert client.patch(f"/api/crm/leads/{lid}", headers=h, json={"status": "bogus"}).status_code == 422
    q = client.post(f"/api/crm/leads/{lid}/qualify", headers=h, json={"budget": 2, "authority": 1, "need": 2, "timeline": 1}).json()
    assert q["qualified"] and q["lead"]["status"] == "qualified"
    assert client.post(f"/api/crm/leads/{lid}/convert", headers=h).status_code == 422  # gate: needs a quotation
    qt = client.post("/api/quotations", headers=h, json={"lead_id": lid, "items": [{"product_id": pid, "qty": 2}]})
    assert qt.status_code == 200 and qt.json()["number"] == "QTN-0001" and qt.json()["total"] == 2360.0
    conv = client.post(f"/api/crm/leads/{lid}/convert", headers=h).json()
    assert conv["customer"]["name"] == "Apex Ltd"
    assert client.post(f"/api/crm/leads/{lid}/convert", headers=h).status_code == 409
    tl = client.get(f"/api/crm/leads/{lid}/timeline", headers=h).json()
    assert len(tl) >= 4
    assert client.delete(f"/api/crm/leads/{lid}", headers=h).json()["recoverable_days"] == 90
    assert client.get(f"/api/crm/leads/{lid}", headers=h).status_code == 404
    assert any(b["id"] == lid for b in client.get("/api/recycle-bin", headers=h).json())
    assert client.post(f"/api/recycle-bin/lead/{lid}/restore", headers=h).status_code == 200
    assert client.get(f"/api/crm/leads/{lid}", headers=h).status_code == 200
    csv = client.get("/api/crm/leads?fmt=csv", headers=h)
    assert "Ravi" in csv.text
    card = client.post("/api/crm/ocr/parse-card", headers=h, json={"text": "Suresh Nair\nSenior Sales Manager\nGulf Steel Industries LLC\n+971 50 123 4567\nsuresh@gulfsteel.ae\nwww.gulfsteel.ae"}).json()
    assert card["email"] == "suresh@gulfsteel.ae" and card["name"] == "Suresh Nair" and "Manager" in card["designation"] and "Steel" in card["company"]
    # rename a field keeps data
    f = client.get("/api/custom-fields?entity=lead", headers=h).json()[0]
    client.patch(f"/api/custom-fields/{f['id']}", headers=h, json={"label": "Cost Centre"})
    assert client.get(f"/api/crm/leads/{lid}", headers=h).json()["custom"]["budget_code"] == "B1"


def test_tasks_geofence_repeat_visits_otp_routes(client):
    ws, h = signup(client, "Eta Co", "o@eta.com")
    cust = client.post("/api/crm/customers", headers=h, json={"name": "Site A", "phone": "+919811111111", "lat": 19.10, "lng": 72.85}).json()
    c2 = client.post("/api/crm/customers", headers=h, json={"name": "Site B", "lat": 19.20, "lng": 72.95}).json()
    c3 = client.post("/api/crm/customers", headers=h, json={"name": "Site C", "lat": 19.11, "lng": 72.86}).json()
    t = client.post("/api/tasks", headers=h, json={"title": "Deliver samples", "customer_id": cust["id"], "repeat": "daily", "due_at": datetime.utcnow().isoformat()}).json()
    assert t["geofence_m"] == 150 and t["lat"] == 19.10  # inherited from customer
    assert client.patch(f"/api/tasks/{t['id']}", headers=h, json={"status": "done", "lat": 19.5, "lng": 72.0}).status_code == 403
    done = client.patch(f"/api/tasks/{t['id']}", headers=h, json={"status": "done", "lat": 19.10, "lng": 72.85})
    assert done.status_code == 200
    open_tasks = client.get("/api/tasks?status=open", headers=h).json()
    assert any(x["title"] == "Deliver samples" for x in open_tasks)  # repeat spawned next
    up = client.post("/api/tasks/bulk-upload", headers=h, files={"file": ("t.csv", b"title,priority,due\nA,high,2030-01-01\n,low,\nB,weird,\n", "text/csv")}).json()
    assert up["created"] == 1 and len(up["errors"]) == 2
    # visit OTP
    v = client.post("/api/visits/start", headers=h, json={"customer_id": cust["id"], "lat": 19.10, "lng": 72.85}).json()
    assert v["otp_required"] and "dev_otp" in v
    assert client.post(f"/api/visits/{v['visit']['id']}/end", headers=h, json={"mom": "x"}).status_code == 403
    assert client.post(f"/api/visits/{v['visit']['id']}/verify-otp", headers=h, json={"otp": "000000"}).status_code == 400
    assert client.post(f"/api/visits/{v['visit']['id']}/verify-otp", headers=h, json={"otp": v["dev_otp"]}).json()["otp_verified"]
    end = client.post(f"/api/visits/{v['visit']['id']}/end", headers=h, json={"mom": "Agreed on pricing.", "email_to": "boss@x.com"})
    assert end.status_code == 200 and "boss@x.com" in end.json()["mom_emailed_to"]
    # route optimisation: A(19.10,72.85) -> C(19.11,72.86) -> B(19.2,72.95)
    r = client.post("/api/routes/optimize", headers=h, json={"start_lat": 19.09, "start_lng": 72.84, "customer_ids": [c2["id"], cust["id"], c3["id"]]}).json()
    assert [s["name"] for s in r["stops"]] == ["Site A", "Site C", "Site B"] and r["distance_km"] > 0


def test_erpnext_sync_with_mock_server(client):
    from app.integrations import erpnext as erp
    from app.db import SessionLocal
    from app.models import Tenant
    ws, h = signup(client, "Theta Co", "o@theta.com")
    tid = client.get("/api/auth/me", headers=h).json()["tenant"]["id"]
    sent = []

    def handler(req: httpx.Request):
        assert req.headers["authorization"] == "token K:S"
        p = req.url.path
        if req.method == "GET" and p == "/api/resource/Employee":
            return httpx.Response(200, json={"data": [
                {"name": "HR-EMP-1", "employee_name": "Boss Man", "user_id": "boss@theta.com", "designation": "Sales Manager", "reports_to": None, "branch": "Mumbai", "cell_number": "1"},
                {"name": "HR-EMP-2", "employee_name": "Rep One", "user_id": "rep@theta.com", "designation": "Executive", "reports_to": "HR-EMP-1", "branch": "Mumbai"}]})
        if req.method == "GET" and p == "/api/resource/Item":
            return httpx.Response(200, json={"data": [{"name": "I-1", "item_code": "I-1", "item_name": "Widget", "item_group": "G", "stock_uom": "Nos", "standard_rate": 99}]})
        if req.method == "GET":
            return httpx.Response(200, json={"data": []})
        if req.method == "POST":
            body = json.loads(req.content)
            sent.append((p, body))
            return httpx.Response(200, json={"data": {"name": "NEW-1"}})
        return httpx.Response(200, json={"data": {"name": "X"}})

    mt = httpx.MockTransport(handler)
    assert client.put("/api/erp/config", headers=h, json={"enabled": True, "url": "https://erp.example", "api_key": "K", "api_secret": "S", "company": "Theta"}).status_code == 200
    cfg = client.get("/api/erp/config", headers=h).json()
    assert "api_secret" not in cfg and cfg["api_secret_set"] and cfg["webhook_secret"]
    assert erp.run_sync(tid, "employees", mt) == {"employees": 2}
    users = {u["email"]: u for u in client.get("/api/users", headers=h).json()}
    assert users["rep@theta.com"]["manager_id"] == users["boss@theta.com"]["id"] and users["boss@theta.com"]["role"] == "manager"
    assert erp.run_sync(tid, "products", mt) == {"products": 1}
    assert client.get("/api/crm/products", headers=h).json()["items"][0]["price"] == 99
    pid = mk_product(client, h, "P-9")
    client.post("/api/crm/leads", headers=h, json={"name": "Pushed Lead", "status": "contacted", "product_id": pid, "phone": "+919000000004"})
    res = erp.run_sync(tid, "crm", mt)
    assert res["crm_push"] >= 1 and any(p == "/api/resource/Lead" and b["status"] == "Open" for p, b in sent)
    # attendance push needs a completed attendance for rep
    from app.models import Attendance, User
    from sqlalchemy import select
    db = SessionLocal()
    rep = db.scalar(select(User).where(User.email == "rep@theta.com"))
    db.add(Attendance(tenant_id=tid, user_id=rep.id, day=date.today(), check_in_at=datetime.utcnow(), check_out_at=datetime.utcnow(), check_in_lat=1, check_in_lng=2))
    db.commit(); db.close()
    assert erp.run_sync(tid, "attendance", mt) == {"attendance": 1}
    assert [b["log_type"] for p, b in sent if p == "/api/resource/Employee Checkin"] == ["IN", "OUT"]
    # webhook
    secret = cfg["webhook_secret"]
    assert client.post(f"/api/erp/webhook/{ws}", headers={"X-Webhook-Secret": "bad"}).status_code == 401
    assert client.post(f"/api/erp/webhook/{ws}", headers={"X-Webhook-Secret": secret}).status_code == 200
    assert any(l["ok"] for l in client.get("/api/erp/logs", headers=h).json())


def test_dashboard_and_seat_limit(client):
    ws, h = signup(client, "Iota Co", "o@iota.com")
    d = client.get("/api/reports/dashboard", headers=h).json()
    assert d["headcount"] >= 0 and len(d["trend"]) == 7
    for i in range(9):
        assert client.post("/api/users", headers=h, json={"name": f"U{i} Name", "email": f"u{i}@iota.com", "password": "Passw0rd!x"}).status_code == 200
    assert client.post("/api/users", headers=h, json={"name": "Over Limit", "email": "o2@iota.com", "password": "Passw0rd!x"}).status_code == 402


def test_pipeline_quotation_erp_followups_meetings_reports(client, monkeypatch):
    from app.integrations import erpnext as erp
    from app.routers import sales
    ws, h = signup(client, "Kappa Co", "o@kappa.com")
    mgr = mk_user(client, h, "m@kappa.com", "manager")
    mk_user(client, h, "e@kappa.com", "employee", mgr["id"])
    eh, mh = login(client, ws, "e@kappa.com"), login(client, ws, "m@kappa.com")
    pid = mk_product(client, h, "K-1", 5000)
    posted = []

    def handler(req):
        if req.method == "POST":
            body = json.loads(req.content); posted.append((req.url.path, body))
            return httpx.Response(200, json={"data": {"name": "SAL-QTN-2026-00012" if "Quotation" in req.url.path else "CRM-LEAD-7", "grand_total": 11800, "status": "Draft"}})
        if req.method == "GET":
            return httpx.Response(200, json={"data": {"status": "Open", "grand_total": 11800}})
        return httpx.Response(200, json={"data": {"name": "X"}})
    cfg = {"enabled": True, "url": "https://erp.example", "api_key": "K", "api_secret": "S", "company": "Kappa"}
    client.put("/api/erp/config", headers=h, json=cfg)
    real = sales._erp_client
    monkeypatch.setattr(sales, "_erp_client", lambda ctx: erp.ERPNextClient(erp._cfg(ctx.tenant), httpx.MockTransport(handler)))
    lead = client.post("/api/crm/leads", headers=eh, json={"name": "Hot Lead", "company": "Hot Co", "value": 100000, "product_id": pid, "phone": "+919000000005"}).json()
    lid = lead["id"]
    # auto follow-up was scheduled
    assert len(client.get(f"/api/followups?lead_id={lid}", headers=eh).json()) == 1
    # lead can't be quoted until qualified; weak answers don't qualify
    assert client.post("/api/quotations", headers=eh, json={"lead_id": lid, "items": [{"product_id": pid, "qty": 1}]}).status_code == 422
    assert client.post(f"/api/crm/leads/{lid}/qualify", headers=eh, json={"budget": 0, "authority": 1, "need": 1, "timeline": 0}).json()["qualified"] is False
    assert client.post(f"/api/crm/leads/{lid}/qualify", headers=eh, json={"budget": 2, "authority": 2, "need": 2, "timeline": 1}).json()["qualified"]
    # discount above threshold needs manager approval and is not pushed to ERP yet
    items = [{"product_id": pid, "qty": 2, "discount_pct": 15}]
    q = client.post("/api/quotations", headers=eh, json={"lead_id": lid, "items": items, "tax_pct": 18}).json()
    assert q["status"] == "pending_approval" and q["erp_name"] == ""
    assert client.post(f"/api/quotations/{q['id']}/approve", headers=eh).status_code == 403
    ap = client.post(f"/api/quotations/{q['id']}/approve", headers=mh).json()
    assert ap["erp_name"] == "SAL-QTN-2026-00012" and ap["erp_total"] == 11800  # number fetched from ERPNext
    qpost = [b for p, b in posted if p.endswith("/Quotation")][0]
    assert qpost["quotation_to"] == "Lead" and qpost["party_name"] == "CRM-LEAD-7" and qpost["items"][0]["item_code"] == "K-1" and qpost["items"][0]["discount_percentage"] == 15
    assert client.get(f"/api/crm/leads/{lid}", headers=eh).json()["status"] == "proposal"
    assert client.post(f"/api/quotations/{q['id']}/refresh", headers=eh).json()["erp_status"] == "Open"
    # lost needs reason; won converts
    assert client.patch(f"/api/crm/leads/{lid}", headers=eh, json={"status": "lost"}).status_code == 422
    client.post(f"/api/quotations/{q['id']}/status", headers=eh, json={"status": "accepted"})
    assert client.patch(f"/api/crm/leads/{lid}", headers=eh, json={"status": "won"}).json()["converted_customer_id"]
    # follow-ups: complete + schedule next
    fid = client.get(f"/api/followups?lead_id={lid}", headers=eh).json()[0]["id"]
    nxt = (datetime.utcnow() + timedelta(days=3)).isoformat()
    assert client.post(f"/api/followups/{fid}/complete", headers=eh, json={"outcome": "follow_up_required", "notes": "call Friday", "next_at": nxt}).status_code == 200
    assert len(client.get(f"/api/followups?lead_id={lid}", headers=eh).json()) == 1
    # meeting with action items
    cust = client.get(f"/api/crm/leads/{lid}", headers=eh).json()["converted_customer_id"]
    m = client.post("/api/meetings", headers=eh, json={"title": "Kickoff", "customer_id": cust, "scheduled_at": nxt, "mode": "call"}).json()
    assert client.post(f"/api/meetings/{m['id']}/start", headers=eh, json={}).status_code == 200
    done = client.post(f"/api/meetings/{m['id']}/complete", headers=eh, json={"outcome": "order_placed", "mom": "PO expected", "action_items": [{"title": "Send contract"}, {"title": "Share specs"}], "next_followup_at": nxt}).json()
    assert done["tasks_created"] == 2
    rel = client.get(f"/api/crm/customers/{cust}/related", headers=eh).json()
    assert len(rel["tasks"]) == 2 and len(rel["meetings"]) == 1 and len(rel["quotations"]) == 1
    # analytics
    f = client.get("/api/reports/forecast", headers=mh).json()
    assert f["won_this_month"] == 100000
    assert client.get("/api/reports/funnel", headers=mh).json()["win_rate"] == 100
    d = client.get("/api/reports/dcr", headers=mh).json()
    assert any(r["name"].startswith("E") and r["calls_done"] == 1 and r["meetings"] == 1 for r in d)
    e_id = [u for u in client.get("/api/users", headers=mh).json() if u["email"] == "e@kappa.com"][0]["id"]
    assert client.put("/api/reports/targets", headers=mh, json={"user_id": e_id, "month": datetime.utcnow().strftime("%Y-%m"), "revenue": 200000, "visits": 10}).status_code == 200
    t = client.get("/api/reports/targets", headers=mh).json()["rows"][0]
    assert t["revenue"] == 100000 and t["target_revenue"] == 200000


def test_orders_credit_collections_erp(client, monkeypatch):
    from app.integrations import erpnext as erp
    from app.routers import sales, orders
    ws, h = signup(client, "Lambda Co", "o@lambda.com")
    mgr = mk_user(client, h, "m@lambda.com", "manager")
    mk_user(client, h, "e@lambda.com", "employee", mgr["id"])
    eh, mh = login(client, ws, "e@lambda.com"), login(client, ws, "m@lambda.com")
    pid = mk_product(client, h, "L-1", 1000)
    cust = client.post("/api/crm/customers", headers=eh, json={"name": "Buyer Ltd", "credit_limit": 50000, "owner_id": None}).json()
    cid = cust["id"]
    posted, uploads = [], []

    def handler(req):
        if req.method == "POST" and req.url.path == "/api/method/upload_file":
            raw = req.content.decode("latin-1")
            uploads.append((re.search(r'name="doctype"\r\n\r\n([^\r]+)', raw).group(1), re.search(r'name="docname"\r\n\r\n([^\r]+)', raw).group(1), re.search(r'filename="([^"]+)"', raw).group(1)))
            return httpx.Response(200, json={"message": {"file_url": "/private/files/x.pdf"}})
        if req.method == "POST":
            body = json.loads(req.content); posted.append((req.url.path, body))
            return httpx.Response(200, json={"data": {"name": {"Sales Order": "SAL-ORD-2026-00005", "Payment Entry": "ACC-PAY-2026-00009", "Customer": "CUST-1"}[req.url.path.rsplit("/", 1)[-1].replace("%20", " ")] if req.url.path.rsplit("/", 1)[-1].replace("%20", " ") in ("Sales Order", "Payment Entry", "Customer") else "X"}})
        return httpx.Response(200, json={"data": [{"warehouse": "Main", "actual_qty": 40, "reserved_qty": 10}]})
    client.put("/api/erp/config", headers=h, json={"enabled": True, "url": "https://erp.example", "api_key": "K", "api_secret": "S", "company": "Lambda"})
    monkeypatch.setattr(sales, "_erp_client", lambda ctx: erp.ERPNextClient(erp._cfg(ctx.tenant), httpx.MockTransport(handler)))
    monkeypatch.setattr(orders, "_erp_client", sales._erp_client)
    inv = client.post("/api/invoices", headers=h, json={"customer_id": cid, "number": "INV-1", "invoice_date": (date.today() - timedelta(days=60)).isoformat(), "due_date": (date.today() - timedelta(days=40)).isoformat(), "total": 40000}).json()
    inv2 = client.post("/api/invoices", headers=h, json={"customer_id": cid, "number": "INV-2", "invoice_date": date.today().isoformat(), "total": 5000}).json()
    out = client.get("/api/collections/outstanding", headers=h).json()
    assert out["total_outstanding"] == 45000 and out["customers"][0]["aging"]["d31_60"] == 40000
    # credit limit: 45000 outstanding + 11800 > 50000 -> needs approval for a rep
    od = {"customer_id": cid, "items": [{"product_id": pid, "qty": 10}], "delivery_date": (date.today() + timedelta(days=5)).isoformat()}
    assert client.post("/api/orders", headers=eh, json=od).status_code == 422  # customer PO scan is mandatory
    assert client.post("/api/orders", headers=eh, json={**od, "po_file": "data:text/plain;base64,aGVsbG8gd29ybGQgaGVsbG8gd29ybGQ="}).status_code == 422
    od["po_file"] = "data:application/pdf;base64," + base64.b64encode(b"%PDF-1.4 fake customer po").decode()
    o = client.post("/api/orders", headers=eh, json=od).json()
    assert o["status"] == "pending_approval" and "credit limit" in o["credit_note"] and o["erp_name"] == ""
    assert client.post(f"/api/orders/{o['id']}/approve", headers=eh).status_code == 403
    ap = client.post(f"/api/orders/{o['id']}/approve", headers=mh).json()
    assert ap["status"] == "confirmed" and ap["erp_name"] == "SAL-ORD-2026-00005" and ap["po_attached"] is True
    assert uploads and uploads[0][0] == "Sales Order" and uploads[0][1] == "SAL-ORD-2026-00005" and uploads[0][2].endswith(".pdf")
    so = [b for p, b in posted if p.endswith("/Sales%20Order") or p.endswith("/Sales Order")][0]
    assert so["customer"] == "CUST-1" and so["items"][0]["qty"] == 10 and so["docstatus"] == 0  # draft
    assert client.get(f"/api/products/{pid}/stock", headers=eh).json()["available"] == 30
    # collections: validation then verify
    bad = client.post("/api/collections", headers=eh, json={"customer_id": cid, "invoice_id": inv["id"], "amount": 50000, "mode": "cash"})
    assert bad.status_code == 422
    assert client.post("/api/collections", headers=eh, json={"customer_id": cid, "invoice_id": inv["id"], "amount": 100, "mode": "upi"}).status_code == 422  # needs reference
    p = client.post("/api/collections", headers=eh, json={"customer_id": cid, "invoice_id": inv["id"], "amount": 15000, "mode": "upi", "reference": "UTR123"}).json()
    assert p["receipt_no"] == "RCT-00001" and p["status"] == "pending"
    assert client.post("/api/collections", headers=eh, json={"customer_id": cid, "amount": 100, "mode": "upi", "reference": "UTR123"}).status_code == 409  # duplicate ref
    assert client.post(f"/api/collections/{p['id']}/verify", headers=eh, json={"decision": "verified"}).status_code == 403
    v = client.post(f"/api/collections/{p['id']}/verify", headers=mh, json={"decision": "verified"}).json()
    assert v["status"] == "verified"
    pe = [b for p_, b in posted if "Payment" in p_][0]
    assert pe["paid_amount"] == 15000 and pe["party"] == "CUST-1" and pe["mode_of_payment"] == "Wire Transfer" and "references" not in pe  # manual invoice isn't in ERPNext
    assert v["erp_ref"] == "ACC-PAY-2026-00009"
    after = client.get("/api/collections/outstanding", headers=h).json()
    assert after["total_outstanding"] == 30000
    d = client.get("/api/reports/dcr", headers=mh).json()
    assert any(r["collected"] == 15000 and r["orders_booked"] == 1 for r in d)


def test_beat_plan_lifecycle(client):
    ws, h = signup(client, "Mu Co", "o@mu.com")
    mgr = mk_user(client, h, "m@mu.com", "manager")
    rep = mk_user(client, h, "e@mu.com", "employee", mgr["id"])
    eh, mh = login(client, ws, "e@mu.com"), login(client, ws, "m@mu.com")
    cs = [client.post("/api/crm/customers", headers=h, json={"name": n, "phone": "+9198111100" + str(i), "lat": la, "lng": 72.85}).json()["id"] for i, (n, la) in enumerate([("A", 19.30), ("B", 19.10), ("C", 19.20)])]
    wd = date.today().weekday()
    body = {"name": "Andheri Beat", "user_id": rep["id"], "days": [wd, (wd + 3) % 7], "customer_ids": cs, "every_weeks": 1}
    assert client.post("/api/beats", headers=eh, json=body).status_code == 403           # reps can't create beats
    assert client.post("/api/beats", headers=mh, json={**body, "customer_ids": [cs[0], cs[0]]}).status_code == 422
    b = client.post("/api/beats", headers=mh, json=body).json()
    plan = client.get("/api/beats/day", headers=eh).json()
    assert plan["planned"] == 3 and [s["customer"]["name"] for s in plan["stops"]] == ["A", "B", "C"]
    # optimiser reorders stops by proximity (B is nearest to start A? start = first customer)
    opt = client.post("/api/beats/optimize", headers=mh, json={"customer_ids": cs, "user_id": rep["id"]}).json()
    assert opt["customer_ids"][0] == cs[0] and opt["customer_ids"][1] == cs[2]   # A -> C -> B
    client.patch(f"/api/beats/{b['id']}", headers=mh, json={**body, "customer_ids": opt["customer_ids"]})
    assert [s["customer"]["name"] for s in client.get("/api/beats/day", headers=eh).json()["stops"]] == ["A", "C", "B"]
    # execute stop 1 with OTP, skip stop 2 (reason required)
    s1, s2 = client.get("/api/beats/day", headers=eh).json()["stops"][:2]
    st = client.post(f"/api/beats/visits/{s1['id']}/start", headers=eh, json={"lat": 19.30, "lng": 72.85}).json()
    assert st["otp_required"]
    vid = st["visit"]["id"]
    client.post(f"/api/visits/{vid}/verify-otp", headers=eh, json={"otp": st["dev_otp"]})
    assert client.post(f"/api/visits/{vid}/end", headers=eh, json={"mom": "Order discussed"}).status_code == 200
    assert client.post(f"/api/beats/visits/{s2['id']}/skip", headers=eh, json={"reason": "x"}).status_code == 422
    assert client.post(f"/api/beats/visits/{s2['id']}/skip", headers=eh, json={"reason": "Shop closed today"}).status_code == 200
    assert client.post(f"/api/beats/visits/{s1['id']}/start", headers=eh, json={}).status_code == 409  # already visited
    plan = client.get("/api/beats/day", headers=mh, params={"user_id": rep["id"]}).json()
    assert plan["visited"] == 1 and plan["skipped"] == 1
    ad = client.get("/api/beats/adherence", headers=mh).json()
    assert ad["total"]["visited"] == 1 and ad["by_user"][0]["adherence"] == 50   # 1 visited of 2 finished (1 still open)
    assert any(n["title"] == "Beat stop skipped" for n in client.get("/api/notifications", headers=mh).json()["items"])
    assert client.delete(f"/api/beats/{b['id']}", headers=mh).status_code == 200
    assert client.get("/api/beats/day", headers=eh).json()["planned"] == 2     # open stop removed, history kept
