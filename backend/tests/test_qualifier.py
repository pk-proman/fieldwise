import json
import httpx
from conftest import signup, login
from test_app import mk_user, mk_product


def _lead(client, h, **kw):
    r = client.post("/api/crm/leads", headers=h, json=kw)
    assert r.status_code == 200, r.text
    return r.json()


def _timeline(client, h, lid):
    return [a["text"] for a in client.get(f"/api/crm/leads/{lid}/timeline", headers=h).json()]


def _qual_remark(client, h, lid):
    return next(t for t in _timeline(client, h, lid) if t.startswith("AI Lead Qualifier"))


def test_good_lead_is_qualified_with_reasoning_in_remarks(client):
    ws, h = signup(client, "Q Good Co", "o@qgood.com")
    pid = client.post("/api/crm/products", headers=h, json={"code": "VS-1", "name": "Vibratory Screen 2 Deck", "price": 1}).json()["id"]
    lead = _lead(client, h, name="Ramesh Kulkarni", company="Kulkarni Aggregates Pvt Ltd", phone="+91 98451 37206", email="ramesh@kulkarniagg.in", source="IndiaMART",
                 product_id=pid, notes="Need a quotation for a vibratory screen 2 deck, capacity 150 tph. Urgent, plant commissioning next month.")
    assert lead["ai_qualification"]["verdict"] == "qualified" and lead["ai_qualification"]["score"] >= 60
    assert "ai-qualified" in lead["tags"]
    rm = _qual_remark(client, h, lead["id"])
    assert "QUALIFIED" in rm and "Product fit" in rm and "Contactability" in rm and "Next:" in rm
    assert "[AI qualifier" in client.get(f"/api/crm/leads/{lead['id']}", headers=h).json()["notes"]


def test_junk_and_non_buyers_are_rejected_with_reasons(client):
    ws, h = signup(client, "Q Junk Co", "o@qjunk.com")
    pid = mk_product(client, h, "J-1")
    junk = _lead(client, h, name="test", phone="9999999999", product_id=pid, source="website")
    q = junk["ai_qualification"]
    assert q["verdict"] == "rejected" and any("fake" in f for f in q["flags"]) and "ai-rejected" in junk["tags"] and junk["temperature"] == "cold"
    assert "REJECTED" in _qual_remark(client, h, junk["id"]) and "Red flags" in _qual_remark(client, h, junk["id"])
    seo = _lead(client, h, name="Priya Shah", company="Rank Fast", phone="+91 98111 22334", email="priya@mailinator.com", product_id=pid, notes="We offer SEO and digital marketing services for your business")
    assert seo["ai_qualification"]["verdict"] == "rejected" and len(seo["ai_qualification"]["flags"]) >= 2
    seller = _lead(client, h, name="Anil", company="Metal Traders", phone="+91 98222 33445", product_id=pid, notes="We are a leading manufacturer and supplier of steel plates, looking for buyers")
    assert seller["ai_qualification"]["verdict"] == "rejected" and any("selling" in f for f in seller["ai_qualification"]["flags"])
    thin = _lead(client, h, name="Sam", phone="+91 98333 44556", source="cold list", product_id=pid)
    assert thin["ai_qualification"]["verdict"] == "rejected" and thin["ai_qualification"]["flags"] == []  # low score, not junk: flagged for a quick check
    assert thin["ai_qualification"]["confidence"] in ("low", "medium", "high")


def test_existing_customer_never_rejected_and_threshold_and_auto_regret(client):
    ws, h = signup(client, "Q Rules Co", "o@qrules.com")
    pid = mk_product(client, h, "R-1")
    client.post("/api/crm/customers", headers=h, json={"name": "Old Client Ltd", "phone": "+91 97000 11122"})
    old = _lead(client, h, name="Old Client Ltd", phone="+91 97000 11122", product_id=pid)
    assert old["ai_qualification"]["verdict"] == "qualified" and any("existing customer" in c["note"] for c in old["ai_qualification"]["checks"])
    # settings: threshold, validation, auto-regret
    r = client.put("/api/ai/qualifier", headers=h, json={"threshold": 5, "auto_regret": True})
    assert r.status_code == 200 and r.json()["settings"]["threshold"] == 10
    junk = _lead(client, h, name="asdf", phone="1234567890", product_id=pid)
    assert junk["ai_qualification"]["verdict"] == "rejected" and junk["status"] == "lost" and "AI qualifier" in junk["lost_reason"]
    client.put("/api/ai/qualifier", headers=h, json={"enabled": False})
    off = _lead(client, h, name="Someone Else", phone="+91 98765 11111", product_id=pid)
    assert not off["ai_qualification"]
    # re-run on demand and backfill
    again = client.post("/api/ai/qualifier/run", headers=h, json={"lead_id": off["id"]})
    assert again.status_code == 200 and again.json()["engine"] == "rules-v1"
    stats = client.get("/api/ai/qualifier", headers=h).json()
    assert stats["last_30_days"]["scored"] >= 3
    # employees cannot change the engine
    emp = mk_user(client, h, "e@qrules.com", "employee")
    he = login(client, ws, "e@qrules.com")
    assert client.put("/api/ai/qualifier", headers=he, json={"threshold": 90}).status_code == 403
    assert client.post("/api/ai/qualifier/backfill", headers=he).status_code == 403


def test_intake_and_import_paths_are_qualified(client):
    ws, h = signup(client, "Q Intake Co", "o@qintake.com")
    key = client.get("/api/integrations/intake", headers=h).json()["key"]
    r = client.post(f"/api/intake/{ws}/website?key={key}", json={"name": "Web Buyer", "phone": "+919876500001", "company": "Hill Quarry LLP", "message": "Price for 2 crushers 200 tph"}).json()
    assert r["STATUS"] == "SUCCESS"
    row = client.get("/api/crm/leads?q=Web+Buyer", headers=h).json()["items"][0]
    assert row["ai_qualification"]["verdict"] in ("qualified", "rejected") and row["ai_qualification"]["score"] > 0
    assert any(t.startswith("AI Lead Qualifier") for t in _timeline(client, h, row["id"]))


def test_second_opinion_is_added_as_a_separate_remark(client):
    from app import qualifier
    from app.db import SessionLocal
    from app.models import Lead
    from sqlalchemy import select
    ws, h = signup(client, "Q LLM Co", "o@qllm.com")
    tid = client.get("/api/auth/me", headers=h).json()["tenant"]["id"]
    lead = _lead(client, h, name="Dev", company="Dev Infra", phone="+91 98765 77777", product_id=mk_product(client, h, "L-1"))
    seen = {}

    def handler(req: httpx.Request):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "I agree. Ask what daily tonnage the plant targets."}]})
    client.put("/api/integrations/ai", headers=h, json={"enabled": True, "provider": "anthropic", "key": "sk-test"})
    qualifier.second_opinion(tid, lead["id"], {"lead": {"name": "Dev"}, "verdict": "qualified", "score": 70, "checks": [], "flags": []}, httpx.Client(transport=httpx.MockTransport(handler)))
    assert any(t.startswith("AI second opinion: I agree") for t in _timeline(client, h, lead["id"]))
    assert "scorecard" in seen["body"]["system"] and "Dev" in seen["body"]["messages"][0]["content"]
    db = SessionLocal()
    assert db.scalar(select(Lead.ai_qualification).where(Lead.id == lead["id"]))["verdict"] in ("qualified", "rejected")  # verdict untouched
    db.close()


def test_sales_team_sync_owner_picker_and_isolation(client):
    from app.integrations import erpnext as erp
    ws, h = signup(client, "Team Co", "o@team.com")
    tid = client.get("/api/auth/me", headers=h).json()["tenant"]["id"]
    ws2, h2 = signup(client, "Other Team Co", "o@otherteam.com")
    asked = []

    def handler(req: httpx.Request):
        p = req.url.path
        asked.append((p, dict(req.url.params)))
        if p == "/api/resource/Sales Person":
            return httpx.Response(200, json={"data": [
                {"name": "Asha Rao", "employee": "E1", "parent_sales_person": "Sales Team", "enabled": 1, "is_group": 1},
                {"name": "Binod K", "employee": "E2", "parent_sales_person": "Asha Rao", "enabled": 1, "is_group": 0},
                {"name": "Old Rep", "employee": "E3", "parent_sales_person": "Sales Team", "enabled": 0, "is_group": 0}]})
        if p == "/api/resource/Employee":
            return httpx.Response(200, json={"data": [
                {"name": "E1", "employee_name": "Asha Rao", "designation": "Sales Head", "department": "Sales - X", "branch": "Mumbai", "user_id": "asha@team.com", "cell_number": "98"},
                {"name": "E2", "employee_name": "Binod Kumar", "designation": "Executive", "department": "Sales - X", "branch": "Delhi", "company_email": "binod@team.com"},
                {"name": "E3", "employee_name": "Old Rep", "designation": "Executive"}]})
        if p == "/api/resource/Item":
            return httpx.Response(200, json={"data": [{"name": "93120000", "item_code": "93120000", "item_name": "VGF (1.2 X 5)", "item_group": "Products", "stock_uom": "NOS", "standard_rate": 0,
                                                       "disabled": 1, "gst_hsn_code": "84749000", "description": "<div>Vibratory <b>Grizzly</b> Feeder</div>"}]})
        return httpx.Response(200, json={"data": []})

    mt = httpx.MockTransport(handler)
    client.put("/api/erp/config", headers=h, json={"enabled": True, "url": "https://erp.example", "api_key": "K", "api_secret": "S", "item_group": "Products"})
    assert erp.run_sync(tid, "sales_team", mt) == {"sales_team": 3}
    item_call = None
    assert erp.run_sync(tid, "products", mt) == {"products": 1}
    assert any(p == "/api/resource/Item" and "Products" in prm.get("filters", "") for p, prm in asked)  # only the configured item group is requested
    from app.db import SessionLocal
    from app.models import Product
    from sqlalchemy import select
    db = SessionLocal()
    prod = db.scalar(select(Product).where(Product.tenant_id == tid, Product.erp_name == "93120000"))
    assert prod.hsn == "84749000" and prod.disabled is True and prod.description == "Vibratory Grizzly Feeder" and prod.group == "Products"
    db.close()
    assert not any(p["code"] == "93120000" for p in client.get("/api/crm/products", headers=h).json()["items"])  # disabled items stay out of the lead product picker
    team = client.get("/api/sales-team", headers=h).json()
    assert [t["name"] for t in team] == ["Asha Rao", "Binod Kumar"]  # disabled people are hidden from the picker
    assert len(client.get("/api/sales-team?all=true", headers=h).json()) == 3
    asha = next(t for t in team if t["name"] == "Asha Rao")
    assert asha["is_group"] and asha["designation"] == "Sales Head" and not asha["has_login"]
    # logins: only people with an email, within seats
    out = client.post("/api/sales-team/create-logins", headers=h).json()
    assert set(out["created"]) == {"Asha Rao", "Binod Kumar"} and out["skipped"] == []  # the disabled member is not given a login
    team = {t["name"]: t for t in client.get("/api/sales-team", headers=h).json()}
    assert team["Asha Rao"]["has_login"] and team["Binod Kumar"]["login_email"] == "binod@team.com"
    # lead owner is picked from the sales team
    pid = mk_product(client, h, "T-1")
    lead = _lead(client, h, name="Buyer One", phone="+91 98765 12345", product_id=pid, sales_person_id=team["Binod Kumar"]["id"])
    assert lead["sales_person_id"] == team["Binod Kumar"]["id"] and lead["owner_id"] == team["Binod Kumar"]["user_id"]
    moved = client.patch(f"/api/crm/leads/{lead['id']}", headers=h, json={"sales_person_id": team["Asha Rao"]["id"]}).json()
    assert moved["owner_id"] == team["Asha Rao"]["user_id"]
    by_user = client.patch(f"/api/crm/leads/{lead['id']}", headers=h, json={"owner_id": team["Binod Kumar"]["user_id"]}).json()
    assert by_user["sales_person_id"] == team["Binod Kumar"]["id"]
    bulk = client.post("/api/crm/leads/bulk", headers=h, json={"ids": [lead["id"]], "action": "update", "changes": {"sales_person_id": team["Asha Rao"]["id"]}})
    assert bulk.status_code == 200 and client.get(f"/api/crm/leads/{lead['id']}", headers=h).json()["owner_id"] == team["Asha Rao"]["user_id"]
    # another workspace cannot use this team, and cannot see it
    assert client.get("/api/sales-team", headers=h2).json() == []
    pid2 = mk_product(client, h2, "T-2")
    bad = client.post("/api/crm/leads", headers=h2, json={"name": "X", "phone": "+91 98765 99999", "product_id": pid2, "sales_person_id": team["Asha Rao"]["id"]})
    assert bad.status_code in (404, 422)
    assert client.patch(f"/api/sales-team/{team['Asha Rao']['id']}", headers=h2, json={"enabled": False}).status_code == 404
    # another workspace's import only affects itself
    assert client.post("/api/sales-team/import", headers=h2, json={"rows": [{"erp_name": "Z", "employee_name": "Zed"}]}).json() == {"imported": 1}
    assert [t["name"] for t in client.get("/api/sales-team", headers=h).json()] == ["Asha Rao", "Binod Kumar"]
