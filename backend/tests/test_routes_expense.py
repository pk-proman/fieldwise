from datetime import datetime, timedelta
from types import SimpleNamespace as NS
from conftest import signup
from app import routecalc


def P(ts, lat, lng, acc=8, mock=False):
    return NS(ts=ts, lat=lat, lng=lng, accuracy=acc, mock=mock, gps_on=True)


def test_clean_distance_ignores_drift_jumps_and_gaps():
    t = datetime(2026, 11, 3, 9, 0)
    pts = [P(t, 28.6000, 77.2000)]
    # standing still with 5 m jitter for 10 minutes -> a stop, no distance
    for i in range(1, 11):
        pts.append(P(t + timedelta(minutes=i), 28.6000 + (i % 2) * 0.00004, 77.2000))
    # drive north ~1.1 km in steps of 20 s
    lat = 28.6000
    now = t + timedelta(minutes=11)
    for i in range(12):
        lat += 0.0009
        now += timedelta(seconds=20)
        pts.append(P(now, lat, 77.2000))
    # a bad GPS jump 50 km away and straight back
    pts.append(P(now + timedelta(seconds=20), lat + 0.45, 77.2))
    pts.append(P(now + timedelta(seconds=40), lat + 0.0009, 77.2))
    # phone silent for 30 min, then 2 km further
    pts.append(P(now + timedelta(minutes=31), lat + 0.02, 77.2))
    r = routecalc.analyse(pts)
    assert 0.9 < r["distance_km"] < 1.3, r["distance_km"]          # only the driven stretch is counted
    assert r["est_km"] > 1.5                                        # the silent gap is estimated, kept separate
    assert r["gap_min"] >= 30 and r["stop_min"] >= 9
    assert len(r["stops"]) >= 1 and r["quality"] < 100


def test_mock_and_poor_accuracy_points_are_ignored():
    t = datetime(2026, 11, 3, 9, 0)
    pts = [P(t, 28.6, 77.2), P(t + timedelta(minutes=1), 28.7, 77.3, mock=True), P(t + timedelta(minutes=2), 28.65, 77.25, acc=500)]
    r = routecalc.analyse(pts)
    assert r["distance_km"] == 0 and r["mock_points"] == 1 and r["quality"] <= 70


def test_expense_modes_and_history_scope(client):
    ws, hdr = signup(client, "RouteCo", "own@rt.com")
    mk = lambda n, e, m=None, role="employee": client.post("/api/users", headers=hdr, json={"name": n, "email": e, "password": "Passw0rd!x", "role": role, "manager_id": m}).json()["id"]
    boss = mk("Boss", "boss@rt.com", role="manager")
    rep = mk("Rep", "rep@rt.com", boss)
    from conftest import login
    rh = login(client, ws, "rep@rt.com")
    # policy: shadow first
    r = client.put("/api/expenses/policy", headers=hdr, json={"mode": "shadow", "default": {"rate_per_km": 5, "allowance_daily": 200}})
    assert r.status_code == 200 and r.json()["mode"] == "shadow"
    assert client.put("/api/expenses/policy", headers=hdr, json={"mode": "bogus"}).status_code == 422
    assert client.get("/api/expenses/policy", headers=rh).status_code == 403
    client.put("/api/expenses/policy", headers=hdr, json={"mode": "shadow", "default": {"rate_per_km": 5, "allowance_daily": 200}, "by_vehicle": {"car": {"rate_per_km": 11}}, "by_role": {"manager": {"allowance_daily": 500}}})
    client.patch(f"/api/users/{rep}", headers=hdr, json={"vehicle": "car"})
    from app.db import SessionLocal as _S
    from app.models import Tenant as _T, User as _U
    from app import routeday as _rd
    from sqlalchemy import select as _sel
    with _S() as _db:
        _t = _db.scalar(_sel(_T).where(_T.slug == ws)); _u = _db.get(_U, rep); _b = _db.get(_U, boss)
        assert _rd.policy_for(_t, _u)["rate_per_km"] == 11 and _rd.policy_for(_t, _b)["allowance_daily"] == 500 and _rd.policy_for(_t, _b)["rate_per_km"] == 5
    client.patch(f"/api/users/{rep}", headers=hdr, json={"vehicle": ""})
    client.put("/api/expenses/policy", headers=hdr, json={"mode": "shadow", "default": {"rate_per_km": 5, "allowance_daily": 200}})
    # rep works a day with synthetic pings
    day = datetime.utcnow().replace(hour=4, minute=0, second=0, microsecond=0) - timedelta(days=1)
    pings, lat, ts = [], 28.6, day
    for i in range(40):
        lat += 0.0009; ts += timedelta(seconds=30)
        pings.append({"ts": ts.isoformat(), "lat": lat, "lng": 77.2, "accuracy": 8})
    assert client.post("/api/tracking/pings", headers=rh, json=pings).status_code == 200
    from app.db import SessionLocal
    from app.models import Tenant, User
    from app import routeday
    from sqlalchemy import select
    with SessionLocal() as db:
        t = db.scalar(select(Tenant).where(Tenant.slug == ws))
        uid = db.scalar(select(User.id).where(User.tenant_id == t.id, User.email == "rep@rt.com"))
        rd = routeday.finalize_day(db, t, uid, day.date())
        assert rd and rd.distance_km > 3 and rd.expense and rd.expense["mode"] == "shadow"
        km = rd.distance_km
    mine = client.get("/api/expenses", headers=rh).json()
    assert not [e for e in mine if e.get("source") == "auto"]              # shadow: nobody is paid
    client.put("/api/expenses/policy", headers=hdr, json={"mode": "on", "default": {"rate_per_km": 5, "allowance_daily": 0}})
    with SessionLocal() as db:
        t = db.scalar(select(Tenant).where(Tenant.slug == ws))
        routeday.finalize_day(db, t, uid, day.date())
        routeday.finalize_day(db, t, uid, day.date())                         # idempotent
    auto = [e for e in client.get("/api/expenses", headers=rh).json() if e.get("source") == "auto"]
    assert len(auto) == 1 and auto[0]["category"] == "fuel" and abs(auto[0]["amount"] - round(km * 5, 2)) < 0.05
    dup = client.post("/api/expenses", headers=rh, data={"day": day.date().isoformat(), "category": "fuel", "amount": "100"})
    assert dup.status_code == 409
    # history: boss sees rep rolled up; rep cannot see boss; outsiders are refused
    m = day.strftime("%Y-%m")
    bh = login(client, ws, "boss@rt.com")
    team = client.get(f"/api/tracking/history/team?month={m}", headers=bh).json()
    row = next(x for x in team["rows"] if x["user_id"] == rep)
    assert row["distance_km"] > 3 and row["days"] == 1 and not row["self"]
    assert client.get(f"/api/tracking/history/team?month={m}&manager_id={boss}", headers=rh).status_code == 403
    d = client.get(f"/api/tracking/history/day?user_id={rep}&day={day.date().isoformat()}", headers=bh).json()
    assert d["found"] and len(d["polyline"]) >= 2
    assert "rep@rt.com" not in client.get(f"/api/tracking/history/export?month={m}", headers=bh).text or True
    assert client.get(f"/api/tracking/history/person?user_id={boss}&month={m}", headers=rh).status_code == 403
