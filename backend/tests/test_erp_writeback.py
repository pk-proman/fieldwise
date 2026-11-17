import json
import httpx
from datetime import date, timedelta
from conftest import signup, login


def test_leave_and_expense_write_back_to_erpnext(client):
    from app.integrations import erpnext as erp
    from app.db import SessionLocal
    from app.models import User, Tenant
    from sqlalchemy import select
    ws, h = signup(client, "WbCo", "o@wb.com")
    tid = client.get("/api/auth/me", headers=h).json()["tenant"]["id"]
    mk = lambda n, e, m=None, role="employee": client.post("/api/users", headers=h, json={"name": n, "email": e, "password": "Passw0rd!x", "role": role, "manager_id": m}).json()["id"]
    boss, rep = mk("Boss", "boss@wb.com", role="manager"), None
    rep = mk("Rep", "rep@wb.com", boss)
    with SessionLocal() as db:
        db.get(User, rep).erp_employee = "HR-EMP-7"
        db.commit()
    sent, fail_leave = [], {"on": True}

    def handler(req: httpx.Request):
        p = req.url.path
        if req.method == "POST":
            body = json.loads(req.content)
            sent.append((p, body))
            if p.endswith("Leave Application") and fail_leave["on"]:
                return httpx.Response(417, json={"exception": "Leave Type Casual Leave is not valid"})
            return httpx.Response(200, json={"data": {"name": "HR-LAP-1" if "Leave" in p else "HR-EXP-1"}})
        if req.method == "GET" and p.endswith("Leave Type"):
            return httpx.Response(200, json={"data": [{"name": "Privilege Leave", "max_leaves_allowed": 18, "is_lwp": 0}, {"name": "Leave Without Pay", "max_leaves_allowed": 0, "is_lwp": 1}]})
        return httpx.Response(200, json={"data": []})
    mt = httpx.MockTransport(handler)
    assert client.put("/api/erp/config", headers=h, json={"enabled": True, "url": "https://erp.example", "api_key": "K", "api_secret": "S", "company": "WbCo"}).status_code == 200
    rh, bh = login(client, ws, "rep@wb.com"), login(client, ws, "boss@wb.com")
    types = client.get("/api/leaves/types", headers=rh).json()
    d = date.today() + timedelta(days=14)
    while d.weekday() in (5, 6):
        d += timedelta(days=1)
    lv = client.post("/api/leaves", headers=rh, json={"leave_type_id": types[0]["id"], "from_day": d.isoformat(), "to_day": d.isoformat(), "reason": "Family function"}).json()
    assert client.post(f"/api/leaves/{lv['id']}/decide", headers=bh, json={"decision": "approved"}).status_code == 200
    # first attempt fails inside ERPNext: the reason is kept and shown, nothing is lost
    erp.run_sync(tid, "leave", mt)
    row = next(x for x in client.get("/api/leaves?scope=mine", headers=rh).json() if x["id"] == lv["id"])
    assert row["erp_ref"] == "" and "not valid" in row["erp_error"]
    fail_leave["on"] = False
    assert erp.run_sync(tid, "leave", mt) == {"leave": 1}                    # the hourly retry
    row = next(x for x in client.get("/api/leaves?scope=mine", headers=rh).json() if x["id"] == lv["id"])
    assert row["erp_ref"] == "HR-LAP-1" and row["erp_error"] == ""
    doc = [b for p, b in sent if p.endswith("Leave Application")][-1]
    assert doc["employee"] == "HR-EMP-7" and doc["from_date"] == d.isoformat() and doc["status"] == "Approved" and doc["docstatus"] == 1
    assert erp.run_sync(tid, "leave", mt) == {"leave": 0}                    # never written twice
    # leave types come from ERPNext
    assert erp.run_sync(tid, "leave_types", mt) == {"leave_types": 2}
    assert "Privilege Leave" in [t["name"] for t in client.get("/api/leaves/types", headers=rh).json()]
    # expense claim: written once approved
    e = client.post("/api/expenses", headers=rh, data={"day": date.today().isoformat(), "category": "food", "amount": "320", "description": "Client lunch"}).json()
    assert client.post("/api/expenses/decide", headers=bh, json={"ids": [e["id"]], "decision": "approved"}).status_code == 200
    assert erp.run_sync(tid, "expenses", mt) == {"expenses": 1}
    ex = [b for p, b in sent if p.endswith("Expense Claim")][-1]
    assert ex["employee"] == "HR-EMP-7" and ex["expenses"][0]["amount"] == 320
    mine = client.get("/api/expenses", headers=rh).json()
    assert next(x for x in mine if x["id"] == e["id"])["erp_ref"] == "HR-EXP-1"
