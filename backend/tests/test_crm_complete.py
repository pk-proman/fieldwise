import base64
import json
import time
from datetime import date, datetime, timedelta
import httpx
from conftest import signup, login
from test_app import mk_user, mk_product


def test_dedupe_stages_assignment_pool_import(client):
    ws, h = signup(client, "Nu Co", "o@nu.com")
    pid = mk_product(client, h, "N-1", 1000)
    a = mk_user(client, h, "a@nu.com", "employee")
    b = mk_user(client, h, "b@nu.com", "employee")
    base = {"name": "Buyer", "product_id": pid, "phone": "+91 98765-43210"}
    l1 = client.post("/api/crm/leads", headers=h, json=base)
    assert l1.status_code == 200
    dup = client.post("/api/crm/leads", headers=h, json={**base, "name": "Same buyer", "phone": "9876543210"})
    assert dup.status_code == 409 and "Duplicate" in dup.json()["detail"]
    rep = client.post("/api/crm/leads", headers=h, json={**base, "on_duplicate": "repeat", "name": "x", "notes": "needs 2 units"}).json()
    assert rep["id"] == l1.json()["id"] and rep["repeat_count"] == 2
    assert client.post("/api/crm/leads", headers=h, json={"name": "No contact", "product_id": pid}).status_code == 422
    # configurable stages
    st = client.get("/api/stages", headers=h).json()
    st.insert(3, {"key": "site_visit", "label": "Site visit", "kind": "open", "gate": "none", "prob": 25})
    assert client.put("/api/stages", headers=h, json=st).status_code == 200
    lid = l1.json()["id"]
    assert client.patch(f"/api/crm/leads/{lid}", headers=h, json={"status": "site_visit", "temperature": "hot"}).json()["status"] == "site_visit"
    assert client.put("/api/stages", headers=h, json=[s for s in st if s["key"] != "site_visit"]).status_code == 409  # in use
    bad = client.put("/api/stages", headers=h, json=[{"key": "x", "label": "X", "kind": "won", "gate": "none", "prob": 100}])
    assert bad.status_code == 422
    # round-robin assignment through intake
    key = client.get("/api/integrations/intake", headers=h).json()["key"]
    client.put("/api/assignment", headers=h, json={"mode": "round_robin", "default_user_ids": [a["id"], b["id"]], "rules": []})
    owners = []
    for i in range(4):
        r = client.post(f"/api/intake/{ws}/website?key={key}", json={"name": f"Web {i}", "phone": f"+91900000010{i}", "message": "need pump"}).json()
        assert r["STATUS"] == "SUCCESS"
        owners.append(client.get(f"/api/crm/leads/{[x for x in client.get('/api/crm/leads?q=Web+' + str(i), headers=h).json()['items']][0]['id']}", headers=h).json()["owner_id"])
    assert owners[0] != owners[1] and owners[0] == owners[2]
    # pool mode: no matching users -> common pool, claim by a rep
    client.put("/api/assignment", headers=h, json={"mode": "pool", "default_user_ids": [], "rules": []})
    client.post(f"/api/intake/{ws}/website?key={key}", json={"name": "Pool lead", "phone": "+919000000200"})
    pool = client.get("/api/pool", headers=h).json()
    assert len(pool) == 1 and pool[0]["owner_id"] is None
    ah = login(client, ws, "a@nu.com")
    assert client.post(f"/api/crm/leads/{pool[0]['id']}/claim", headers=ah).json()["owner_id"] == a["id"]
    assert client.post(f"/api/crm/leads/{pool[0]['id']}/claim", headers=ah).status_code == 409
    # CSV import with a duplicate + dry run
    csv_ = "Name,Mobile,Email,Company,Product,Requirement\nAsha,+919111111111,,Apex,,pump\nDup,9876543210,,X,,again\n,,,,,\n"
    dry = client.post("/api/import/leads?dry_run=true", headers=h, files={"file": ("l.csv", csv_.encode(), "text/csv")}).json()
    assert dry["created"] == 1 and dry["repeat"] == 1 and len(dry["errors"]) == 1
    real = client.post("/api/import/leads", headers=h, files={"file": ("l.csv", csv_.encode(), "text/csv")}).json()
    assert real["created"] == 1 and real["repeat"] == 1
    assert client.get(f"/api/crm/leads/{lid}", headers=h).json()["repeat_count"] == 3


def test_intake_sources(client):
    ws, h = signup(client, "Xi Co", "o@xi.com")
    pid = mk_product(client, h, "X-1", 500)
    key = client.get("/api/integrations/intake", headers=h).json()["key"]
    assert client.post(f"/api/intake/{ws}/indiamart?key=wrong", json={}).status_code == 401
    im = {"CODE": 200, "STATUS": "SUCCESS", "RESPONSE": {"UNIQUE_QUERY_ID": "Q1", "QUERY_ID": "Q1", "SENDER_NAME": "Ravi", "SENDER_MOBILE": "+91-9822000001", "SENDER_EMAIL": "ravi@x.com", "SENDER_COMPANY": "Apex Crushers",
                                                  "SENDER_CITY": "Pune", "SENDER_STATE": "Maharashtra", "QUERY_PRODUCT_NAME": "Prod X-1", "QUERY_MESSAGE": "Need 250 TPH plant"}}
    r = client.post(f"/api/intake/{ws}/indiamart?key={key}", json=im).json()
    assert r["result"] == "created"
    again = client.post(f"/api/intake/{ws}/indiamart?key={key}", json=im).json()
    assert again["result"] == "duplicate"                                 # same QUERY_ID is idempotent
    lead = client.get("/api/crm/leads?q=Ravi", headers=h).json()["items"][0]
    assert lead["source"] == "IndiaMART" and lead["company"] == "Apex Crushers" and lead["product_id"] == pid and lead["region"] == "Maharashtra"
    jd = client.post(f"/api/intake/{ws}/justdial?key={key}", data={"name": "Meena", "mobile": "9822000002", "category": "Unknown thing", "city": "Nashik", "leadid": "J9"}).json()
    assert jd["result"] == "created"
    meena = client.get("/api/crm/leads?q=Meena", headers=h).json()["items"][0]
    assert meena["product_name"] and "Unspecified" in meena["product_name"] and "needs-product" in meena["tags"]
    assert client.post(f"/api/intake/{ws}/website?key={key}", json={"name": "No contact"}).json()["result"] == "rejected"
    # repeat enquiry from another portal bumps the counter instead of duplicating
    rp = client.post(f"/api/intake/{ws}/tradeindia?key={key}", json={"sender_name": "Ravi K", "sender_mobile": "9822000001", "message": "again"}).json()
    assert rp["result"] == "repeat"
    # facebook: handshake + lead fetched from Graph API
    assert client.get(f"/api/intake/{ws}/facebook", params={"hub.verify_token": key, "hub.challenge": "abc"}).text == "abc"
    assert client.get(f"/api/intake/{ws}/facebook", params={"hub.verify_token": "no", "hub.challenge": "abc"}).status_code == 401
    from app.routers import intake as itk
    from app.db import SessionLocal
    from app.models import Tenant
    from sqlalchemy import select
    client.put("/api/integrations/intake", headers=h, json={"fb_page_token": "PAGE-TOKEN"})
    db = SessionLocal()
    t = db.scalar(select(Tenant).where(Tenant.slug == ws))
    mock = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"id": "777", "field_data": [{"name": "full_name", "values": ["Fb Person"]}, {"name": "phone_number", "values": ["+919822000009"]}, {"name": "email", "values": ["fb@x.com"]}]})))
    st, msg, lid = itk.process(db, t, "facebook", {"entry": [{"changes": [{"value": {"leadgen_id": "777"}}]}]}, mock)
    db.commit(); db.close()
    assert st == "created" and msg == "Fb Person"
    m = client.post("/api/integrations/intake/test", headers=h, json={"source": "indiamart", "payload": im}).json()
    assert m["mapped"]["email"] == "ravi@x.com" and "repeat" in m["would"]
    assert any(l["status"] == "duplicate" for l in client.get("/api/integrations/intake/logs", headers=h).json())


def test_quotation_pdf_send_numbering_invoices(client):
    ws, h = signup(client, "Omicron Co", "o@omi.com")
    pid = mk_product(client, h, "O-1", 1000)
    client.put("/api/company", headers=h, json={"name": "Omicron Industries", "gstin": "29ABCDE1234F1Z5", "address": "Plot 5", "city": "Bengaluru", "bank": {"name": "HDFC", "account": "123", "ifsc": "HDFC0001"}})
    assert client.put("/api/company", headers=h, json={"gstin": "short"}).status_code == 422
    client.put("/api/quote-settings", headers=h, json={"numbering": {"quote": {"prefix": "OMI/Q/", "next": 101}, "invoice": {"prefix": "OMI/INV/", "next": 7}}, "terms": "Pay in 30 days."})
    cust = client.post("/api/crm/customers", headers=h, json={"name": "Buyer Co", "phone": "+919822000000", "email": "buy@co.com", "credit_days": 45}).json()
    lead = client.post("/api/crm/leads", headers=h, json={"name": "Q Lead", "company": "Buyer Co", "phone": "+919000000300", "email": "ql@co.com", "product_id": pid}).json()
    client.post(f"/api/crm/leads/{lead['id']}/qualify", headers=h, json={"budget": 2, "authority": 2, "need": 2, "timeline": 2})
    q = client.post("/api/quotations", headers=h, json={"lead_id": lead["id"], "items": [{"product_id": pid, "qty": 2}], "charges": [{"name": "Freight", "amount": 500}], "tax_pct": 18}).json()
    assert q["number"] == "OMI/Q/0101" and q["charges_total"] == 500 and q["total"] == round((2000 + 500) * 1.18, 2) and q["terms"] == "Pay in 30 days."
    pdf = client.get(f"/api/quotations/{q['id']}/pdf", headers=h)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF") and len(pdf.content) > 1500
    tok = h["Authorization"][7:]
    assert client.get(f"/api/quotations/{q['id']}/pdf-view?t={tok}").content.startswith(b"%PDF")
    assert client.get(f"/api/quotations/{q['id']}/pdf-view?t=bad").status_code == 401
    em = client.post(f"/api/quotations/{q['id']}/send", headers=h, json={"channel": "email", "base_url": "http://x"}).json()
    assert em["sent"] is False and "SMTP" in em["detail"]                       # no SMTP configured
    wa = client.post(f"/api/quotations/{q['id']}/send", headers=h, json={"channel": "whatsapp", "to": "9000000300", "base_url": "http://x", "mark_sent": True}).json()
    assert wa["wa_link"].startswith("https://wa.me/919000000300?text=") and "OMI/Q/0101" in wa["message"] and wa["quotation"]["status"] == "sent"
    link = wa["message"].split("valid till")[-1].split(": ")[-1].split("\n")[0].strip()
    path = link.replace("http://x", "")
    assert client.get(path).content.startswith(b"%PDF")                         # signed public link works
    assert client.get(path[:-12] + "0" * 8 + ".pdf").status_code == 404        # tampered signature
    # custom PDF replaces the generated one
    custom = "data:application/pdf;base64," + base64.b64encode(b"%PDF-1.4 my own quote").decode()
    client.post(f"/api/quotations/{q['id']}/custom-pdf", headers=h, json={"data": custom})
    assert b"my own quote" in client.get(f"/api/quotations/{q['id']}/pdf", headers=h).content
    # order -> proforma -> tax invoice -> TDS collection
    client.post(f"/api/quotations/{q['id']}/status", headers=h, json={"status": "accepted"})
    client.patch(f"/api/crm/leads/{lead['id']}", headers=h, json={"status": "won"})
    cid = client.get(f"/api/crm/leads/{lead['id']}", headers=h).json()["converted_customer_id"]
    po = "data:application/pdf;base64," + base64.b64encode(b"%PDF-1.4 po").decode()
    o = client.post("/api/orders", headers=h, json={"quotation_id": q["id"], "delivery_date": (date.today() + timedelta(days=9)).isoformat(), "po_file": po, "payment_terms": "Net 45 days"}).json()
    assert o["sales_type"] == "new" and o["payment_terms"] == "Net 45 days"
    pf = client.post(f"/api/orders/{o['id']}/proforma", headers=h, json={}).json()
    assert pf["kind"] == "proforma" and pf["outstanding"] == 0 and pf["number"].startswith("PI-")
    inv = client.post(f"/api/orders/{o['id']}/invoice", headers=h, json={"tds_pct": 2}).json()
    assert inv["number"] == "OMI/INV/0007" and inv["outstanding"] == o["total"] and inv["due_date"] == (date.today() + timedelta(days=45)).isoformat()
    assert client.post(f"/api/orders/{o['id']}/invoice", headers=h, json={}).status_code == 409
    assert client.get(f"/api/invoices/{inv['id']}/pdf-view?t={tok}").content.startswith(b"%PDF")
    out = client.get("/api/collections/outstanding", headers=h).json()
    assert out["total_outstanding"] == round(o["total"])                         # proforma doesn't count
    tds = round(o["total"] * 0.02, 2)
    pay = client.post("/api/collections", headers=h, json={"customer_id": cid, "invoice_id": inv["id"], "amount": round(o["total"] - tds, 2), "tds_amount": tds, "mode": "bank", "reference": "UTR1"})
    assert pay.status_code == 200, pay.text
    mgr_pay = pay.json()
    ol = [x for x in client.get("/api/orders?scope=mine", headers=h).json() if x["id"] == o["id"]][0]
    assert ol["invoice_count"] == 1 and ol["balance_payment"] == o["total"]


def test_calls_dialer_renewals_events_notes_search(client):
    ws, h = signup(client, "Pi Co", "o@pi.com")
    pid = mk_product(client, h, "P-1", 100)
    key = client.get("/api/integrations/intake", headers=h).json()["key"]
    me = client.get("/api/auth/me", headers=h).json()["user"]
    lead = client.post("/api/crm/leads", headers=h, json={"name": "Known", "phone": "+919333300001", "product_id": pid, "email": "k@x.com", "city": "Nagpur"}).json()
    c = client.post("/api/calls", headers=h, json={"number": "93333 00001", "direction": "outgoing", "duration_sec": 125, "notes": "Discussed price"}).json()
    assert c["lead_id"] == lead["id"] and c["name"] == "Known"
    assert client.get("/api/crm/leads?q=Known", headers=h).json()["items"][0]["call_attempts"] >= 1
    # missed call from an unknown number creates a lead
    w = client.post(f"/api/intake/{ws}/calllog?key={key}", json={"from": "+919444400002", "direction": "missed", "start": "2026-11-05T10:00:00Z"}).json()
    assert w["lead_id"]
    assert client.post(f"/api/intake/{ws}/calllog?key=bad", json={"from": "1"}).status_code == 401
    # click-to-call without a gateway falls back to tel:
    d = client.post("/api/calls/dial", headers=h, json={"to": "+919333300001"}).json()
    assert d["connected"] is False and d["tel"].startswith("tel:")
    # auto dialer: campaign of 4 numbers over 2 callers
    emp = mk_user(client, h, "e@pi.com", "employee")
    cp = client.post("/api/campaigns", headers=h, json={"name": "Cold list", "mode": "auto", "user_ids": [me["id"], emp["id"]], "contacts": [{"number": f"+91955550000{i}", "name": f"N{i}"} for i in range(4)] + [{"number": "bad"}]}).json()
    assert cp["contacts"] == 4
    nxt = client.get("/api/campaigns-next", headers=h).json()
    assert nxt["remaining"] == 2 and nxt["contact"]["campaign"] == "Cold list"
    cid = nxt["contact"]["id"]
    assert client.post(f"/api/campaigns/contacts/{cid}/dispose", headers=h, json={"disposition": "call_back"}).status_code == 422
    r = client.post(f"/api/campaigns/contacts/{cid}/dispose", headers=h, json={"disposition": "lead_created", "lead": {"name": "Dialed Lead", "product_id": pid}, "notes": "wants quote"}).json()
    assert r["status"] == "disposed" and r["lead_id"]
    nxt2 = client.get("/api/campaigns-next", headers=h).json()
    assert nxt2["remaining"] == 1
    assert client.post(f"/api/campaigns/contacts/{nxt2['contact']['id']}/dispose", headers=h, json={"disposition": "not_picked"}).json()["status"] == "pending"   # retried later
    st = client.get("/api/campaigns", headers=h).json()[0]
    assert st["created_leads"] == 1 and st["total"] == 4
    # renewals
    cust = client.post("/api/crm/customers", headers=h, json={"name": "Renew Co", "phone": "+919666600001"}).json()
    rn = client.post("/api/renewals", headers=h, json={"customer_id": cust["id"], "title": "AMC 2026", "amount": 50000, "renewal_date": (date.today() + timedelta(days=20)).isoformat()}).json()
    assert client.get("/api/renewals", headers=h).json()[0]["days_left"] == 20
    assert client.post(f"/api/renewals/{rn['id']}/to-lead", headers=h).status_code == 200
    csvr = f"customer,title,amount,date\nRenew Co,Warranty,1000,{(date.today() + timedelta(days=5)).isoformat()}\nGhost Co,x,1,2026-01-01\n"
    imp = client.post("/api/import/renewals", headers=h, files={"file": ("r.csv", csvr.encode(), "text/csv")}).json()
    assert imp["created"] == 1 and len(imp["errors"]) == 1
    # events
    ev = client.post("/api/events", headers=h, json={"name": "Bauma 2026", "venue": "Delhi", "stall_no": "H2-14", "start_date": date.today().isoformat(), "end_date": (date.today() + timedelta(days=3)).isoformat(), "budget": 100000}).json()
    client.post("/api/crm/leads", headers=h, json={"name": "Expo Lead", "phone": "+919777700001", "product_id": pid, "event_id": ev["id"]})
    e = client.get("/api/events?when=ongoing", headers=h).json()[0]
    assert e["leads"] == 1 and e["cost_per_lead"] == 100000
    # notes with replies and unread
    emp_h = login(client, ws, "e@pi.com")
    n = client.post("/api/notes", headers=h, json={"entity": "lead", "entity_id": lead["id"], "text": "Please call him", "mentions": [emp["id"]]}).json()
    assert [x["id"] for x in client.get("/api/notes?unread=true", headers=emp_h).json()] == [n["id"]]
    client.post("/api/notes", headers=emp_h, json={"entity": "lead", "entity_id": lead["id"], "text": "On it", "parent_id": n["id"]})
    mine = client.get("/api/notes", headers=h).json()[0]
    assert mine["has_unread"] and len(mine["replies"]) == 1
    client.post(f"/api/notes/{n['id']}/read", headers=h)
    assert client.get("/api/notes?unread=true", headers=h).json() == []
    # global search
    s = client.get("/api/search?q=9333300001", headers=h).json()
    assert s["leads"][0]["title"] == "Known"
    assert client.get("/api/search?q=Nagpur&field=city", headers=h).json()["leads"][0]["title"] == "Known"
    assert client.get("/api/search?q=Renew", headers=h).json()["customers"][0]["title"] == "Renew Co"


def test_security_people_purchase_ai(client):
    from app.security import _hotp
    ws, h = signup(client, "Rho Co", "o@rho.com")
    # MFA
    setup = client.post("/api/auth/mfa/setup", headers=h).json()
    assert setup["uri"].startswith("otpauth://totp/")
    code = _hotp(setup["secret"], int(time.time() // 30))
    assert client.post("/api/auth/mfa/enable", headers=h, json={"code": "000000"}).status_code == 400
    assert client.post("/api/auth/mfa/enable", headers=h, json={"code": code}).status_code == 200
    r = client.post("/api/auth/login", json={"workspace": ws, "email": "o@rho.com", "password": "Passw0rd!x"})
    assert r.status_code == 428
    assert client.post("/api/auth/login", json={"workspace": ws, "email": "o@rho.com", "password": "Passw0rd!x", "otp": "123456"}).status_code == 401
    ok = client.post("/api/auth/login", json={"workspace": ws, "email": "o@rho.com", "password": "Passw0rd!x", "otp": _hotp(setup["secret"], int(time.time() // 30))})
    assert ok.status_code == 200
    h2 = {"Authorization": "Bearer " + ok.json()["token"]}
    assert any(a["reason"] == "wrong MFA code" for a in client.get("/api/security/login-attempts?failed=true", headers=h2).json())
    client.put("/api/settings", headers=h2, json={"mfa_mode": "required", "session_hours": 2})
    me = client.get("/api/auth/me", headers=h2).json()["user"]
    # departments / designations / incentives
    d = client.post("/api/departments", headers=h2, json={"name": "Sales"}).json()
    g = client.post("/api/designations", headers=h2, json={"name": "Sales Executive"}).json()
    emp = mk_user(client, h2, "e@rho.com", "employee")
    client.patch(f"/api/users/{emp['id']}", headers=h2, json={"department_id": d["id"], "designation_id": g["id"]})
    assert client.delete(f"/api/departments/{d['id']}", headers=h2).status_code == 409
    plan = client.post("/api/incentives/plans", headers=h2, json={"name": "Orders slab", "metric": "orders", "user_ids": [emp["id"]], "slabs": [{"from": 0, "to": 100000, "pct": 1}, {"from": 100000, "to": None, "pct": 2, "flat": 500}]})
    assert plan.status_code == 200, plan.text
    assert client.post("/api/incentives/plans", headers=h2, json={"name": "bad", "metric": "orders", "slabs": [{"from": 0, "to": 100, "pct": 1}, {"from": 50, "to": None, "pct": 2}]}).status_code == 422
    from app.routers.people import payout
    assert payout(plan.json()["slabs"], 250000)[0] == 5500 and payout(plan.json()["slabs"], 50000)[0] == 500
    assert client.get("/api/incentives/report", headers=h2).json()["rows"][0]["incentive"] == 0
    # vendors / PO / dealers / helpdesk
    v = client.post("/api/vendors", headers=h2, json={"name": "Steel Supply", "phone": "9"}).json()
    po = client.post("/api/purchase-orders", headers=h2, json={"vendor_id": v["id"], "items": [{"description": "Plates", "qty": 10, "rate": 250}]}).json()
    assert po["total"] == 2500 and po["number"] == "PO-0001"
    assert client.post(f"/api/purchase-orders/{po['id']}/status", headers=h2, json={"status": "received"}).status_code == 409
    assert client.post(f"/api/purchase-orders/{po['id']}/status", headers=h2, json={"status": "issued"}).status_code == 200
    assert client.get("/api/vendors", headers=h2).json()[0]["po_count"] == 1
    assert client.post("/api/dealers", headers=h2, json={"name": "Dealer One", "services": ["Spares"]}).status_code == 200
    eh = login(client, ws, "e@rho.com")
    t = client.post("/api/tickets", headers=eh, json={"subject": "Can't log in on phone", "kind": "Login related issues"}).json()
    assert t["number"] == "TKT-0001"
    assert [x["id"] for x in client.get("/api/tickets", headers=eh).json()] == [t["id"]]
    assert client.post(f"/api/tickets/{t['id']}/update", headers=eh, json={"status": "closed"}).status_code == 403
    client.post(f"/api/tickets/{t['id']}/update", headers=h2, json={"status": "closed"})
    assert any(n["title"].endswith("closed") for n in client.get("/api/notifications", headers=eh).json()["items"])
    # masters
    assert "Price too high" in [m["name"] for m in client.get("/api/masters?kind=regret_reason", headers=h2).json()]
    client.post("/api/masters", headers=h2, json={"kind": "region", "name": "West"})
    assert [m["name"] for m in client.get("/api/masters?kind=region", headers=h2).json()] == ["West"]
    # AI: rules fallback, then provider via mock
    pid = mk_product(client, h2, "R-1", 10)
    lead = client.post("/api/crm/leads", headers=h2, json={"name": "AI Lead", "phone": "+919888800001", "product_id": pid}).json()
    a0 = client.post("/api/ai/lead-analyze", headers=h2, json={"lead_id": lead["id"]}).json()
    assert a0["source"] == "rules" and a0["next_steps"]
    client.put("/api/integrations/ai", headers=h2, json={"enabled": True, "provider": "anthropic", "key": "sk-test-123"})
    assert "key" not in client.get("/api/integrations/ai", headers=h2).json() and client.get("/api/integrations/ai", headers=h2).json()["has_key"]
    from app.routers import ai as aimod
    seen = {}

    def handler(req):
        seen["key"], seen["body"] = req.headers.get("x-api-key"), json.loads(req.content)
        return httpx.Response(200, json={"content": [{"type": "text", "text": 'Here: {"summary": "Warm lead", "risks": ["slow"], "next_steps": ["Call today"], "suggested_followup_days": 2, "draft_message": "Hi"}'}]})
    aimod._client_factory = lambda: httpx.Client(transport=httpx.MockTransport(handler))
    a1 = client.post("/api/ai/lead-analyze", headers=h2, json={"lead_id": lead["id"]}).json()
    aimod._client_factory = None
    assert a1["source"] == "anthropic" and a1["next_steps"] == ["Call today"] and seen["key"] == "sk-test-123" and "AI Lead" in seen["body"]["messages"][0]["content"]
    # overview
    ov = client.get("/api/reports/overview", headers=h2).json()
    assert len(ov["series"]) == 6 and "new_leads" in ov["compare"] and ov["opportunity"]["open"] >= 0
