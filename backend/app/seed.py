"""Demo data: a Proman-style workspace with a Mumbai HQ, a Dubai branch, a team, customers and leads.
Run:  python -m app.seed      (login: workspace=proman  admin@proman.demo / Demo@12345)"""
import random
from datetime import timedelta, datetime
from sqlalchemy import select
from .db import Base, engine, SessionLocal
from .models import *  # noqa
from .routers.auth import bootstrap_tenant
from .security import hash_password

PW = "Demo@12345"


def run():
    Base.metadata.create_all(engine)
    db = SessionLocal()
    if db.scalar(select(Tenant.id).where(Tenant.slug == "proman")):
        print("Demo workspace already exists.")
        return
    t, owner = bootstrap_tenant(db, "Proman", "Prashant Kumar", "admin@proman.demo", PW, "India", plan="growth")
    t.slug, t.seat_limit = "proman", 25
    mum = Branch(tenant_id=t.id, name="Mumbai HQ", address="Andheri East, Mumbai", lat=19.1136, lng=72.8697, radius_m=200)
    dxb = Branch(tenant_id=t.id, name="Dubai Office", address="Business Bay, Dubai", country="UAE", lat=25.1850, lng=55.2650, radius_m=250)
    db.add_all([mum, dxb])
    db.flush()
    mgr = User(tenant_id=t.id, email="manager@proman.demo", name="Anita Rao", role="manager", password_hash=hash_password(PW), branch_id=mum.id, territory="West India", manager_id=owner.id)
    db.add(mgr)
    db.flush()
    reps = []
    for n, e in (("Rahul Mehta", "rahul"), ("Sneha Iyer", "sneha"), ("Imran Khan", "imran")):
        u = User(tenant_id=t.id, email=f"{e}@proman.demo", name=n, role="employee", password_hash=hash_password(PW), branch_id=mum.id, manager_id=mgr.id,
                 territory="Mumbai", phone="+9198200" + str(random.randint(10000, 99999)))
        db.add(u)
        reps.append(u)
    db.flush()
    custs = [("Tata Projects Ltd", "Construction", 19.0760, 72.8777, "Mumbai"), ("Larsen Infra Pvt Ltd", "Infrastructure", 19.1197, 72.8464, "Mumbai"),
             ("Godrej Properties", "Real Estate", 19.0330, 72.8570, "Mumbai"), ("Shapoorji Pallonji", "Construction", 18.9400, 72.8350, "Mumbai"),
             ("Emaar Contracting", "Real Estate", 25.1972, 55.2744, "Dubai")]
    cs = []
    for i, (n, ind, la, lo, city) in enumerate(custs):
        c = Customer(tenant_id=t.id, name=n, industry=ind, lat=la, lng=lo, city=city, country="UAE" if city == "Dubai" else "India",
                     phone=f"+91982000{1000 + i}", email=f"purchase@{n.split()[0].lower()}.example", owner_id=reps[i % 3].id, address=f"{city} site office")
        db.add(c)
        cs.append(c)
    db.flush()
    for c in cs:
        db.add(Contact(tenant_id=t.id, name="Purchase Head", designation="Procurement Manager", phone=c.phone, email=c.email, customer_id=c.id, owner_id=c.owner_id))
    prods = []
    for code, name, grp, price in (("PRM-001", "Industrial Fastener Set M12", "Fasteners", 450), ("PRM-014", "Hydraulic Coupling 2in", "Hydraulics", 3200), ("PRM-022", "Safety Valve 150lb", "Valves", 8800), ("PRM-031", "Gasket Kit Spiral Wound", "Gaskets", 1250)):
        p = Product(tenant_id=t.id, code=code, name=name, group=grp, price=price)
        db.add(p)
        prods.append(p)
    db.flush()
    stages = ["new", "contacted", "qualified", "proposal", "negotiation", "lost", "qualified", "proposal", "new", "contacted", "negotiation", "qualified", "new", "proposal"]
    leads = []
    now0 = datetime.utcnow()
    for i, st in enumerate(stages):
        pr = prods[i % len(prods)]
        l = Lead(tenant_id=t.id, name=f"{random.choice(['Mehta', 'Shah', 'Nair', 'Verma', 'Khan'])} {i + 1}", company=random.choice(["Apex Builders", "Skyline Infra", "Metro Fab", "Gulf Steel"]), status=st,
                 value=random.choice([150000, 320000, 780000, 1250000]), owner_id=reps[i % 3].id, source=random.choice(["Referral", "Website", "Exhibition", "Cold call"]),
                 city=random.choice(["Mumbai", "Pune", "Dubai"]), phone=f"+91981100{2000 + i}", email=f"lead{i + 1}@example.com", product_id=pr.id,
                 expected_close=(now0 + timedelta(days=random.choice([10, 25, 40, 70]))).date(), stage_changed_at=now0 - timedelta(days=random.choice([1, 3, 9, 20])))
        if st not in ("new", "contacted"):
            l.qualified_at = now0 - timedelta(days=5)
            l.qualification = {"budget": 2, "authority": 1, "need": 2, "timeline": 1, "total": 6, "notes": "", "by": "seed"}
        if st == "lost":
            l.lost_reason = "Price too high"
        db.add(l)
        leads.append(l)
    db.flush()
    for i, l in enumerate(leads):
        if l.status in ("proposal", "negotiation"):
            pr = next(p for p in prods if p.id == l.product_id)
            qty = random.choice([10, 50, 100])
            amt = qty * pr.price
            db.add(Quotation(tenant_id=t.id, number=f"QTN-{i + 1:04d}", lead_id=l.id, created_by=l.owner_id, items=[{"product_id": pr.id, "code": pr.code, "name": pr.name, "uom": pr.uom, "qty": qty, "rate": pr.price, "discount_pct": 0, "amount": amt}],
                             subtotal=amt, total=round(amt * 1.18, 2), status="sent", valid_till=(now0 + timedelta(days=10)).date()))
        if l.status not in ("lost",):
            due = now0 + timedelta(hours=random.choice([-30, -3, 2, 6, 30, 80]))
            db.add(Followup(tenant_id=t.id, lead_id=l.id, owner_id=l.owner_id, kind=random.choice(["call", "visit", "whatsapp"]), due_at=due, notes="Check on requirement and next steps"))
    for i, c in enumerate(cs[:3]):
        db.add(Meeting(tenant_id=t.id, title=f"Requirement review - {c.name}", owner_id=reps[i].id, customer_id=c.id, scheduled_at=now0 + timedelta(hours=4 + i * 20), duration_min=45, agenda="Review requirement and pricing"))
    mth = now0.strftime("%Y-%m")
    for u in reps:
        db.add(Target(tenant_id=t.id, user_id=u.id, month=mth, revenue=1500000, visits=25, new_leads=12))
    for dday, nm in (((datetime.utcnow().date() + timedelta(days=30)), "Company Foundation Day"),):
        db.add(Holiday(tenant_id=t.id, day=dday, name=nm))
    now = datetime.utcnow()
    for i, c in enumerate(cs[:3]):
        db.add(Task(tenant_id=t.id, title=f"Site visit - {c.name}", assignee_id=reps[i].id, created_by=mgr.id, customer_id=c.id, priority="high",
                    due_at=now + timedelta(hours=3 + i), lat=c.lat, lng=c.lng, geofence_m=200))
    db.add(CustomField(tenant_id=t.id, entity="lead", key="project_size_sqft", label="Project size (sq ft)", type="number"))
    db.add(CustomField(tenant_id=t.id, entity="customer", key="transport", label="Transport type", type="select", options=["Bike", "Car", "Bus", "Train"]))
    for i, c in enumerate(cs):
        c.credit_limit = 500000 if i % 2 == 0 else 0
        for j, (age, amt) in enumerate(((75, 120000), (35, 85000), (5, 60000))[: 3 - (i % 2)]):
            d = now.date() - timedelta(days=age)
            db.add(Invoice(tenant_id=t.id, number=f"INV-{2000 + i * 10 + j}", customer_id=c.id, invoice_date=d, due_date=d + timedelta(days=30), total=amt, outstanding=amt))
    for k, u in enumerate(reps):
        mine = [c.id for c in cs if c.owner_id == u.id and c.country == "India"] or [cs[k].id]
        db.add(Beat(tenant_id=t.id, name=f"{u.name.split()[0]} - Mumbai beat", user_id=u.id, days=[now.weekday(), (now.weekday() + 3) % 7], start_date=now.date(), customer_ids=mine, created_by=mgr.id, territory="Mumbai"))
    db.commit()
    print(f"Seeded. Workspace 'proman' - logins (password {PW}): admin@proman.demo, manager@proman.demo, rahul@/sneha@/imran@proman.demo")


if __name__ == "__main__":
    run()
