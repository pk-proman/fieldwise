from conftest import signup, login
from test_app import mk_user
from app import devhealth

GOOD = {"location": "always", "location_services": True, "notifications": True, "battery_unrestricted": True, "autostart_confirmed": True,
        "manufacturer": "Xiaomi", "model": "Redmi Note 12", "android": 14, "app_version": "1.0.0", "tracking_active": True}


def test_problems_are_plain_language():
    assert devhealth.problems(GOOD) == []
    bad = devhealth.problems({**GOOD, "location": "while_in_use", "battery_unrestricted": False, "autostart_confirmed": False})
    assert [p["code"] for p in bad] == ["location", "battery", "autostart"]
    assert devhealth.problems({**GOOD, "manufacturer": "Google", "autostart_confirmed": False}) == []     # Pixel has no autostart screen
    assert "switched off" in devhealth.likely_reason({**GOOD, "location_services": False})
    assert devhealth.likely_reason(None) == ""


def test_report_and_manager_view(client):
    ws, oh = signup(client, "Health Co", "o@health.com")
    mgr = mk_user(client, oh, "m@health.com", role="manager")
    emp = mk_user(client, oh, "e@health.com", manager_id=mgr["id"], name="Field Fred")
    other = mk_user(client, oh, "x@health.com", name="Unrelated")
    mh, eh = login(client, ws, "m@health.com"), login(client, ws, "e@health.com")
    r = client.post("/api/tracking/health", headers=eh, json={**GOOD, "battery_unrestricted": False})
    assert r.status_code == 200 and r.json()["problems"][0]["code"] == "battery"
    client.post("/api/tracking/health", headers=eh, json=GOOD)                 # re-report replaces, never duplicates
    client.post("/api/tracking/health", headers=eh, json={**GOOD, "battery_unrestricted": False, "stop_reason": "app_stopped"})
    rows = client.get("/api/tracking/health", headers=mh).json()
    byname = {x["name"]: x for x in rows}
    assert "Field Fred" in byname and "Unrelated" not in byname                  # managers see their own team only
    fred = byname["Field Fred"]
    assert fred["device"] == "Xiaomi Redmi Note 12" and [p["code"] for p in fred["problems"]] == ["battery", "stopped"]
    assert client.get("/api/tracking/health", headers=eh).status_code == 403     # employees cannot list the team
    assert client.post("/api/tracking/health", headers=eh, json={**GOOD, "android": "x"}).status_code == 422
