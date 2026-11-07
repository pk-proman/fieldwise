"""Privacy and accountability: data export, workspace deletion, erasing a person's data, retention, audit trail."""
import io
import json
import shutil
import zipfile
from datetime import date, datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select, delete, update, text
from ..db import Base
from ..deps import Ctx, current, require
from ..models import AuditLog, User, LocationPing, Attendance, Tenant, GeoEvent, LoginAttempt, IntakeLog, SyncLog, utcnow
from ..common import to_dict, get_or_404
from ..audit import audit
from ..security import verify_password
from .. import config

router = APIRouter(prefix="/compliance", tags=["compliance"])
SKIP_TABLES = {"tenants", "job_locks"}


def _jsonable(v):
    return v.isoformat() if isinstance(v, (datetime, date)) else v


@router.get("/export")
def export_workspace(ctx: Ctx = Depends(require("owner"))):
    """ZIP with every record of this workspace as JSON (passwords, tokens and integration secrets excluded)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        meta = {k: v for k, v in (ctx.tenant.settings or {}).items() if k not in ("secrets", "intake", "erpnext", "smtp", "gateways", "ai")}
        z.writestr("workspace.json", json.dumps({"name": ctx.tenant.name, "slug": ctx.tenant.slug, "plan": ctx.tenant.plan, "settings": meta, "exported_at": utcnow().isoformat() + "Z"}, indent=1, default=str))
        for t in Base.metadata.sorted_tables:
            if t.name in SKIP_TABLES or "tenant_id" not in t.c:
                continue
            rows = ctx.db.execute(select(t).where(t.c.tenant_id == ctx.tid)).mappings().all()
            drop = {"password_hash", "session_id", "mfa_secret", "face_ref", "otp_hash"}
            z.writestr(f"{t.name}.json", json.dumps([{k: _jsonable(v) for k, v in r.items() if k not in drop} for r in rows], default=str))
    audit(ctx, "workspace.export", "tenant", ctx.tid)
    ctx.db.commit()
    return Response(buf.getvalue(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{ctx.tenant.slug}-export-{date.today()}.zip"'})


class DeleteIn(BaseModel):
    confirm_slug: str
    password: str


@router.post("/delete-workspace")
def delete_workspace(body: DeleteIn, ctx: Ctx = Depends(require("owner"))):
    """Permanently removes the workspace and all its data and files. Cannot be undone."""
    if body.confirm_slug != ctx.tenant.slug:
        raise HTTPException(422, "Type the workspace name exactly to confirm.")
    if not verify_password(body.password, ctx.user.password_hash):
        raise HTTPException(403, "Password is incorrect.")
    tid = ctx.tid
    for t in reversed(Base.metadata.sorted_tables):
        if t.name in ("tenants", "job_locks") or "tenant_id" not in t.c:
            continue
        ctx.db.execute(delete(t).where(t.c.tenant_id == tid))
    ctx.db.execute(delete(Tenant).where(Tenant.id == tid))
    ctx.db.commit()
    shutil.rmtree(config.UPLOAD_DIR / str(tid), ignore_errors=True)
    return {"deleted": True}


@router.post("/erase-user/{uid}")
def erase_user(uid: int, ctx: Ctx = Depends(require("admin"))):
    """Right to erasure for a person: their identity and location history are removed; business records they created stay (anonymised author)."""
    u = get_or_404(ctx.db, User, uid, ctx.tid)
    if u.role == "owner":
        raise HTTPException(409, "The workspace owner can't be erased. Delete the workspace instead.")
    ctx.db.execute(delete(LocationPing).where(LocationPing.tenant_id == ctx.tid, LocationPing.user_id == uid))
    ctx.db.execute(delete(GeoEvent).where(GeoEvent.tenant_id == ctx.tid, GeoEvent.user_id == uid))
    ctx.db.execute(update(Attendance).where(Attendance.tenant_id == ctx.tid, Attendance.user_id == uid).values(check_in_selfie=None, check_in_lat=None, check_in_lng=None, check_out_lat=None, check_out_lng=None))
    u.name, u.email, u.phone, u.face_ref, u.employee_code, u.erp_employee = f"Former employee {u.id}", f"erased-{u.id}@invalid.local", "", None, "", ""
    u.active, u.session_id, u.mfa_secret, u.mfa_enabled = False, None, None, False
    u.deleted_at = utcnow()
    audit(ctx, "user.erase", "user", uid)
    ctx.db.commit()
    return {"erased": True}


@router.get("/audit")
def audit_log(action: str | None = None, limit: int = Query(200, le=1000), ctx: Ctx = Depends(require("admin"))):
    q = select(AuditLog).where(AuditLog.tenant_id == ctx.tid)
    if action:
        q = q.where(AuditLog.action.like(f"{action}%"))
    names = {u.id: u.name for u in ctx.db.scalars(select(User).where(User.tenant_id == ctx.tid))}
    return [{**to_dict(a), "user_name": names.get(a.user_id)} for a in ctx.db.scalars(q.order_by(AuditLog.id.desc()).limit(limit))]


def purge_old_data(db):
    """Retention: raw location breadcrumbs and technical logs are kept for a limited time (workspace setting, default 180 days)."""
    now = utcnow()
    for t in db.scalars(select(Tenant)).all():
        days = int((t.settings or {}).get("ping_retention_days") or config.PING_RETENTION_DAYS)
        cutoff = now - timedelta(days=max(days, 30))
        db.execute(delete(LocationPing).where(LocationPing.tenant_id == t.id, LocationPing.ts < cutoff))
        db.execute(delete(GeoEvent).where(GeoEvent.tenant_id == t.id, GeoEvent.ts < cutoff))
    log_cut = now - timedelta(days=90)
    for M in (LoginAttempt, IntakeLog, SyncLog):
        db.execute(delete(M).where(M.created_at < log_cut))
    db.commit()
