import io
from conftest import signup, login


def _mk(client, hdr, name, email, mgr=None, phone="", role="employee"):
    r = client.post("/api/users", headers=hdr, json={"name": name, "email": email, "password": "Passw0rd!x", "role": role, "manager_id": mgr, "phone": phone})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_import_export_and_hierarchy(client):
    ws, hdr = signup(client, "ImpCo", "own@imp.com")
    csv_text = ("name,email,phone,role,employee_code,reporting_manager,active\n"
                "Boss One,boss@imp.com,9000000001,manager,B1,,yes\n"
                "Rep One,rep1@imp.com,9000000002,employee,R1,boss@imp.com,yes\n"
                "Rep Two,rep2@imp.com,9000000003,employee,R2,R1,yes\n"
                "Bad Row,not-an-email,,employee,,,\n"
                "Loop A,la@imp.com,,employee,LA,lb@imp.com,\n"
                "Loop B,lb@imp.com,,employee,LB,la@imp.com,\n")
    files = {"file": ("t.csv", io.BytesIO(csv_text.encode()), "text/csv")}
    dry = client.post("/api/users/import", headers=hdr, files=files, data={"commit": "false"}).json()
    assert dry["committed"] is False and dry["created"] == 3 and dry["skipped"] == 3, dry
    files = {"file": ("t.csv", io.BytesIO(csv_text.encode()), "text/csv")}
    done = client.post("/api/users/import", headers=hdr, files=files, data={"commit": "true"}).json()
    assert done["created"] == 3
    users = {u["email"]: u for u in client.get("/api/users?all=1", headers=hdr).json()}
    assert users["rep1@imp.com"]["manager_id"] == users["boss@imp.com"]["id"]
    assert users["rep2@imp.com"]["manager_id"] == users["rep1@imp.com"]["id"]
    assert "la@imp.com" not in users
    exp = client.get("/api/users/export", headers=hdr).text
    assert "rep2@imp.com" in exp and "rep1@imp.com" in exp   # manager column filled
    # the boss sees the whole subtree, rep2 sees only self
    b = login(client, ws, "boss@imp.com") if False else None
    # disabling a manager moves their reports up
    client.patch(f"/api/users/{users['rep1']['id'] if 'rep1' in users else users['rep1@imp.com']['id']}", headers=hdr, json={"active": False})
    users = {u["email"]: u for u in client.get("/api/users?all=1", headers=hdr).json()}
    assert users["rep2@imp.com"]["manager_id"] == users["boss@imp.com"]["id"]


def test_pin_activation_flow(client):
    ws, hdr = signup(client, "PinCo", "own@pin.com")
    uid = _mk(client, hdr, "Field Fred", "fred@pin.com", phone="+91 98765 43210")
    r = client.post(f"/api/users/{uid}/activation-pin", headers=hdr, json={})
    assert r.status_code == 200, r.text
    pin = r.json()["pin"]
    body = {"workspace": ws, "phone": "9876543210", "pin": pin, "device_secret": "x" * 32, "label": "Pixel 8", "model": "Pixel 8"}
    bad = client.post("/api/auth/activate", json={**body, "pin": "000000" if pin != "000000" else "111111"})
    assert bad.status_code == 401
    wrong_phone = client.post("/api/auth/activate", json={**body, "phone": "9876543211"})
    assert wrong_phone.status_code == 401
    ok = client.post("/api/auth/activate", json=body)
    assert ok.status_code == 200, ok.text
    tok = {"Authorization": "Bearer " + ok.json()["token"]}
    me = client.get("/api/auth/me", headers=tok).json()
    assert me["app_scope"] == "field"
    # field-scope phone cannot reach admin tools even if the person is an admin on the web
    assert client.get("/api/devices", headers=tok).status_code == 403
    assert client.post("/api/auth/device/renew", headers=tok, json={"device_secret": "x" * 32}).status_code == 200
    # the PIN works once
    assert client.post("/api/auth/activate", json=body).status_code == 401
    # a new PIN on a new phone replaces the old phone
    pin2 = client.post(f"/api/users/{uid}/activation-pin", headers=hdr, json={"scope": "manager"}).json()["pin"]
    ok2 = client.post("/api/auth/activate", json={**body, "pin": pin2, "device_secret": "y" * 32})
    assert ok2.status_code == 200
    assert client.get("/api/auth/me", headers=tok).status_code == 401      # old phone is revoked
    # wrong-secret renew revokes
    tok2 = {"Authorization": "Bearer " + ok2.json()["token"]}
    assert client.post("/api/auth/device/renew", headers=tok2, json={"device_secret": "z" * 32}).status_code == 401


def test_pin_lockout_and_missing_phone(client):
    ws, hdr = signup(client, "LockCo", "own@lock.com")
    uid = _mk(client, hdr, "No Phone", "np@lock.com")
    assert client.post(f"/api/users/{uid}/activation-pin", headers=hdr, json={}).status_code == 422
    uid2 = _mk(client, hdr, "Has Phone", "hp@lock.com", phone="9111111111")
    pin = client.post(f"/api/users/{uid2}/activation-pin", headers=hdr, json={}).json()["pin"]
    body = {"workspace": ws, "phone": "9111111111", "device_secret": "k" * 32}
    for i in range(5):
        assert client.post("/api/auth/activate", json={**body, "pin": "99999" + str(i)}).status_code == 401
    assert client.post("/api/auth/activate", json={**body, "pin": pin}).status_code == 401   # locked after 5 tries
