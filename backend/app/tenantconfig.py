"""Export / import a client's CONFIGURATION (not its transactions): a reusable template to onboard the next client of the same kind,
or to move a configuration between environments. Secrets (ERP keys, SMTP, gateways) and anything that points at individual people are never included."""
from datetime import date
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from .models import Tenant, Branch, Department, Designation, LeaveType, Holiday, Master, CustomField, MessageTemplate, Shift, FormTemplate, Product

EXCLUDED_SETTINGS = {"erpnext", "secrets", "smtp", "gateways", "intake", "ai", "assignment", "numbering", "company", "ai_qualifier"}
VERSION = 1

# (key in file, model, fields copied, natural key used to avoid duplicates on import)
TABLES = [
    ("branches", Branch, ["name", "address", "country", "lat", "lng", "radius_m"], ("name",)),
    ("departments", Department, ["name"], ("name",)),          # parent link is rebuilt by name below
    ("designations", Designation, ["name"], ("name",)),
    ("leave_types", LeaveType, ["name", "days_per_year", "paid"], ("name",)),
    ("holidays", Holiday, ["day", "name", "country"], ("day", "name")),
    ("masters", Master, ["kind", "name", "sort"], ("kind", "name")),
    ("custom_fields", CustomField, ["entity", "key", "label", "type", "options", "required"], ("entity", "key")),
    ("message_templates", MessageTemplate, ["key", "name", "channel", "subject", "body"], ("key",)),
    ("shifts", Shift, ["name", "start", "end", "grace_min", "break_min", "weekly_off"], ("name",)),
    ("form_templates", FormTemplate, ["name", "applies_to", "fields", "mandatory", "active"], ("name",)),
]
PRODUCT_FIELDS = ["code", "name", "group", "uom", "price", "erp_name", "category", "hsn", "gst_pct", "description", "moq", "amc_charge", "disabled", "kind"]


def _val(v):
    return v.isoformat() if isinstance(v, date) else v


def export_config(db: Session, t: Tenant, include_catalog: bool = False) -> dict:
    out = {"version": VERSION, "settings": {k: v for k, v in (t.settings or {}).items() if k not in EXCLUDED_SETTINGS}}
    for key, model, fields, _nk in TABLES:
        rows = db.scalars(select(model).where(model.tenant_id == t.id, model.deleted_at.is_(None))).all()
        out[key] = [{f: _val(getattr(r, f)) for f in fields} for r in rows]
    deps = {d.id: d for d in db.scalars(select(Department).where(Department.tenant_id == t.id, Department.deleted_at.is_(None)))}
    out["departments"] = [{"name": d.name, "parent": deps[d.parent_id].name if d.parent_id in deps else None} for d in deps.values()]
    if include_catalog:
        out["products"] = [{f: _val(getattr(p, f)) for f in PRODUCT_FIELDS} for p in db.scalars(select(Product).where(Product.tenant_id == t.id, Product.deleted_at.is_(None)))]
    return out


def import_config(db: Session, t: Tenant, cfg: dict, overwrite_settings: bool = False) -> dict:
    """Adds what is missing; never overwrites or deletes existing rows. Settings are only replaced when overwrite_settings is set
    (used when a brand-new client is created from a template). Returns counts of rows created per table."""
    if not isinstance(cfg, dict) or cfg.get("version") != VERSION:
        raise ValueError("This is not a Fieldwise configuration file (or it is from a newer version).")
    counts = {}
    s = dict(t.settings or {})
    for k, v in (cfg.get("settings") or {}).items():
        if k not in EXCLUDED_SETTINGS and (overwrite_settings or k not in s):
            s[k] = v
    t.settings = s
    flag_modified(t, "settings")
    for key, model, fields, nk in TABLES:
        existing = {tuple(str(getattr(r, k)) for k in nk) for r in db.scalars(select(model).where(model.tenant_id == t.id, model.deleted_at.is_(None)))}
        made = 0
        for row in cfg.get(key) or []:
            data = {f: row.get(f) for f in fields if f in row}
            if key == "holidays" and isinstance(data.get("day"), str):
                data["day"] = date.fromisoformat(data["day"])
            sig = tuple(str(data.get(k)) for k in nk)
            if sig in existing or any(data.get(k) is None for k in nk):
                continue
            db.add(model(tenant_id=t.id, **data))
            existing.add(sig)
            made += 1
        counts[key] = made
    db.flush()
    by_name = {d.name: d for d in db.scalars(select(Department).where(Department.tenant_id == t.id, Department.deleted_at.is_(None)))}
    for row in cfg.get("departments") or []:   # parent links by name
        d, p = by_name.get(row.get("name")), by_name.get(row.get("parent") or "")
        if d and p and d.parent_id is None and d.id != p.id:
            d.parent_id = p.id
    pcount = 0
    have = {p.code for p in db.scalars(select(Product).where(Product.tenant_id == t.id, Product.deleted_at.is_(None)))}
    for row in cfg.get("products") or []:
        if row.get("code") and row["code"] not in have:
            db.add(Product(tenant_id=t.id, **{f: row.get(f) for f in PRODUCT_FIELDS if f in row}))
            have.add(row["code"])
            pcount += 1
    if "products" in cfg:
        counts["products"] = pcount
    return counts
