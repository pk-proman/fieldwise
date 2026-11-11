"""The mandatory first-run setup wizard for a client's workspace.
Until the required steps are done and POST /setup/complete succeeds, only the client's administrators can use the workspace (see deps._auth)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.orm.attributes import flag_modified
from ..deps import Ctx, require
from ..models import Tenant, Branch, User, Product, Master, utcnow
from ..audit import audit
from .. import modules as M

router = APIRouter(tags=["setup"])

# key, title, help, required
STEPS = [
    ("company", "Company profile", "Legal name, address, tax IDs, country, currency, time zone and logo. They appear on quotations, invoices and emails.", True),
    ("modules", "Modules", "Switch on only what this company will use. You can change this later.", True),
    ("work_rules", "Work rules", "Working hours, weekly off, attendance checks (location, face, mock-GPS) and the tracking notice.", True),
    ("branches", "Offices and branches", "At least one office with its map location, used for location-verified attendance.", True),
    ("team", "People", "Invite managers and field staff. You can also do this later.", False),
    ("catalog", "Products and sales masters", "Product catalogue, lead sources and regions. Import a CSV or sync from your ERP.", False),
    ("integrations", "Integrations", "ERPNext, WhatsApp/SMS gateway, email sending. Optional.", False),
]


def _flag(t: Tenant, key: str) -> bool:
    return bool((t.setup_state or {}).get(key))


def status_of(db, t: Tenant) -> dict:
    mods = (t.settings or {}).get("modules") or M.defaults()
    branches = db.scalar(select(func.count(Branch.id)).where(Branch.tenant_id == t.id, Branch.deleted_at.is_(None))) or 0
    users = db.scalar(select(func.count(User.id)).where(User.tenant_id == t.id, User.deleted_at.is_(None), User.active.is_(True))) or 0
    products = db.scalar(select(func.count(Product.id)).where(Product.tenant_id == t.id, Product.deleted_at.is_(None))) or 0
    sources = db.scalar(select(func.count(Master.id)).where(Master.tenant_id == t.id, Master.kind == "lead_source", Master.deleted_at.is_(None))) or 0
    steps = []
    for key, title, help_, required in STEPS:
        if key == "branches":
            needed = mods.get("field_force", True) is not False
            done = branches > 0 or not needed
            detail = f"{branches} office(s)" + ("" if needed else " (not needed: field force is off)")
        elif key == "team":
            done, detail = _flag(t, key) or users > 1, f"{users} active user(s)"
        elif key == "catalog":
            done, detail = _flag(t, key) or products > 0, f"{products} product(s), {sources} lead source(s)"
        else:
            done, detail = _flag(t, key), ""
        steps.append({"key": key, "title": title, "help": help_, "required": required, "done": bool(done), "detail": detail})
    req = [s for s in steps if s["required"]]
    return {"complete": t.setup_completed_at is not None, "completed_at": t.setup_completed_at.isoformat() + "Z" if t.setup_completed_at else None,
            "steps": steps, "ready": all(s["done"] for s in req), "progress": round(100 * sum(s["done"] for s in steps) / len(steps)),
            "modules": {**M.defaults(), **mods}, "module_catalog": [{"key": k, "title": n, "about": d} for k, n, d in M.MODULES]}


@router.get("/setup")
def get_setup(ctx: Ctx = Depends(require("admin"))):
    return status_of(ctx.db, ctx.tenant)


@router.post("/setup/step/{key}")
def mark_step(key: str, body: dict | None = None, ctx: Ctx = Depends(require("admin"))):
    if key not in {s[0] for s in STEPS}:
        raise HTTPException(404, "Unknown setup step.")
    st = dict(ctx.tenant.setup_state or {})
    st[key] = bool((body or {}).get("done", True))
    ctx.tenant.setup_state = st
    flag_modified(ctx.tenant, "setup_state")
    ctx.db.commit()
    return status_of(ctx.db, ctx.tenant)


@router.put("/setup/modules")
def set_modules(body: dict, ctx: Ctx = Depends(require("admin"))):
    mods = body.get("modules")
    if not isinstance(mods, dict) or set(mods) - set(M.KEYS):
        raise HTTPException(422, "Unknown module.")
    merged = {**M.defaults(), **{k: bool(v) for k, v in mods.items()}}
    if not (merged["field_force"] or merged["crm"]):
        raise HTTPException(422, "Switch on at least Field force or CRM.")
    s = dict(ctx.tenant.settings or {})
    s["modules"] = merged
    ctx.tenant.settings = s
    flag_modified(ctx.tenant, "settings")
    st = dict(ctx.tenant.setup_state or {})
    st["modules"] = True
    ctx.tenant.setup_state = st
    flag_modified(ctx.tenant, "setup_state")
    audit(ctx, "setup.modules", "settings", None, ", ".join(k for k, v in merged.items() if v))
    ctx.db.commit()
    return status_of(ctx.db, ctx.tenant)


@router.post("/setup/complete")
def complete(ctx: Ctx = Depends(require("admin"))):
    st = status_of(ctx.db, ctx.tenant)
    missing = [s["title"] for s in st["steps"] if s["required"] and not s["done"]]
    if missing:
        raise HTTPException(422, "Still to do: " + ", ".join(missing) + ".")
    if ctx.tenant.setup_completed_at is None:
        ctx.tenant.setup_completed_at = utcnow()
        audit(ctx, "setup.complete", "tenant", ctx.tenant.id, "")
    ctx.db.commit()
    return status_of(ctx.db, ctx.tenant)
