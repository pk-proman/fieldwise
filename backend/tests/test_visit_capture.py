from conftest import signup


def _product(client, h, code="P-1"):
    r = client.post("/api/crm/products", headers=h, json={"code": code, "name": "Screen " + code, "price": 100})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_new_customer_visit_becomes_a_lead(client):
    ws, h = signup(client, "VisitCo", "o@vc.com")
    pid = _product(client, h)
    assert client.post("/api/visits/start", headers=h, json={"lat": 19.1, "lng": 72.8}).status_code == 422          # a customer is needed
    assert client.post("/api/visits/start", headers=h, json={"customer": {"name": "Ravi Traders"}}).status_code == 422  # and a location
    r = client.post("/api/visits/start", headers=h, json={"customer": {"name": "Ravi Traders"}, "lat": 19.1, "lng": 72.8})
    assert r.status_code == 200, r.text
    v = r.json()["visit"]
    assert v["customer_id"] is None and v["prospect"]["name"] == "Ravi Traders"
    assert client.get("/api/visits/active", headers=h).json()["customer_name"] == "Ravi Traders"
    # checkout needs customer data and a summary
    e = client.post(f"/api/visits/{v['id']}/end", headers=h, json={"mom": ""})
    assert e.status_code == 422 and "mobile number" in e.json()["detail"] and "product" in e.json()["detail"] and "summary" in e.json()["detail"]
    # details can be added during the visit
    assert client.post(f"/api/visits/{v['id']}/customer", headers=h, json={"phone": "98 2222 3333", "city": "Pune"}).status_code == 200
    e = client.post(f"/api/visits/{v['id']}/end", headers=h, json={"mom": "Wants 2 screens", "customer": {"product_id": pid, "email": "ravi@example.com"}})
    assert e.status_code == 200, e.text
    out = e.json()
    assert out["lead_id"] and out["outcome"].startswith("New lead")
    leads = client.get("/api/crm/leads", headers=h).json()
    items = leads["items"] if isinstance(leads, dict) else leads
    lead = next(x for x in items if x["id"] == out["lead_id"])
    assert lead["source"] == "Field visit" and lead["city"] == "Pune" and lead["product_id"] == pid
    # a second visit to the same person updates the lead instead of duplicating it
    v2 = client.post("/api/visits/start", headers=h, json={"customer": {"name": "Ravi T", "phone": "9822223333"}, "lat": 19.1, "lng": 72.8}).json()["visit"]
    e2 = client.post(f"/api/visits/{v2['id']}/end", headers=h, json={"mom": "Second meeting, price agreed", "customer": {"product_id": pid, "company": "Ravi Traders Pvt"}}).json()
    assert e2["lead_id"] == out["lead_id"] and "existing lead" in e2["outcome"].lower()
    items2 = client.get("/api/crm/leads", headers=h).json()
    items2 = items2["items"] if isinstance(items2, dict) else items2
    assert len([x for x in items2 if x["phone"].endswith("2223333") or "9822223333" in x["phone"].replace(" ", "")]) == 1


def test_existing_customer_visit_updates_record(client):
    ws, h = signup(client, "VisitCo2", "o@vc2.com")
    cust = client.post("/api/crm/customers", headers=h, json={"name": "Site A", "lat": 19.1, "lng": 72.85}).json()
    v = client.post("/api/visits/start", headers=h, json={"customer_id": cust["id"], "lat": 19.5, "lng": 72.0}).json()   # far away: recorded, not blocked
    assert v["distance_m"] > 1000 and v["visit"]["start_dist_m"] > 1000
    vid = v["visit"]["id"]
    assert client.post(f"/api/visits/{vid}/end", headers=h, json={"mom": "Discussed supply"}).status_code == 422       # no phone on record yet
    e = client.post(f"/api/visits/{vid}/end", headers=h, json={"mom": "Discussed supply", "customer": {"phone": "9811100000", "email": "a@site.com", "city": "Thane"}})
    assert e.status_code == 200 and e.json()["outcome"].startswith("Updated customer")
    c = client.get(f"/api/crm/customers/{cust['id']}", headers=h).json()
    assert c["phone"] == "9811100000" and c["email"] == "a@site.com" and c["city"] == "Thane"
    mine = client.get("/api/visits", headers=h).json()
    assert mine[0]["customer_name"] == "Site A"
