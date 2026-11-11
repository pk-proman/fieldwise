from datetime import timedelta
import re
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from ..deps import Ctx, current, require, _auth
from ..db import SessionLocal
from ..models import (User, Branch, CustomField, SavedView, Notification, Lead, Customer, Contact, Task, ExpenseClaim, Product, utcnow)
from ..common import to_dict, get_or_404
from ..security import hash_password, check_password
from ..refs import own
from ..audit import audit
from .. import config

router = APIRouter(tags=["admin"])
VEHICLES = ("", "bike", "car", "public", "other")

# ------------------------------- Users & hierarchy -------------------------------
@router.get("/users")
def users(ctx: Ctx = Depends(current), all: bool = False):
    q = select(User).where(User.tenant_id == ctx.tid, User.deleted_at.is_(None)).order_by(User.name)
    if not (all and ctx.is_at_least("manager")):
        q = q.where(User.id.in_(ctx.team_ids()))
    return [to_dict(u) for u in ctx.db.scalars(q)]


class UserIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    email: str
    phone: str = ""
    password: str | None = Field(None, min_length=8, max_length=72)
    role: str = "employee"
    manager_id: int | None = None
    branch_id: int | None = None
    territory: str = ""
    employee_code: str = ""
    vehicle: str = ""
    active: bool = True


def _check_hierarchy(ctx: Ctx, uid: int | None, manager_id: int | None):
    if not manager_id:
        return
    m = ctx.db.get(User, manager_id)
    if not m or m.tenant_id != ctx.tid or m.deleted_at:
        raise HTTPException(422, "Reporting manager not found.")
    if not m.active:
        raise HTTPException(422, f"{m.name} is disabled, so cannot be a reporting manager.")
    cur = m
    for _ in range(50):  # reject cycles
        if uid and cur.id == uid:
            raise HTTPException(422, "That would make a reporting loop.")
        if not cur.manager_id:
            break
        cur = ctx.db.get(User, cur.manager_id)


@router.post("/users")
def create_user(body: UserIn, ctx: Ctx = Depends(require("admin"))):
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", body.email):
        raise HTTPException(422, "Enter a valid email address.")
    if body.role not in ("admin", "manager", "employee"):
        raise HTTPException(422, "Role must be admin, manager or employee.")
    if not body.password:
        raise HTTPException(422, "Set an initial password (min 8 characters).")
    problem = check_password(body.password, body.email)
    if problem:
        raise HTTPException(422, problem)
    seats = ctx.db.scalar(select(func.count(User.id)).where(User.tenant_id == ctx.tid, User.deleted_at.is_(None), User.active.is_(True)))
    if seats >= ctx.tenant.seat_limit:
        raise HTTPException(402, f"Your plan allows {ctx.tenant.seat_limit} active users. Upgrade to add more.")
    if ctx.db.scalar(select(User.id).where(User.tenant_id == ctx.tid, User.email == body.email.lower())):
        raise HTTPException(409, "A user with this email already exists.")
    _check_hierarchy(ctx, None, body.manager_id)
    if body.branch_id:
        own(ctx, Branch, body.branch_id, "Branch")
    u = User(tenant_id=ctx.tid, email=body.email.lower(), name=body.name, phone=body.phone, password_hash=hash_password(body.password),
             role=body.role, manager_id=body.manager_id, branch_id=body.branch_id, territory=body.territory, employee_code=body.employee_code, vehicle=body.vehicle if body.vehicle in VEHICLES else "", active=body.active)
    ctx.db.add(u)
    ctx.db.flush()
    audit(ctx, "user.create", "user", u.id, f"{u.email} as {u.role}")
    ctx.db.commit()
    return to_dict(u)


@router.patch("/users/{uid}")
def update_user(uid: int, body: dict, ctx: Ctx = Depends(require("admin"))):
    u = get_or_404(ctx.db, User, uid, ctx.tid)
    if u.role == "owner" and ctx.user.role != "owner":
        raise HTTPException(403, "Only the owner can edit the owner account.")
    if body.get("role") and (body["role"] not in ("admin", "manager", "employee") or u.role == "owner"):
        raise HTTPException(422, "That role change is not allowed.")
    if "manager_id" in body:
        _check_hierarchy(ctx, uid, body["manager_id"])
    from ..models import Department, Designation, Shift
    for k, M in (("branch_id", Branch), ("department_id", Department), ("designation_id", Designation), ("shift_id", Shift)):
        if body.get(k):
            own(ctx, M, body[k], k.replace("_id", "").title())
    changed = [k for k in ("role", "active", "manager_id") if k in body and getattr(u, k) != body[k]]
    for k in ("name", "phone", "role", "manager_id", "branch_id", "territory", "employee_code", "active", "department_id", "designation_id", "shift_id", "vehicle"):
        if k in body:
            setattr(u, k, body[k] if k != "vehicle" or body[k] in VEHICLES else "")
    if changed:
        audit(ctx, "user.update", "user", u.id, ", ".join(f"{k}={body[k]}" for k in changed))
    if body.get("password"):
        problem = check_password(body["password"], u.email)
        if problem:
            raise HTTPException(422, problem)
        audit(ctx, "password.admin_set", "user", u.id)
        u.password_hash = hash_password(body["password"])
        u.session_id = None
    if body.get("active") is False:
        u.session_id = None  # force sign-out
        # Their team must not be left without a manager: move direct reports up one level
        moved = list(ctx.db.scalars(select(User).where(User.tenant_id == ctx.tid, User.manager_id == u.id, User.deleted_at.is_(None))))
        for r in moved:
            r.manager_id = u.manager_id
        if moved:
            audit(ctx, "user.reassign_reports", "user", u.id, f"{len(moved)} reports moved to manager id {u.manager_id}")
    ctx.db.commit()
    return to_dict(u)


@router.post("/users/{uid}/reset-device")
def reset_device(uid: int, ctx: Ctx = Depends(require("admin"))):
    get_or_404(ctx.db, User, uid, ctx.tid).session_id = None
    ctx.db.commit()
    return {"ok": True}


# ------------------------------- Branches / territories -------------------------------
@router.get("/branches")
def branches(ctx: Ctx = Depends(current)):
    return [to_dict(b) for b in ctx.db.scalars(select(Branch).where(Branch.tenant_id == ctx.tid, Branch.deleted_at.is_(None)).order_by(Branch.name))]


@router.post("/branches")
def add_branch(body: dict, ctx: Ctx = Depends(require("admin"))):
    try:
        b = Branch(tenant_id=ctx.tid, name=str(body["name"]).strip(), address=body.get("address", ""), country=body.get("country", "India"),
                   lat=float(body["lat"]), lng=float(body["lng"]), radius_m=int(body.get("radius_m") or ctx.setting("geofence_default_m", 150)))
    except (KeyError, ValueError, TypeError):
        raise HTTPException(422, "Branch needs a name, latitude and longitude.")
    if not (-90 <= b.lat <= 90 and -180 <= b.lng <= 180) or not b.name or not (20 <= b.radius_m <= 5000):
        raise HTTPException(422, "Check the coordinates, and use a radius between 20 and 5000 m.")
    ctx.db.add(b)
    ctx.db.commit()
    return to_dict(b)


@router.patch("/branches/{bid}")
def edit_branch(bid: int, body: dict, ctx: Ctx = Depends(require("admin"))):
    b = get_or_404(ctx.db, Branch, bid, ctx.tid)
    for k in ("name", "address", "country", "lat", "lng", "radius_m"):
        if k in body:
            setattr(b, k, body[k])
    ctx.db.commit()
    return to_dict(b)


@router.delete("/branches/{bid}")
def del_branch(bid: int, ctx: Ctx = Depends(require("admin"))):
    get_or_404(ctx.db, Branch, bid, ctx.tid).deleted_at = utcnow()
    ctx.db.commit()
    return {"ok": True}


# ------------------------------- Settings -------------------------------
SETTING_KEYS = {"currency", "timezone", "work_start", "work_end", "late_grace_min", "weekly_off", "fuel_rate_per_km", "fuel_daily_cap",
                "geofence_default_m", "enforce_geofence", "checkout_geofence", "block_mock_location", "require_face", "require_visit_otp",
                "map_style", "labels", "task_statuses", "waiting_alert_min", "gps_off_alert_min", "mom_recipients_default", "page_headers",
                "team_label", "discount_approval_pct", "qualify_min_score", "auto_followup_days", "stage_probability", "dcr_digest", "session_hours", "mfa_mode", "auto_regret_days", "notify_arrivals", "require_visit_photo", "require_tracking_consent", "tracking_policy", "tracking_policy_version", "ping_retention_days"}


@router.get("/settings")
def get_settings(ctx: Ctx = Depends(current)):
    return {k: v for k, v in (ctx.tenant.settings or {}).items() if k not in ("erpnext", "secrets", "smtp", "gateways", "intake", "ai", "assignment")}


@router.put("/settings")
def put_settings(body: dict, ctx: Ctx = Depends(require("admin"))):
    s = dict(ctx.tenant.settings or {})
    for k, v in body.items():
        if k in SETTING_KEYS:
            s[k] = v
    if body.get("mfa_mode") not in (None, "off", "optional", "required"):
        raise HTTPException(422, "MFA must be off, optional or required.")
    if "task_statuses" in body and (not isinstance(body["task_statuses"], list) or "done" not in body["task_statuses"] or "open" not in body["task_statuses"]):
        raise HTTPException(422, "Task statuses must include 'open' and 'done'.")
    ctx.tenant.settings = s
    audit(ctx, "settings.update", "settings", None, ", ".join(sorted(k for k in body if k in SETTING_KEYS))[:400])
    ctx.db.commit()
    return {k: v for k, v in s.items() if k not in ("erpnext", "secrets", "smtp", "gateways", "intake", "ai", "assignment")}


# ------------------------------- Custom fields & views -------------------------------
@router.get("/custom-fields")
def cfields(entity: str | None = None, ctx: Ctx = Depends(current)):
    q = select(CustomField).where(CustomField.tenant_id == ctx.tid, CustomField.deleted_at.is_(None))
    if entity:
        q = q.where(CustomField.entity == entity)
    return [to_dict(f) for f in ctx.db.scalars(q.order_by(CustomField.id))]


@router.post("/custom-fields")
def add_cfield(body: dict, ctx: Ctx = Depends(require("admin"))):
    ent, label = body.get("entity"), str(body.get("label", "")).strip()
    typ = body.get("type", "text")
    if ent not in ("lead", "customer", "contact", "task") or not label:
        raise HTTPException(422, "Choose a module and give the field a label.")
    if typ not in ("text", "number", "date", "select", "transport", "route", "card"):
        raise HTTPException(422, "Unsupported field type.")
    key = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:50]
    if ctx.db.scalar(select(CustomField.id).where(CustomField.tenant_id == ctx.tid, CustomField.entity == ent, CustomField.key == key, CustomField.deleted_at.is_(None))):
        raise HTTPException(409, "A field with that name already exists.")
    opts = body.get("options") or ([ "Bike", "Car", "Bus", "Train", "Flight", "Taxi"] if typ == "transport" else [])
    f = CustomField(tenant_id=ctx.tid, entity=ent, key=key, label=label, type=typ if typ != "transport" else "select", options=opts, required=bool(body.get("required")))
    ctx.db.add(f)
    ctx.db.commit()
    return to_dict(f)


@router.patch("/custom-fields/{fid}")
def rename_cfield(fid: int, body: dict, ctx: Ctx = Depends(require("admin"))):
    f = get_or_404(ctx.db, CustomField, fid, ctx.tid)
    for k in ("label", "required", "options"):
        if k in body:
            setattr(f, k, body[k])  # the key never changes, so stored data survives a rename
    ctx.db.commit()
    return to_dict(f)


@router.delete("/custom-fields/{fid}")
def del_cfield(fid: int, ctx: Ctx = Depends(require("admin"))):
    get_or_404(ctx.db, CustomField, fid, ctx.tid).deleted_at = utcnow()
    ctx.db.commit()
    return {"ok": True}


@router.get("/views")
def views(entity: str, ctx: Ctx = Depends(current)):
    q = select(SavedView).where(SavedView.tenant_id == ctx.tid, SavedView.entity == entity, SavedView.deleted_at.is_(None),
                                (SavedView.user_id == ctx.user.id) | (SavedView.shared.is_(True)))
    return [to_dict(v) for v in ctx.db.scalars(q)]


@router.post("/views")
def save_view(body: dict, ctx: Ctx = Depends(current)):
    if not body.get("name") or not body.get("entity"):
        raise HTTPException(422, "Give the view a name.")
    v = SavedView(tenant_id=ctx.tid, user_id=ctx.user.id, entity=body["entity"], name=str(body["name"])[:100],
                  filters=body.get("filters") or {}, shared=bool(body.get("shared")) and ctx.is_at_least("manager"))
    ctx.db.add(v)
    ctx.db.commit()
    return to_dict(v)


@router.delete("/views/{vid}")
def del_view(vid: int, ctx: Ctx = Depends(current)):
    v = get_or_404(ctx.db, SavedView, vid, ctx.tid)
    if v.user_id != ctx.user.id and not ctx.is_at_least("admin"):
        raise HTTPException(403, "You can only delete your own views.")
    v.deleted_at = utcnow()
    ctx.db.commit()
    return {"ok": True}


# ------------------------------- Notifications -------------------------------
@router.get("/notifications")
def notifications(ctx: Ctx = Depends(current)):
    rows = ctx.db.scalars(select(Notification).where(Notification.tenant_id == ctx.tid, Notification.user_id == ctx.user.id)
                          .order_by(Notification.id.desc()).limit(100)).all()
    return {"unread": sum(1 for n in rows if not n.read), "items": [to_dict(n) for n in rows]}


@router.post("/notifications/read")
def mark_read(ctx: Ctx = Depends(current)):
    for n in ctx.db.scalars(select(Notification).where(Notification.tenant_id == ctx.tid, Notification.user_id == ctx.user.id, Notification.read.is_(False))):
        n.read = True
    ctx.db.commit()
    return {"ok": True}


# ------------------------------- Recycle bin (90 days) -------------------------------
BIN = {"lead": Lead, "customer": Customer, "contact": Contact, "task": Task, "expense": ExpenseClaim, "product": Product, "branch": Branch}


def _label(o):
    return getattr(o, "name", None) or getattr(o, "title", None) or getattr(o, "description", None) or f"#{o.id}"


@router.get("/recycle-bin")
def bin_list(ctx: Ctx = Depends(require("manager"))):
    cutoff = utcnow() - timedelta(days=config.RECYCLE_DAYS)
    out = []
    for kind, m in BIN.items():
        for o in ctx.db.scalars(select(m).where(m.tenant_id == ctx.tid, m.deleted_at.is_not(None), m.deleted_at >= cutoff)):
            out.append({"kind": kind, "id": o.id, "label": _label(o), "deleted_at": o.deleted_at.isoformat() + "Z",
                        "purges_on": (o.deleted_at + timedelta(days=config.RECYCLE_DAYS)).date().isoformat()})
    return sorted(out, key=lambda x: x["deleted_at"], reverse=True)


@router.post("/recycle-bin/{kind}/{rid}/restore")
def bin_restore(kind: str, rid: int, ctx: Ctx = Depends(require("manager"))):
    m = BIN.get(kind) or (_ for _ in ()).throw(HTTPException(404, "Unknown record type."))
    o = ctx.db.get(m, rid)
    if not o or o.tenant_id != ctx.tid or not o.deleted_at:
        raise HTTPException(404, "Record not found in the recycle bin.")
    o.deleted_at = None
    ctx.db.commit()
    return {"ok": True}


@router.delete("/recycle-bin/{kind}/{rid}")
def bin_purge(kind: str, rid: int, ctx: Ctx = Depends(require("admin"))):
    m = BIN.get(kind) or (_ for _ in ()).throw(HTTPException(404, "Unknown record type."))
    o = ctx.db.get(m, rid)
    if not o or o.tenant_id != ctx.tid or not o.deleted_at:
        raise HTTPException(404, "Record not found in the recycle bin.")
    audit(ctx, "recycle.purge", kind, rid)
    ctx.db.delete(o)
    ctx.db.commit()
    return {"ok": True}


# ------------------------------- Files (receipts, selfies, photos) -------------------------------
@router.get("/files/{tenant_id}/{name}")
def get_file(tenant_id: int, name: str, t: str = Query("")):
    if not re.fullmatch(r"[a-f0-9]{32}\.[a-z0-9]{1,5}", name):
        raise HTTPException(404, "Not found.")
    db = SessionLocal()
    try:
        ctx = _auth(db, t)
        if ctx.tid != tenant_id:
            raise HTTPException(404, "Not found.")
    finally:
        db.close()
    p = config.UPLOAD_DIR / str(tenant_id) / name
    if not p.exists():
        raise HTTPException(404, "Not found.")
    return FileResponse(p, headers={"Content-Security-Policy": "default-src 'none'; sandbox", "X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=300"})


@router.get("/security/login-attempts")
def login_attempts(failed: bool = False, ctx: Ctx = Depends(require("admin"))):
    from ..models import LoginAttempt
    q = select(LoginAttempt).where(LoginAttempt.tenant_id == ctx.tid)
    if failed:
        q = q.where(LoginAttempt.ok.is_(False))
    return [to_dict(a) for a in ctx.db.scalars(q.order_by(LoginAttempt.id.desc()).limit(200))]


@router.post("/users/{uid}/reset-mfa")
def reset_mfa(uid: int, ctx: Ctx = Depends(require("admin"))):
    u = get_or_404(ctx.db, User, uid, ctx.tid)
    u.mfa_enabled, u.mfa_secret = False, None
    audit(ctx, "user.mfa_reset", "user", uid)
    ctx.db.commit()
    return {"ok": True}
