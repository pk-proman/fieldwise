from datetime import datetime, timedelta
from types import SimpleNamespace as NS
from conftest import signup, login
from app import routecalc


def _pings(day, start_lat=28.6):
    """About 20 minutes of driving, the phone offline for a few minutes, GPS off for a few, then 40 minutes of silence, then driving again."""
    out, lat, ts = [], start_lat, day
    def add(sec, **kw):
        nonlocal lat, ts
        lat += 0.0009; ts += timedelta(seconds=sec)
        out.append({"ts": ts.isoformat(), "lat": lat, "lng": 77.2, "accuracy": 8, "battery": 80, **kw})
    for _ in range(20): add(30)
    for _ in range(6): add(60, network="offline")
    for _ in range(4):
        add(60); out[-1].update(gps_on=False, lat=None, lng=None)
    ts += timedelta(minutes=40)
    for _ in range(10): add(30, battery=9)
    return out


def test_connectivity_episodes():
    t = datetime(2026, 11, 17, 4, 0)
    P = lambda m, **k: NS(ts=t + timedelta(minutes=m), lat=k.pop("lat", 19.0), lng=19.0, network=k.pop("net", "online"), gps_on=k.pop("gps", True), battery=k.pop("b", 80))
    pings = [P(0), P(2), P(4), P(6, net="offline"), P(8, net="offline"), P(10, net="offline"), P(12), P(14, gps=False, lat=None), P(16, gps=False, lat=None), P(18), P(60, b=12), P(62, b=11)]
    r = routecalc.connectivity(pings, t, t + timedelta(minutes=100))
    kinds = {e["kind"]: e for e in r["episodes"]}
    assert kinds["no_internet"]["minutes"] == 6 and kinds["gps_off"]["minutes"] == 4
    assert [e["minutes"] for e in r["episodes"] if e["kind"] == "unreachable"] == [42, 38]
    assert r["episodes"][-1]["ongoing"] and r["totals"]["unreachable_min"] == 80 and r["totals"]["reporting_pct"] == 20
    assert kinds["battery_low"]["battery"] == 12
    assert routecalc.connectivity([], t, t)["totals"]["unreachable_min"] == 0


def test_person_day_log_and_access(client):
    ws, hdr = signup(client, "TrackCo", "own@tk.com")
    mk = lambda n, e, m=None, role="employee": client.post("/api/users", headers=hdr, json={"name": n, "email": e, "password": "Passw0rd!x", "role": role, "manager_id": m}).json()["id"]
    boss = mk("Boss", "boss@tk.com", role="manager")
    rep = mk("Rep", "rep@tk.com", boss)
    peer = mk("Peer", "peer@tk.com")
    rh, bh = login(client, ws, "rep@tk.com"), login(client, ws, "boss@tk.com")
    day = datetime.utcnow().replace(hour=4, minute=0, second=0, microsecond=0) - timedelta(days=1)
    assert client.post("/api/tracking/pings", headers=rh, json=_pings(day)).status_code == 200
    r = client.get(f"/api/tracking/person-day?user_id={rep}&day={day.date().isoformat()}", headers=bh).json()
    assert r["found"] and r["route"]["distance_km"] > 3 and len(r["route"]["polyline"]) >= 2
    c = r["connectivity"]
    assert c["totals"]["unreachable_min"] >= 35 and c["totals"]["no_internet_min"] >= 4 and c["totals"]["gps_off_min"] >= 3
    types = [x["type"] for x in r["log"]]
    assert "unreachable" in types and "no_internet" in types and "gps_off" in types
    assert [x["at"] for x in r["log"]] == sorted(x["at"] for x in r["log"])
    empty = client.get(f"/api/tracking/person-day?user_id={rep}&day={(day - timedelta(days=5)).date().isoformat()}", headers=bh).json()
    assert empty["found"] is False
    assert client.get(f"/api/tracking/person-day?user_id={rep}", headers=login(client, ws, "peer@tk.com")).status_code == 403
    assert client.get(f"/api/tracking/person-day?user_id={boss}", headers=rh).status_code == 403


def test_daily_tracker_adjust_and_other_expenses(client):
    ws, hdr = signup(client, "TrackerCo", "own@tr.com")
    mk = lambda n, e, m=None, role="employee": client.post("/api/users", headers=hdr, json={"name": n, "email": e, "password": "Passw0rd!x", "role": role, "manager_id": m}).json()["id"]
    boss = mk("Boss", "boss@tr.com", role="manager")
    rep = mk("Rep", "rep@tr.com", boss)
    client.put("/api/expenses/policy", headers=hdr, json={"mode": "off", "default": {"rate_per_km": 5, "allowance_daily": 0}})
    rh, bh = login(client, ws, "rep@tr.com"), login(client, ws, "boss@tr.com")
    day = datetime.utcnow().replace(hour=4, minute=0, second=0, microsecond=0) - timedelta(days=1)
    client.post("/api/tracking/pings", headers=rh, json=_pings(day))
    d = day.date().isoformat()
    per = client.get(f"/api/expenses/period?start={d}&end={d}", headers=rh).json()
    row = per["days"][0]
    assert row["km"] > 3 and abs(row["calc"]["fuel"] - row["km"] * 5) < 0.05 and per["totals"]["unclaimed_days"] == 1     # expected amount is shown even with the rules switched off
    # changing the amount needs a reason
    assert client.post("/api/expenses/day-travel", headers=rh, json={"day": d, "amount": 500}).status_code == 422
    ok = client.post("/api/expenses/day-travel", headers=rh, json={"day": d, "km": row["km"] + 2, "reason": "Took a detour to the depot"})
    assert ok.status_code == 200 and ok.json()["saved"][0]["source"] == "edited" and ok.json()["saved"][0]["calc_amount"] == row["calc"]["fuel"]
    assert abs(ok.json()["saved"][0]["amount"] - (row["km"] + 2) * 5) < 0.05
    again = client.post("/api/expenses/day-travel", headers=rh, json={"day": d, "amount": 321, "reason": "Fixed amount agreed"}).json()["saved"][0]
    assert again["amount"] == 321 and len([e for e in client.get("/api/expenses?scope=mine", headers=rh).json() if e["category"] == "fuel"]) == 1     # updated, not duplicated
    assert client.post("/api/expenses", headers=rh, data={"day": d, "category": "fuel", "amount": "100"}).status_code == 409               # no second fuel claim
    # other expenses on the same day
    for cat, amt in (("lodging", "1800"), ("food", "350"), ("taxi", "220")):
        assert client.post("/api/expenses", headers=rh, data={"day": d, "category": cat, "amount": amt, "description": cat}).status_code == 200
    per = client.get(f"/api/expenses/period?start={d}&end={d}", headers=rh).json()
    assert per["days"][0]["other"] == 2370 and per["totals"]["other"] == 2370 and per["days"][0]["claimed"] == 2691 and per["totals"]["unclaimed_days"] == 0
    # the manager can adjust a pending claim; the employee cannot adjust their own; the original is kept
    fuel = next(i for i in per["days"][0]["items"] if i["category"] == "fuel")
    assert client.post(f"/api/expenses/{fuel['id']}/adjust", headers=rh, json={"amount": 999, "note": "self"}).status_code == 403
    assert client.post(f"/api/expenses/{fuel['id']}/adjust", headers=bh, json={"amount": 300}).status_code == 422
    adj = client.post(f"/api/expenses/{fuel['id']}/adjust", headers=bh, json={"amount": 280, "note": "Depot trip not approved"})
    assert adj.status_code == 200 and adj.json()["amount"] == 280 and adj.json()["calc_amount"] == row["calc"]["fuel"]
    # once approved the employee can no longer change it
    assert client.post("/api/expenses/decide", headers=bh, json={"ids": [fuel["id"]], "decision": "approved"}).status_code == 200
    assert client.post("/api/expenses/day-travel", headers=rh, json={"day": d, "amount": 100, "reason": "oops"}).status_code == 409
    assert client.post("/api/expenses/day-travel", headers=rh, json={"day": (datetime.utcnow() + timedelta(days=2)).date().isoformat()}).status_code == 422
