"""Synthetic load test: builds a realistic mid-size workspace (40 field staff, 8k leads, 150k GPS points...) and times the heavy endpoints.
Usage:  DATABASE_URL=postgresql+psycopg://... python scripts/loadtest.py"""
import os, random, time, tempfile
os.environ.setdefault("UPLOAD_DIR", tempfile.mkdtemp()); os.environ["ENABLE_SCHEDULER"] = "0"; os.environ["RATELIMIT_DISABLE"] = "1"; os.environ["NETGUARD_DISABLE"] = "1"
from datetime import datetime, timedelta, date
from fastapi.testclient import TestClient
from sqlalchemy import insert
from app.main import app
from app.db import SessionLocal, Base, engine
from app.models import *  # noqa
from app.routers.auth import bootstrap_tenant
from app.security import hash_password

random.seed(7)
N_USERS, N_LEADS, N_CUST, DAYS = 40, 8000, 3000, 30
with TestClient(app) as c:
    db = SessionLocal()
    t0 = time.time()
    slug = f"load-{int(time.time())}"
    t, owner = bootstrap_tenant(db, "Load Test Co " + slug, "Owner", f"o@{slug}.com", "Passw0rd!x")
    t.settings = {**t.settings, "require_tracking_consent": False}; t.seat_limit = 500
    br = Branch(tenant_id=t.id, name="HQ", lat=19.11, lng=72.87, radius_m=300); db.add(br); db.flush()
    pw = hash_password("Passw0rd!x")
    mgrs = []
    for i in range(4):
        u = User(tenant_id=t.id, email=f"m{i}@{slug}.com", name=f"Manager {i}", role="manager", password_hash=pw, manager_id=owner.id, branch_id=br.id); db.add(u); mgrs.append(u)
    db.flush()
    reps = []
    for i in range(N_USERS):
        u = User(tenant_id=t.id, email=f"r{i}@{slug}.com", name=f"Rep {i}", role="employee", password_hash=pw, manager_id=mgrs[i % 4].id, branch_id=br.id); db.add(u); reps.append(u)
    prods = [Product(tenant_id=t.id, code=f"P{i}", name=f"Product {i}", price=1000 * (i + 1)) for i in range(40)]; db.add_all(prods); db.flush()
    db.commit()
    now = datetime.utcnow(); users = reps + mgrs
    custs = [dict(tenant_id=t.id, name=f"Customer {i}", phone=f"+9198{i:08d}", phone_key=f"98{i:08d}", lat=19.0 + random.random() * .3, lng=72.8 + random.random() * .3, owner_id=random.choice(reps).id, city="Mumbai", created_at=now) for i in range(N_CUST)]
    db.execute(insert(Customer), custs)
    leads = [dict(tenant_id=t.id, name=f"Lead {i}", company=f"Company {i % 900}", phone=f"+9197{i:08d}", phone_key=f"97{i:08d}", email=f"l{i}@x.com", status=random.choice(["new", "contacted", "qualified", "proposal", "negotiation", "won", "lost"]), value=random.choice([1e5, 3e5, 1e6]), owner_id=random.choice(reps).id,
                  product_id=random.choice(prods).id, source="Website", temperature=random.choice(["hot", "warm", "cold"]), created_at=now - timedelta(days=random.randint(0, 120)), stage_changed_at=now - timedelta(days=random.randint(0, 60)),
                  country="India", city="Mumbai", notes="", tags=[], custom={}, shared_with=[], observers=[], products=[], qualification={}, repeat_count=1, in_pool=False, starred=False, region="", lost_reason="", erp_name="", source_ref="") for i in range(N_LEADS)]
    db.execute(insert(Lead), leads)
    db.commit()
    from sqlalchemy import select as _sel
    lead_ids = [i for (i,) in db.execute(_sel(Lead.id).where(Lead.tenant_id == t.id))]
    fus = [dict(tenant_id=t.id, lead_id=random.choice(lead_ids), owner_id=random.choice(reps).id, kind="call", due_at=now + timedelta(hours=random.randint(-200, 200)), notes="follow up", status=random.choice(["pending", "pending", "done"]), outcome="", outcome_notes="", repeat="", reminded=False, created_at=now) for _ in range(12000)]
    db.execute(insert(Followup), fus)
    acts = [dict(tenant_id=t.id, entity="lead", entity_id=random.choice(lead_ids), user_id=random.choice(reps).id, text="note", created_at=now - timedelta(days=random.randint(0, 60))) for _ in range(30000)]
    db.execute(insert(Activity), acts)
    att, pings = [], []
    for u in reps:
        for d in range(DAYS):
            day = (now - timedelta(days=d)).date()
            cin = datetime.combine(day, datetime.min.time()) + timedelta(hours=4, minutes=random.randint(0, 90))
            cout = cin + timedelta(hours=random.randint(7, 10))
            att.append(dict(tenant_id=t.id, user_id=u.id, day=day, check_in_at=cin, check_out_at=cout, check_in_lat=19.11, check_in_lng=72.87, total_distance_km=random.random() * 60, status="present", flagged="", check_in_method="gps", erp_synced=False, created_at=cin))
            lat, lng = 19.11, 72.87
            for k in range(0, int((cout - cin).total_seconds() / 60), 4):  # a point every 4 minutes
                lat += random.uniform(-.002, .002); lng += random.uniform(-.002, .002)
                pings.append(dict(tenant_id=t.id, user_id=u.id, ts=cin + timedelta(minutes=k), lat=lat, lng=lng, accuracy=10.0, battery=random.randint(20, 100), network="online", gps_on=True, mock=False, created_at=cin))
    db.execute(insert(Attendance), att)
    for i in range(0, len(pings), 20000):
        db.execute(insert(LocationPing), pings[i:i + 20000])
    qs = [dict(tenant_id=t.id, number=f"Q{i}", lead_id=random.choice(lead_ids), created_by=random.choice(reps).id, items=[], subtotal=1e5, total=1.18e5, status="sent", charges=[], terms="", erp_name="", erp_status="", erp_error="", created_at=now) for i in range(2500)]
    db.execute(insert(Quotation), qs)
    db.commit()
    print(f"seeded in {time.time() - t0:.0f}s: {N_USERS + 4} users, {N_LEADS} leads, {N_CUST} customers, {len(pings)} GPS points, {len(att)} attendance rows")
    tok = c.post("/api/auth/login", json={"workspace": t.slug, "email": f"m0@{slug}.com", "password": "Passw0rd!x"}).json()["token"]
    ot = c.post("/api/auth/login", json={"workspace": t.slug, "email": f"o@{slug}.com", "password": "Passw0rd!x"}).json()["token"]
    H, OH = {"Authorization": f"Bearer {tok}"}, {"Authorization": f"Bearer {ot}"}
    uid = reps[0].id
    today = date.today()
    tests = [("leads list (500)", "GET", "/api/crm/leads?limit=500", H), ("leads search", "GET", "/api/crm/leads?q=Company%2012", H), ("global search", "GET", "/api/search?q=Lead%2077", H), ("dashboard", "GET", "/api/reports/dashboard", OH),
             ("overview (6 mo)", "GET", "/api/reports/overview", OH), ("forecast", "GET", "/api/reports/forecast", OH), ("funnel", "GET", "/api/reports/funnel", OH), ("followups", "GET", "/api/followups?scope=team&bucket=open", H), ("live team", "GET", "/api/tracking/live", OH),
             ("live routes", "GET", "/api/tracking/live-routes", OH), ("attendance list", "GET", "/api/attendance", OH), ("day summary", "GET", f"/api/field/day-summary?user_id={uid}&day={today - timedelta(days=1)}", H),
             ("productivity 7d (team)", "GET", f"/api/field/productivity?frm={today - timedelta(days=6)}&to={today}", H), ("productivity 31d (all)", "GET", f"/api/field/productivity?frm={today - timedelta(days=30)}&to={today}", OH),
             ("customers list", "GET", "/api/crm/customers?limit=500", OH), ("DCR", "GET", "/api/reports/dcr", OH), ("targets", "GET", "/api/reports/targets", OH), ("outstanding", "GET", "/api/collections/outstanding", OH), ("anomalies 3d", "GET", "/api/field/anomalies", OH)]
    print(f"{'endpoint':28} {'ms':>7}  size")
    slow = []
    for name, m, path, hh in tests:
        t1 = time.time(); r = c.request(m, path, headers=hh); ms = (time.time() - t1) * 1000
        print(f"{name:28} {ms:7.0f}  {len(r.content) // 1024}KB  [{r.status_code}]")
        if ms > 1500 or r.status_code >= 400: slow.append((name, round(ms), r.status_code))
    print("SLOW/ERRORS:", slow or "none")
