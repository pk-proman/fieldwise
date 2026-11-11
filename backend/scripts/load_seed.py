"""Loads catalogue / sales-team files exported from ERPNext into a workspace (one-off; the live path is Settings -> ERPNext -> Sync now).
   PYTHONPATH=. python scripts/load_seed.py --tenant proman --products seed/pispl_products.json [--sales-team seed/pispl_sales_team.json] [--logo seed/proman-logo.png --logo-dark seed/proman-logo-darkmode.png]
   Safe to re-run: rows are matched on their ERPNext id and updated.  Files under backend/seed/ are git-ignored on purpose."""
import argparse
import json
from sqlalchemy import select
from app.db import SessionLocal
from app.models import Tenant, Product
from app.integrations.erpnext import upsert_sales_person
from app.migrate import ensure_schema

ensure_schema()  # creates the new table / columns on an older local database (production uses `alembic upgrade head`)

ap = argparse.ArgumentParser()
ap.add_argument("--tenant", help="workspace slug (omit to list the workspaces on this server)")
ap.add_argument("--products")
ap.add_argument("--sales-team")
ap.add_argument("--logo", help="company logo (PNG/JPG) shown before the company name")
ap.add_argument("--logo-dark", help="optional light-coloured logo for dark mode")
a = ap.parse_args()
db = SessionLocal()
if not a.tenant:
    print("Workspaces on this server:", ", ".join(x.slug for x in db.scalars(select(Tenant))) or "(none yet: sign up first)")
    raise SystemExit(0)
t = db.scalar(select(Tenant).where(Tenant.slug == a.tenant))
if not t:
    raise SystemExit(f"No workspace '{a.tenant}'.")
if a.products:
    new = upd = 0
    for r in json.load(open(a.products)):
        p = db.scalar(select(Product).where(Product.tenant_id == t.id, Product.erp_name == r["erp_name"]))
        if not p:
            p = Product(tenant_id=t.id, erp_name=r["erp_name"])
            db.add(p)
            new += 1
        else:
            upd += 1
        p.code, p.name, p.group, p.uom, p.hsn = r["code"], r["name"], r.get("group", ""), r.get("uom", "Nos"), r.get("hsn", "")
        p.price, p.description, p.disabled, p.deleted_at = r.get("price") or 0, r.get("description", ""), bool(r.get("disabled")), None
    print(f"products: {new} added, {upd} updated")
if a.sales_team:
    n = 0
    for r in json.load(open(a.sales_team)):
        upsert_sales_person(db, t, r)
        n += 1
    print(f"sales team: {n} people")
if a.logo or a.logo_dark:
    from sqlalchemy.orm.attributes import flag_modified
    from app.common import save_upload
    st = dict(t.settings or {})
    co = dict(st.get("company") or {})
    for path, key in ((a.logo, "logo_path"), (a.logo_dark, "logo_dark_path")):
        if path:
            co[key] = save_upload(t.id, open(path, "rb").read(), path.rsplit(".", 1)[-1])
    st["company"] = co
    t.settings = st
    flag_modified(t, "settings")
    print("logo set:", ", ".join(k for k in ("logo_path", "logo_dark_path") if co.get(k)))
db.commit()
