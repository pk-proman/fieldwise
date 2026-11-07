from datetime import timedelta
import hashlib
import hmac
import os
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from ..deps import Ctx, current
from ..models import Visit, Customer, Contact, User, utcnow
from ..common import to_dict, get_or_404, decode_data_url, save_upload, watermark_text
from ..geo import haversine_m
from ..notify import notify, send_email, send_sms
from .. import svc
from ..security import make_otp
from .. import config

router = APIRouter(prefix="/visits", tags=["visits"])


def _h(otp: str, vid: int) -> str:
    return hmac.new(config.SECRET_KEY.encode(), f"{vid}:{otp}".encode(), hashlib.sha256).hexdigest()


class StartIn(BaseModel):
    customer_id: int
    task_id: int | None = None
    lat: float | None = None
    lng: float | None = None


@router.post("/start")
def start(body: StartIn, ctx: Ctx = Depends(current)):
    return begin_visit(ctx, body.customer_id, body.task_id, body.lat, body.lng)


def begin_visit(ctx: Ctx, customer_id: int, task_id, lat, lng) -> dict:
    body = StartIn(customer_id=customer_id, task_id=task_id, lat=lat, lng=lng)
    cust = get_or_404(ctx.db, Customer, body.customer_id, ctx.tid)
    open_visit = ctx.db.scalar(select(Visit).where(Visit.tenant_id == ctx.tid, Visit.user_id == ctx.user.id, Visit.ended_at.is_(None)))
    if open_visit:
        raise HTTPException(409, "You already have a visit in progress. End it first.")
    if cust.lat is not None and body.lat is not None and ctx.setting("enforce_geofence", True):
        d = haversine_m(body.lat, body.lng, cust.lat, cust.lng)
        if d > ctx.setting("geofence_default_m", 150) * 3:
            raise HTTPException(403, f"You are {int(d)} m from {cust.name}. Reach the customer's location to start the visit.")
    v = Visit(tenant_id=ctx.tid, user_id=ctx.user.id, customer_id=cust.id, task_id=body.task_id, lat=body.lat, lng=body.lng)
    ctx.db.add(v)
    ctx.db.flush()
    resp = {"visit": None, "otp_required": False}
    if ctx.setting("require_visit_otp", True):
        phone = cust.phone or ctx.db.scalar(select(Contact.phone).where(Contact.tenant_id == ctx.tid, Contact.customer_id == cust.id, Contact.phone != ""))
        if not phone:
            raise HTTPException(422, "This customer has no phone number for OTP verification. Add one first.")
        otp = make_otp()
        v.otp_hash, v.otp_expires = _h(otp, v.id), utcnow() + timedelta(minutes=15)
        msg = f"{otp} is your visit verification code for {ctx.user.name} ({ctx.tenant.name}). Share it only if the visit took place."
        digits = "".join(ch for ch in phone if ch.isdigit())
        sent, _ = svc.gateway_call(ctx.tenant, "sms", {"to": digits if len(digits) != 10 else "91" + digits, "message": msg})  # the workspace's own SMS gateway first
        sent = sent or send_sms(phone, msg)                                                                                    # then the platform-wide one
        resp.update(otp_required=True, sent_to=phone[:3] + "•••" + phone[-3:])
        if not sent:
            if config.ENV == "production":  # never reveal the code to the person who is supposed to collect it from the customer
                raise HTTPException(503, "The verification code couldn't be sent: no working SMS gateway. Ask an administrator to set one up in Settings, or switch off visit OTP.")
            resp["dev_otp"] = otp  # development / demo only so the flow can be tried without a gateway
    ctx.db.commit()
    resp["visit"] = to_dict(v)
    return resp


class OtpIn(BaseModel):
    otp: str


@router.post("/{vid}/verify-otp")
def verify(vid: int, body: OtpIn, ctx: Ctx = Depends(current)):
    v = get_or_404(ctx.db, Visit, vid, ctx.tid)
    if v.user_id != ctx.user.id:
        raise HTTPException(403, "This is not your visit.")
    if v.otp_verified:
        return to_dict(v)
    if v.otp_attempts >= 5:
        raise HTTPException(429, "Too many wrong codes. Start the visit again.")
    if not v.otp_expires or v.otp_expires < utcnow():
        raise HTTPException(410, "This code expired. Start the visit again for a new code.")
    v.otp_attempts += 1
    if not hmac.compare_digest(_h(body.otp.strip(), v.id), v.otp_hash or ""):
        ctx.db.commit()
        raise HTTPException(400, f"That code is not right. {5 - v.otp_attempts} attempt(s) left.")
    v.otp_verified = True
    v.waiting_minutes = int((utcnow() - v.started_at).total_seconds() // 60)
    ctx.db.commit()
    return to_dict(v)


class EndIn(BaseModel):
    mom: str = ""
    email_to: str = ""
    photo: str | None = None
    lat: float | None = None
    lng: float | None = None


@router.post("/{vid}/end")
def end(vid: int, body: EndIn, ctx: Ctx = Depends(current)):
    v = get_or_404(ctx.db, Visit, vid, ctx.tid)
    if v.user_id != ctx.user.id:
        raise HTTPException(403, "This is not your visit.")
    if v.ended_at:
        raise HTTPException(409, "This visit has already ended.")
    if ctx.setting("require_visit_otp", True) and not v.otp_verified:
        raise HTTPException(403, "Verify the customer's OTP before ending the visit.")
    from .fieldx import pending_mandatory
    missing = pending_mandatory(ctx.db, ctx.tid, v.id)
    if missing:
        raise HTTPException(422, "Fill in the required form before ending the visit: " + ", ".join(missing))
    if ctx.setting("require_visit_photo", False) and not body.photo:
        raise HTTPException(422, "A photo of the visit is required (proof of visit).")
    v.ended_at, v.mom = utcnow(), body.mom.strip()
    from ..models import BeatVisit
    bv = ctx.db.scalar(select(BeatVisit).where(BeatVisit.tenant_id == ctx.tid, BeatVisit.visit_id == v.id))
    if bv:  # closes the planned beat stop
        bv.status, bv.completed_at = "visited", utcnow()
    if body.photo:
        raw, ext = decode_data_url(body.photo)
        v.photo = save_upload(ctx.tid, raw, ext, watermark_text(ctx.user.name, body.lat, body.lng, ctx.tenant))
    cust = ctx.db.get(Customer, v.customer_id) if v.customer_id else None
    recipients = [e.strip() for e in (body.email_to or ctx.setting("mom_recipients_default", "")).split(",") if "@" in e]
    if cust and cust.email and cust.email not in recipients:
        recipients.append(cust.email)
    if ctx.user.manager_id:
        m = ctx.db.get(User, ctx.user.manager_id)
        if m and m.email not in recipients:
            recipients.append(m.email)
    if v.mom and recipients:
        mins = int((v.ended_at - v.started_at).total_seconds() // 60)
        subject = f"Minutes of Meeting - {cust.name if cust else 'Visit'} ({ctx.user.name})"
        text = f"Customer: {cust.name if cust else '-'}\nAttended by: {ctx.user.name}\nDuration: {mins} min\n\n{v.mom}\n\n- {ctx.tenant.name} via Fieldwise"
        sent = [r for r in recipients if send_email(r, subject, text)]
        v.mom_emailed_to = ",".join(recipients)
        if ctx.user.manager_id:
            notify(ctx.db, ctx.tid, ctx.user.manager_id, "MOM submitted", f"{ctx.user.name} - {cust.name if cust else 'visit'}: {v.mom[:120]}", "mom")
    ctx.db.commit()
    return to_dict(v)


@router.get("/active")
def active(ctx: Ctx = Depends(current)):
    v = ctx.db.scalar(select(Visit).where(Visit.tenant_id == ctx.tid, Visit.user_id == ctx.user.id, Visit.ended_at.is_(None)))
    if not v:
        return None
    c = ctx.db.get(Customer, v.customer_id) if v.customer_id else None
    return {**to_dict(v), "customer_name": c.name if c else None}


@router.get("")
def list_visits(user_id: int | None = None, ctx: Ctx = Depends(current)):
    ids = ctx.team_ids()
    q = select(Visit, Customer.name, User.name).outerjoin(Customer, Customer.id == Visit.customer_id).join(User, User.id == Visit.user_id).where(
        Visit.tenant_id == ctx.tid, Visit.user_id.in_([user_id] if user_id in ids else ids)).order_by(Visit.id.desc()).limit(200)
    return [{**to_dict(v), "customer_name": cn, "user_name": un} for v, cn, un in ctx.db.execute(q)]
