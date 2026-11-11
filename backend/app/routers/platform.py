"""The vendor's console: what a super-admin does across client workspaces. Super-admins live in the reserved platform workspace and never
see a client's business data directly; to configure a client they act as that client's administrator ("open as admin", audited)."""
import re
import secrets
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.orm.attributes import flag_modified
from ..db import get_db
from ..deps import Ctx, require_platform
from ..models import Tenant, User, AuditLog, utcnow
from ..security import hash_password, make_reset_token, make_token, new_session_id, check_password
from ..common import to_dict
from .. import config, svc, tenantconfig, modules as M
from .setup import status_of
from .auth import bootstrap_tenant, _slug

router = APIRouter(prefix="/platform", tags=["platform"])
PLANS = ("trial", "starter", "growth", "enterprise")
INVITE_MINUTES = 7 * 24 * 60


def _log(ctx, tenant_id, action, detail=""):
    """Platform actions are recorded in the CLIENT's audit log too, so a client can see when the vendor acted in their workspace."""
    ctx.db.add(AuditLog(tenant_id=tenant_id, user_id=ctx.user.id, action=("platform." + action)[:60], entity="tenant", entity_id=tenant_id, detail=detail[:500]))


def _invite(t: Tenant, u: User) -> str:
    base = (config.PUBLIC_URL or "").rstrip("/")
    return f"{base}/#/reset?token={make_reset_token(u.id, t.id, u.password_hash, INVITE_MINUTES)}&workspace={t.slug}"


def _admin_of(db, t: Tenant):
    return db.scalar(select(User).where(User.tenant_id == t.id, User.role == "owner", User.deleted_at.is_(None)).order_by(User.id))


def _summary(db, t: Tenant, stats):
    n, last = stats.get(t.id, (0, None))
    adm = _admin_of(db, t)
    s = status_of(db, t)
    return {"id": t.id, "name": t.name, "slug": t.slug, "plan": t.plan, "seat_limit": t.seat_limit, "active": t.active, "users": n,
            "last_seen": last.isoformat() + "Z" if last else None, "created_at": t.created_at.isoformat() + "Z",
            "setup_complete": t.setup_completed_at is not None, "setup_progress": s["progress"],
            "admin": {"id": adm.id, "name": adm.name, "email": adm.email} if adm else None, "contact_email": t.contact_email, "notes": t.notes}


@router.get("/tenants")
def tenants(ctx: Ctx = Depends(require_platform)):
    rows = ctx.db.execute(select(User.tenant_id, func.count(User.id), func.max(User.last_seen)).where(User.deleted_at.is_(None), User.active.is_(True)).group_by(User.tenant_id)).all()
    stats = {r[0]: (r[1], r[2]) for r in rows}
    ts = ctx.db.scalars(select(Tenant).where(Tenant.kind == "client").order_by(Tenant.id)).all()
    return [_summary(ctx.db, t, stats) for t in ts]


class NewTenant(BaseModel):
    company: str = Field(min_length=2, max_length=200)
    admin_name: str = Field(min_length=2, max_length=200)
    admin_email: str
    country: str = "India"
    plan: str = "trial"
    seat_limit: int = Field(10, ge=1, le=100000)
    slug: str = ""
    notes: str = ""
    config: dict | None = None        # a configuration exported from another client, used as the starting template


@router.post("/tenants")
def create_tenant(body: NewTenant, ctx: Ctx = Depends(require_platform)):
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", body.admin_email):
        raise HTTPException(422, "Enter a valid email address for the client's administrator.")
    if body.plan not in PLANS:
        raise HTTPException(422, "Plan must be one of: " + ", ".join(PLANS))
    slug = body.slug.strip().lower()
    if slug and (not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,38}", slug) or ctx.db.scalar(select(Tenant.id).where(Tenant.slug == slug))):
        raise HTTPException(409, "That workspace code is invalid or already taken (use 2 to 39 letters, digits or hyphens).")
    t, owner = bootstrap_tenant(ctx.db, body.company, body.admin_name, body.admin_email, secrets.token_urlsafe(24), body.country, plan=body.plan)
    if slug:
        t.slug = slug
    t.seat_limit, t.notes, t.contact_email = body.seat_limit, body.notes[:1000], body.admin_email.lower()
    t.setup_completed_at = None
    t.setup_state = {}
    try:
        counts = tenantconfig.import_config(ctx.db, t, body.config, overwrite_settings=True) if body.config else {}
    except ValueError as e:
        ctx.db.rollback()
        raise HTTPException(422, str(e))
    _log(ctx, t.id, "tenant.create", f"{t.name} ({t.slug}), admin {owner.email}")
    ctx.db.commit()
    link = _invite(t, owner)
    sent = svc.send_email_t(t, owner.email, f"Your {config.PRODUCT_NAME} workspace is ready",
                            f"Hello {owner.name},\n\nYour {config.PRODUCT_NAME} workspace for {t.name} has been created. You are its administrator.\n\n"
                            f"Workspace code: {t.slug}\nChoose your password and start the setup here (link valid for 7 days):\n{link}\n")
    return {**_summary(ctx.db, t, {t.id: (1, None)}), "invite_link": link, "emailed": bool(sent[0]), "imported": counts}


@router.get("/tenants/{tid}")
def tenant_detail(tid: int, ctx: Ctx = Depends(require_platform)):
    t = ctx.db.get(Tenant, tid)
    if not t or t.kind != "client":
        raise HTTPException(404, "Client not found.")
    by_role = dict(ctx.db.execute(select(User.role, func.count(User.id)).where(User.tenant_id == tid, User.deleted_at.is_(None), User.active.is_(True)).group_by(User.role)).all())
    stats = {tid: (sum(by_role.values()), ctx.db.scalar(select(func.max(User.last_seen)).where(User.tenant_id == tid)))}
    log = ctx.db.scalars(select(AuditLog).where(AuditLog.tenant_id == tid, AuditLog.action.like("platform.%")).order_by(AuditLog.id.desc()).limit(15)).all()
    erp = (t.settings or {}).get("erpnext") or {}
    return {**_summary(ctx.db, t, stats), "users_by_role": by_role, "setup": status_of(ctx.db, t), "erp_connected": bool(erp.get("enabled")),
            "platform_log": [{"at": l.created_at.isoformat() + "Z", "action": l.action, "detail": l.detail} for l in log]}


class PatchTenant(BaseModel):
    name: str | None = None
    plan: str | None = None
    seat_limit: int | None = Field(None, ge=1, le=100000)
    active: bool | None = None
    notes: str | None = None
    modules: dict | None = None


@router.patch("/tenants/{tid}")
def patch_tenant(tid: int, body: PatchTenant, ctx: Ctx = Depends(require_platform)):
    t = ctx.db.get(Tenant, tid)
    if not t or t.kind != "client":
        raise HTTPException(404, "Client not found.")
    if body.plan is not None:
        if body.plan not in PLANS:
            raise HTTPException(422, "Plan must be one of: " + ", ".join(PLANS))
        t.plan = body.plan
    if body.name:
        t.name = body.name.strip()[:200]
    if body.seat_limit is not None:
        used = ctx.db.scalar(select(func.count(User.id)).where(User.tenant_id == tid, User.deleted_at.is_(None), User.active.is_(True)))
        if body.seat_limit < used:
            raise HTTPException(422, f"This client already has {used} active users.")
        t.seat_limit = body.seat_limit
    if body.notes is not None:
        t.notes = body.notes[:1000]
    if body.modules is not None:
        if set(body.modules) - set(M.KEYS):
            raise HTTPException(422, "Unknown module.")
        s = dict(t.settings or {})
        s["modules"] = {**M.defaults(), **{k: bool(v) for k, v in body.modules.items()}}
        t.settings = s
        flag_modified(t, "settings")
    if body.active is not None and body.active != t.active:
        t.active = body.active
        _log(ctx, tid, "tenant.reactivate" if t.active else "tenant.suspend")
    _log(ctx, tid, "tenant.update", ", ".join(k for k, v in body.model_dump().items() if v is not None))
    ctx.db.commit()
    return tenant_detail(tid, ctx)


@router.post("/tenants/{tid}/invite")
def resend_invite(tid: int, ctx: Ctx = Depends(require_platform)):
    t = ctx.db.get(Tenant, tid)
    adm = _admin_of(ctx.db, t) if t else None
    if not t or not adm or t.kind != "client":
        raise HTTPException(404, "Client or administrator not found.")
    link = _invite(t, adm)
    sent = svc.send_email_t(t, adm.email, f"Your {config.PRODUCT_NAME} workspace", f"Hello {adm.name},\n\nChoose your password here (valid for 7 days):\n{link}\n\nWorkspace code: {t.slug}")
    _log(ctx, tid, "invite.resend", adm.email)
    ctx.db.commit()
    return {"invite_link": link, "emailed": bool(sent[0])}


@router.post("/tenants/{tid}/open")
def open_as_admin(tid: int, ctx: Ctx = Depends(require_platform)):
    """A short-lived session as the client's administrator (the administrator's own session is not disturbed). Recorded in the client's audit log."""
    t = ctx.db.get(Tenant, tid)
    adm = _admin_of(ctx.db, t) if t else None
    if not t or not adm or t.kind != "client":
        raise HTTPException(404, "Client or administrator not found.")
    if not adm.session_id:
        adm.session_id = new_session_id()
    _log(ctx, tid, "impersonate", f"super-admin {ctx.user.email} opened the workspace as {adm.email}")
    ctx.db.commit()
    return {"token": make_token(adm.id, t.id, adm.session_id, ttl_hours=2, impersonated_by=ctx.user.id), "user": to_dict(adm), "workspace": t.slug}


@router.get("/tenants/{tid}/config")
def export_config(tid: int, catalog: bool = False, ctx: Ctx = Depends(require_platform)):
    t = ctx.db.get(Tenant, tid)
    if not t or t.kind != "client":
        raise HTTPException(404, "Client not found.")
    _log(ctx, tid, "config.export", "with catalogue" if catalog else "")
    ctx.db.commit()
    return tenantconfig.export_config(ctx.db, t, catalog)


@router.post("/tenants/{tid}/config")
def import_config(tid: int, body: dict, ctx: Ctx = Depends(require_platform)):
    t = ctx.db.get(Tenant, tid)
    if not t or t.kind != "client":
        raise HTTPException(404, "Client not found.")
    try:
        counts = tenantconfig.import_config(ctx.db, t, body)
    except ValueError as e:
        raise HTTPException(422, str(e))
    _log(ctx, tid, "config.import", str(counts))
    ctx.db.commit()
    return {"created": counts}


# ---- the vendor's own administrators ----
@router.get("/admins")
def admins(ctx: Ctx = Depends(require_platform)):
    return [{"id": u.id, "name": u.name, "email": u.email, "active": u.active, "last_seen": u.last_seen.isoformat() + "Z" if u.last_seen else None}
            for u in ctx.db.scalars(select(User).where(User.tenant_id == ctx.tid, User.deleted_at.is_(None)).order_by(User.name))]


class NewAdmin(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    email: str


@router.post("/admins")
def add_admin(body: NewAdmin, ctx: Ctx = Depends(require_platform)):
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", body.email):
        raise HTTPException(422, "Enter a valid email address.")
    if ctx.db.scalar(select(User.id).where(User.tenant_id == ctx.tid, User.email == body.email.lower())):
        raise HTTPException(409, "That person is already a platform administrator.")
    u = User(tenant_id=ctx.tid, email=body.email.lower(), name=body.name, role="superadmin", password_hash=hash_password(secrets.token_urlsafe(24)))
    ctx.db.add(u)
    ctx.db.flush()
    link = _invite(ctx.tenant, u)
    ctx.db.commit()
    return {"id": u.id, "invite_link": link}
