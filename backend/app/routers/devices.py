"""Phone activation. The administrator registers a field member (with their mobile number) and hands over a one-time PIN; the person installs the app,
enters workspace + mobile number + PIN once, and the phone then stays signed in until the administrator revokes it or a new phone is activated."""
import re
import secrets
from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from ..db import get_db
from ..deps import Ctx, current, require
from ..models import User, Tenant, AppDevice, ActivationPin, Notification, utcnow
from ..common import get_or_404, to_dict
from ..security import make_token, hash_secret
from ..audit import audit
from ..notify import notify
from .. import config

router = APIRouter(tags=["devices"])
PIN_HOURS = 48
MAX_TRIES = 5
DEVICE_TOKEN_HOURS = 24 * 180


def digits(phone: str) -> str:
    d = re.sub(r"\D", "", phone or "")
    return d[-10:]


def _pin_status(db, uid: int):
    return db.scalar(select(ActivationPin).where(ActivationPin.user_id == uid, ActivationPin.used_at.is_(None), ActivationPin.revoked.is_(False),
                                                 ActivationPin.expires_at > utcnow()).order_by(ActivationPin.id.desc()))


class PinIn(BaseModel):
    scope: str = "field"
    phone: str = ""
    hours: int = Field(PIN_HOURS, ge=1, le=24 * 7)


@router.post("/users/{uid}/activation-pin")
def issue_pin(uid: int, body: PinIn, ctx: Ctx = Depends(require("admin"))):
    u = get_or_404(ctx.db, User, uid, ctx.tid)
    if body.scope not in ("field", "manager"):
        raise HTTPException(422, "Scope must be field or manager.")
    if not u.active:
        raise HTTPException(422, "Enable the user before issuing a PIN.")
    phone = body.phone.strip() or u.phone
    if len(digits(phone)) < 10:
        raise HTTPException(422, "Add the person's 10-digit mobile number first: the PIN works only for that number.")
    if body.phone.strip() and body.phone.strip() != u.phone:
        audit(ctx, "user.phone_change", "user", u.id, "via activation PIN")
        u.phone = body.phone.strip()
    for old in ctx.db.scalars(select(ActivationPin).where(ActivationPin.user_id == u.id, ActivationPin.used_at.is_(None), ActivationPin.revoked.is_(False))):
        old.revoked = True
    pin = f"{secrets.randbelow(10**6):06d}"
    ctx.db.add(ActivationPin(tenant_id=ctx.tid, user_id=u.id, phone=digits(phone), pin_hash=hash_secret(f"{ctx.tid}:{digits(phone)}:{pin}"), scope=body.scope,
                             expires_at=utcnow() + timedelta(hours=body.hours), created_by=ctx.user.id))
    audit(ctx, "device.pin_issued", "user", u.id, f"scope={body.scope}, valid {body.hours}h")
    ctx.db.commit()
    link = (config.PUBLIC_URL or "").rstrip("/") + "/download"
    return {"pin": pin, "workspace": ctx.tenant.slug, "phone": u.phone, "expires_hours": body.hours, "download_url": link, "scope": body.scope,
            "message": (f"Hi {u.name.split()[0]}, install the {config.PRODUCT_NAME} app from {link}\nThen open it and enter:\n"
                        f"Workspace: {ctx.tenant.slug}\nMobile: {u.phone}\nPIN: {pin}\n(valid for {body.hours} hours, works once, only on your number)")}


@router.get("/devices")
def list_devices(ctx: Ctx = Depends(require("admin"))):
    """Per user: the active phone (if any) and whether a PIN is waiting."""
    out = {}
    for d in ctx.db.scalars(select(AppDevice).where(AppDevice.tenant_id == ctx.tid, AppDevice.status == "active")):
        out[d.user_id] = {"device_id": d.id, "scope": d.scope, "label": d.label, "model": d.model, "app_version": d.app_version, "activated_at": d.activated_at.isoformat() + "Z",
                          "last_seen": d.last_seen.isoformat() + "Z" if d.last_seen else None, "pending_phone": d.pending_phone}
    for p in ctx.db.scalars(select(ActivationPin).where(ActivationPin.tenant_id == ctx.tid, ActivationPin.used_at.is_(None), ActivationPin.revoked.is_(False), ActivationPin.expires_at > utcnow())):
        out.setdefault(p.user_id, {})["pin_waiting_until"] = p.expires_at.isoformat() + "Z"
    return out


@router.post("/devices/{did}/revoke")
def revoke(did: int, ctx: Ctx = Depends(require("admin"))):
    d = get_or_404(ctx.db, AppDevice, did, ctx.tid)
    d.status, d.revoked_at, d.revoked_reason = "revoked", utcnow(), "admin"
    audit(ctx, "device.revoke", "user", d.user_id, d.label)
    ctx.db.commit()
    return {"ok": True}


class ActivateIn(BaseModel):
    workspace: str
    phone: str
    pin: str = Field(min_length=4, max_length=12)
    device_secret: str = Field(min_length=16, max_length=128)
    label: str = Field("", max_length=200)
    model: str = Field("", max_length=80)
    app_version: str = Field("", max_length=20)


BAD = "That workspace, mobile number and PIN do not match, or the PIN has expired. Ask your administrator for a new PIN."


@router.post("/auth/activate")
def activate(body: ActivateIn, request: Request, db=Depends(get_db)):
    t = db.scalar(select(Tenant).where(Tenant.slug == body.workspace.strip().lower()))
    ph = digits(body.phone)
    if not t or not t.active or len(ph) < 10:
        raise HTTPException(401, BAD)
    cands = list(db.scalars(select(ActivationPin).where(ActivationPin.tenant_id == t.id, ActivationPin.phone == ph, ActivationPin.used_at.is_(None),
                                                         ActivationPin.revoked.is_(False), ActivationPin.expires_at > utcnow())))
    cands = [c for c in cands if c.attempts < MAX_TRIES]
    hit = next((c for c in cands if secrets.compare_digest(c.pin_hash, hash_secret(f"{t.id}:{ph}:{body.pin.strip()}"))), None)
    if not hit:
        for c in cands:
            c.attempts += 1
        db.commit()
        raise HTTPException(401, BAD)
    u = db.get(User, hit.user_id)
    if not u or u.deleted_at or not u.active:
        raise HTTPException(401, BAD)
    hit.used_at = utcnow()
    for old in db.scalars(select(AppDevice).where(AppDevice.user_id == u.id, AppDevice.status == "active")):
        old.status, old.revoked_at, old.revoked_reason = "revoked", utcnow(), "replaced"
    dev = AppDevice(tenant_id=t.id, user_id=u.id, secret_hash=hash_secret(body.device_secret), scope=hit.scope, label=body.label, model=body.model,
                    app_version=body.app_version, phone=u.phone, last_seen=utcnow())
    db.add(dev)
    db.flush()
    db.add(Notification(tenant_id=t.id, user_id=hit.created_by or u.id, kind="info", title=f"{u.name} activated their phone", body=body.model or body.label))
    db.commit()
    token = make_token(u.id, t.id, u.session_id or "device", DEVICE_TOKEN_HOURS, device_id=dev.id)
    return {"token": token, "user": to_dict(u), "workspace": t.slug, "app_scope": dev.scope, "device_id": dev.id}


class RenewIn(BaseModel):
    device_secret: str = Field(min_length=16, max_length=128)
    app_version: str = Field("", max_length=20)


@router.post("/auth/device/renew")
def renew(body: RenewIn, ctx: Ctx = Depends(current)):
    """Called when the app starts: proves this is still the activated install and extends the token (so it never expires while in use)."""
    d = ctx.device
    if not d:
        raise HTTPException(400, "This session is not an activated phone.")
    if not secrets.compare_digest(d.secret_hash, hash_secret(body.device_secret)):
        d.status, d.revoked_at, d.revoked_reason = "revoked", utcnow(), "secret_mismatch"
        ctx.db.commit()
        raise HTTPException(401, "DEVICE_REVOKED: This phone is no longer registered. Ask your administrator for a new PIN.")
    if body.app_version:
        d.app_version = body.app_version
    ctx.db.commit()
    return {"token": make_token(ctx.user.id, ctx.tid, ctx.user.session_id or "device", DEVICE_TOKEN_HOURS, device_id=d.id), "app_scope": d.scope}


class PhoneChangeIn(BaseModel):
    phone: str = Field(min_length=10, max_length=30)


@router.post("/auth/device/phone-change")
def phone_change(body: PhoneChangeIn, ctx: Ctx = Depends(current)):
    """The person says their mobile number changed. Nothing changes until the administrator re-registers them and issues a new PIN."""
    if not ctx.device:
        raise HTTPException(400, "Only an activated phone can request this.")
    if len(digits(body.phone)) < 10:
        raise HTTPException(422, "Enter a 10-digit mobile number.")
    ctx.device.pending_phone = body.phone.strip()
    admins = [a for (a,) in ctx.db.execute(select(User.id).where(User.tenant_id == ctx.tid, User.active.is_(True), User.role.in_(("owner", "admin"))))]
    for a in admins:
        notify(ctx.db, ctx.tid, a, f"{ctx.user.name} changed their mobile number", f"New number {body.phone.strip()}. Issue a new app PIN from Users & hierarchy to re-register them.", "info", f"phone-{ctx.user.id}")
    ctx.db.commit()
    return {"ok": True, "message": "Your administrator has been told. You'll get a new PIN to continue on the new number."}
