from datetime import datetime, timedelta, timezone, date
from types import SimpleNamespace as NS
from conftest import signup, login
from test_app import mk_user, mk_product

HQ = (19.1136, 72.8697)


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def setup_team(client, name):
    ws, h = signup(client, name, f"o@{name.split()[0].lower()}.com")
    client.post("/api/branches", headers=h, json={"name": "HQ", "address": "Mumbai", "lat": HQ[0], "lng": HQ[1], "radius_m": 300})
    mgr = mk_user(client, h, f"m@{name.split()[0].lower()}.com", "manager")
    emp = mk_user(client, h, f"e@{name.split()[0].lower()}.com", "employee", mgr["id"])
    return ws, h, mgr, emp, login(client, ws, mgr["email"]), login(client, ws, emp["email"])


def ping(ts, lat, lng, **kw):
    return {"ts": ts.isoformat(), "lat": lat, "lng": lng, "battery": kw.get("battery", 80), "network": "online", "gps_on": kw.get("gps_on", True), "mock": kw.get("mock", False)}


def test_analysis_unit():
    from app.routers.field import analyze
    t0 = datetime(2026, 11, 2, 4, 0)
    cust = [NS(name="Site A", lat=19.20, lng=72.90, id=1)]
    pts = []
    # 10 min travelling towards Site A, 30 min on site, 12 min idle elsewhere, then a 45 min silence
    for i in range(11):
        pts.append(NS(ts=t0 + timedelta(minutes=i), lat=19.10 + i * 0.009, lng=72.85 + i * 0.005, mock=False, gps_on=True))
    for i in range(31):
        pts.append(NS(ts=t0 + timedelta(minutes=11 + i), lat=19.20, lng=72.90, mock=False, gps_on=True))
    for i in range(13):
        pts.append(NS(ts=t0 + timedelta(minutes=42 + i), lat=19.30, lng=73.00, mock=False, gps_on=True))
    pts.append(NS(ts=t0 + timedelta(minutes=100), lat=19.30, lng=73.00, mock=False, gps_on=True))
    r = analyze(pts, t0, t0 + timedelta(minutes=100), cust, [], t0 + timedelta(minutes=90), 0)
    assert r["on_site_min"] >= 28 and r["idle_min"] >= 10 and r["travel_min"] >= 8 and r["gap_min"] >= 40
    assert [s["kind"] for s in r["stops"]][:1] == ["customer"] and r["stops"][0]["place"] == "Site A"
    assert any(a["type"] == "tracking_gap" for a in r["anomalies"]) and r["overtime_min"] == 10


def test_summary_geo_territory_dispatch(client):
    ws, h, mgr, emp, mh, eh = setup_team(client, "Alpha Field")
    cust = client.post("/api/crm/customers", headers=h, json={"name": "Site A", "phone": "+919000000001", "lat": 19.1500, "lng": 72.9000}).json()
    now = utc_now()
    ci = client.post("/api/attendance/check-in", headers=eh, json={"lat": HQ[0], "lng": HQ[1], "client_ts": (now - timedelta(minutes=70)).isoformat()})
    assert ci.status_code == 200, ci.text
    # a territory around HQ; the rep drives out and later arrives at Site A
    poly = [[19.08, 72.84], [19.08, 72.89], [19.14, 72.89], [19.14, 72.84]]
    client.post("/api/field/territories", headers=mh, json={"name": "Andheri", "polygon": poly, "user_ids": [emp["id"]], "alert": True})
    path = [ping(now - timedelta(minutes=65 - i), 19.1136 + i * 0.0015, 72.8697 + i * 0.0027) for i in range(15)]       # leaves the polygon
    path += [ping(now - timedelta(minutes=50 - i), 19.1500, 72.9000 + (0.0 if i else 0)) for i in range(0, 25)]            # 25 min at Site A
    path += [ping(now - timedelta(minutes=25 - i), 19.2000 + i * 0.002, 72.9500) for i in range(0, 12)]                    # moves on
    assert client.post("/api/tracking/pings", headers=eh, json=path).json()["accepted"] == len(path)
    ev = client.get("/api/field/geo-events", headers=mh).json()
    kinds = [(e["kind"], e["place"]) for e in ev]
    assert ("arrive", "Site A") in kinds and ("depart", "Site A") in kinds and ("territory_exit", "Andheri") in kinds
    dep = [e for e in ev if e["kind"] == "depart"][0]
    assert dep["minutes"] >= 20
    assert any(n["title"] == "Left territory" for n in client.get("/api/notifications", headers=mh).json()["items"])
    assert any(n["title"].startswith("You've arrived at Site A") for n in client.get("/api/notifications", headers=eh).json()["items"])
    s = client.get("/api/field/day-summary", headers=mh, params={"user_id": emp["id"]}).json()
    assert s["present"] and s["on_site_min"] >= 20 and any(st["place"] == "Site A" for st in s["stops"]) and s["travel_min"] >= 5
    p = client.get("/api/field/productivity", headers=mh).json()["rows"][0]
    assert p["days"] == 1 and p["on_site_min"] >= 20 and p["stops"] >= 1
    sess = client.get(f"/api/tracking/session/{client.get('/api/tracking/sessions', headers=eh).json()[0]['id']}", headers=eh).json()
    assert sess["analysis"]["stops"]
    # dispatch: a task near Site A -> the on-duty rep is suggested, with distance and ETA
    t = client.post("/api/tasks", headers=mh, json={"title": "Urgent sample drop", "customer_id": cust["id"], "assignee_id": mgr["id"]}).json()
    sug = client.get(f"/api/field/dispatch/{t['id']}", headers=mh).json()["suggestions"]
    assert sug and sug[0]["name"] == emp["name"] and sug[0]["eta_min"] >= 0
    assert client.post(f"/api/field/tasks/{t['id']}/progress", headers=eh, json={"step": "en_route"}).status_code == 403   # not theirs yet
    client.patch(f"/api/tasks/{t['id']}", headers=mh, json={"assignee_id": emp["id"]})
    assert client.post(f"/api/field/tasks/{t['id']}/progress", headers=eh, json={"step": "en_route"}).json()["status"] == "en_route"
    assert client.post(f"/api/field/tasks/{t['id']}/progress", headers=eh, json={"step": "rescheduled"}).status_code == 422
    assert client.post(f"/api/field/tasks/{t['id']}/progress", headers=eh, json={"step": "rescheduled", "due_at": (now + timedelta(days=1)).isoformat(), "reason": "Customer away"}).json()["status"] == "rescheduled"
    an = client.get("/api/field/anomalies", headers=mh).json()
    assert isinstance(an, list)


def test_shifts_roster_swaps(client):
    ws, h, mgr, emp, mh, eh = setup_team(client, "Beta Shift")
    emp2 = mk_user(client, h, "e2@beta.com", "employee", mgr["id"])
    e2h = login(client, ws, "e2@beta.com")
    s1 = client.post("/api/field/shifts", headers=h, json={"name": "Morning", "start": "06:00", "end": "14:00", "grace_min": 5, "break_min": 30}).json()
    s2 = client.post("/api/field/shifts", headers=h, json={"name": "Late", "start": "14:00", "end": "22:00", "grace_min": 5, "break_min": 30}).json()
    client.patch(f"/api/users/{emp['id']}", headers=h, json={"shift_id": s1["id"]})
    client.patch(f"/api/users/{emp2['id']}", headers=h, json={"shift_id": s2["id"]})
    assert client.delete(f"/api/field/shifts/{s1['id']}", headers=h).status_code == 409
    # lateness is judged against the person's own shift (Morning starts 06:00 +5; the workspace default is 09:30 +15)
    now = utc_now()
    local_now = now + timedelta(hours=5, minutes=30)
    late_ts = (datetime.combine(local_now.date(), datetime.min.time()) + timedelta(hours=7)) - timedelta(hours=5, minutes=30)   # 07:00 IST today
    if late_ts > now:
        late_ts -= timedelta(days=1)
    r = client.post("/api/attendance/check-in", headers=eh, json={"lat": HQ[0], "lng": HQ[1], "client_ts": late_ts.isoformat()}).json()
    assert r["status"] == "late"
    # roster: mark tomorrow off for emp, and read the week grid
    tmr = date.today() + timedelta(days=1)
    client.put("/api/field/roster", headers=mh, json={"user_id": emp["id"], "day": tmr.isoformat(), "shift_id": None})
    grid = client.get("/api/field/roster", headers=mh, params={"week": tmr.isoformat()}).json()
    cell = [c for rrow in grid["rows"] if rrow["user_id"] == emp["id"] for c in rrow["cells"] if c["day"] == tmr.isoformat()][0]
    assert cell["off"] and cell["override"]
    # swap: emp2 asks emp for the day after tomorrow; emp accepts; the manager approves; shifts are exchanged
    d3 = date.today() + timedelta(days=2)
    sw = client.post("/api/field/swaps", headers=e2h, json={"with_user_id": emp["id"], "day": d3.isoformat(), "reason": "Family event"}).json()
    assert client.post(f"/api/field/swaps/{sw['id']}/respond", headers=e2h, json={"accept": True}).status_code == 403
    assert client.post(f"/api/field/swaps/{sw['id']}/respond", headers=eh, json={"accept": True}).json()["status"] == "accepted"
    assert client.post(f"/api/field/swaps/{sw['id']}/respond", headers=eh, json={"accept": True}).status_code == 403     # only a manager may approve
    assert client.post(f"/api/field/swaps/{sw['id']}/respond", headers=mh, json={"accept": True}).json()["status"] == "approved"
    grid = client.get("/api/field/roster", headers=mh, params={"week": d3.isoformat()}).json()
    names = {rrow["name"]: [c for c in rrow["cells"] if c["day"] == d3.isoformat()][0]["shift"] for rrow in grid["rows"]}
    assert names[emp["name"]] == "Late" and names[emp2["name"]] == "Morning"


def test_forms_da_levels_announcements_schedules(client):
    ws, h, mgr, emp, mh, eh = setup_team(client, "Gamma Extras")
    pid = mk_product(client, h, "G-1", 100)
    cust = client.post("/api/crm/customers", headers=h, json={"name": "Audit Co", "phone": "+919000000009", "lat": 19.1137, "lng": 72.8698}).json()
    # --- mandatory visit form blocks ending the visit
    f = client.post("/api/forms", headers=h, json={"name": "Site audit", "applies_to": "visit", "mandatory": True, "fields": [
        {"label": "Competitor present", "type": "checkbox"}, {"label": "Shelf share %", "type": "number", "required": True}, {"label": "Condition", "type": "select", "options": ["Good", "Poor"], "required": True}, {"label": "Rating", "type": "rating"}]}).json()
    assert [q["key"] for q in f["fields"]][:2] == ["competitor_present", "shelf_share"]
    client.put("/api/settings", headers=h, json={"require_visit_otp": False})
    v = client.post("/api/visits/start", headers=eh, json={"customer_id": cust["id"], "lat": 19.1137, "lng": 72.8698}).json()["visit"]
    blocked = client.post(f"/api/visits/{v['id']}/end", headers=eh, json={"mom": "ok"})
    assert blocked.status_code == 422 and "Site audit" in blocked.json()["detail"]
    assert client.post(f"/api/forms/{f['id']}/responses", headers=eh, json={"visit_id": v["id"], "data": {"shelf_share": "x", "condition": "Good"}}).status_code == 422
    assert client.post(f"/api/forms/{f['id']}/responses", headers=eh, json={"visit_id": v["id"], "data": {"shelf_share": 35, "condition": "Excellent"}}).status_code == 422
    ok = client.post(f"/api/forms/{f['id']}/responses", headers=eh, json={"visit_id": v["id"], "customer_id": cust["id"], "data": {"shelf_share": 35, "condition": "Poor", "rating": 4, "competitor_present": True}})
    assert ok.status_code == 200
    client.put("/api/settings", headers=h, json={"require_visit_photo": True})
    assert client.post(f"/api/visits/{v['id']}/end", headers=eh, json={"mom": "ok"}).status_code == 422                # proof-of-visit photo
    import base64
    img = "data:image/png;base64," + base64.b64encode(bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da6360000002000001e221bc330000000049454e44ae426082")).decode()
    assert client.post(f"/api/visits/{v['id']}/end", headers=eh, json={"mom": "ok", "photo": img}).status_code == 200
    assert "Poor" in client.get("/api/forms/responses", headers=mh, params={"template_id": f["id"], "fmt": "csv"}).text
    # --- DA by tier + approval levels
    assert client.post("/api/expenses/da-auto", headers=eh, json={}).status_code == 422                                   # not enabled
    client.put("/api/da-settings", headers=h, json={"enabled": True, "rates": {"metro": 500, "other": 300, "outstation": 900}, "outstation_km": 50, "min_hours": 4, "metro_keywords": ["Mumbai"],
                                                  "levels": [{"upto": 600, "role": "manager"}, {"upto": None, "role": "admin"}]})
    now = utc_now()
    client.post("/api/attendance/check-in", headers=eh, json={"lat": HQ[0], "lng": HQ[1], "client_ts": (now - timedelta(hours=6)).isoformat()})
    far = [ping(now - timedelta(hours=5) + timedelta(minutes=30 * i), 19.1136 + i * 0.1, 72.87) for i in range(8)]       # drives ~80 km away
    client.post("/api/tracking/pings", headers=eh, json=far)
    client.post("/api/attendance/check-out", headers=eh, json={"lat": HQ[0], "lng": HQ[1]})
    da = client.post("/api/expenses/da-auto", headers=eh, json={})
    assert da.status_code == 200, da.text
    assert da.json()["amount"] == 900 and "outstation" in da.json()["description"] and da.json()["levels_required"] == 2
    assert client.post("/api/expenses/da-auto", headers=eh, json={}).status_code == 409
    eid = da.json()["id"]
    r1 = client.post("/api/expenses/decide", headers=mh, json={"ids": [eid], "decision": "approved"}).json()
    assert r1["awaiting_next_level"] == [eid] and r1["updated"] == []
    assert client.post("/api/expenses/decide", headers=mh, json={"ids": [eid], "decision": "approved"}).json()["skipped"] == [eid]   # not the manager twice
    r2 = client.post("/api/expenses/decide", headers=h, json={"ids": [eid], "decision": "approved"}).json()
    assert r2["updated"] == [eid]
    e = [x for x in client.get("/api/expenses?scope=mine", headers=eh).json() if x["id"] == eid][0]
    assert e["status"] == "approved" and e["level"] == 2 and len(e["approvals"]) == 2
    # --- announcements
    a = client.post("/api/announcements", headers=mh, json={"title": "Diwali schedule", "body": "Offices close Friday", "audience": "team", "pinned": True}).json()
    mine = client.get("/api/announcements", headers=eh).json()
    assert mine[0]["title"] == "Diwali schedule" and mine[0]["unread"]
    client.post(f"/api/announcements/{a['id']}/read", headers=eh)
    assert client.get("/api/announcements", headers=mh).json()[0]["read_count"] == 2
    other = mk_user(client, h, "x@gamma.com", "employee")
    assert client.get("/api/announcements", headers=login(client, ws, "x@gamma.com")).json() == []                           # outside that team
    assert client.post("/api/announcements", headers=eh, json={"title": "Nope"}).status_code == 403
    # --- scheduled e-reports
    sc = client.post("/api/report-schedules", headers=mh, json={"name": "Daily DCR", "report": "dcr", "frequency": "daily", "hour": 0, "recipients": "boss@x.com"}).json()
    assert client.post("/api/report-schedules", headers=mh, json={"name": "bad", "report": "nope", "recipients": "a@b.com"}).status_code == 422
    run = client.post(f"/api/report-schedules/{sc['id']}/run", headers=mh).json()
    assert "SMTP" in run["status"]                                                                                           # no mail server configured: reported, not hidden
    from app.routers.fieldx import build_report
    from app.db import SessionLocal
    from app.models import ReportSchedule, Tenant
    from sqlalchemy import select
    db = SessionLocal()
    t = db.scalar(select(Tenant).where(Tenant.slug == ws))
    fn, data = build_report(db, t, db.get(ReportSchedule, sc["id"]))
    db.close()
    assert fn.startswith("dcr_") and b"name" in data
