from datetime import date, timedelta
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy import select
from ..deps import Ctx, current
from ..models import Visit, Customer, Contact, User, Lead, Product, utcnow
from ..common import to_dict, get_or_404, decode_data_url, save_upload, watermark_text, log_activity, local_day_bounds_utc, parse_day, local_today
from ..geo import haversine_m
from ..notify import notify, send_email
from .. import svc

router = APIRouter(prefix="/visits", tags=["visits"])

PROSPECT_KEYS = ("name", "phone", "email", "company", "city", "address", "product_id", "notes", "temperature")


def _clean(d: dict | None) -> dict:
    out = {}
    for k in PROSPECT_KEYS:
        v = (d or {}).get(k)
        if v in (None, ""):
            continue
        out[k] = int(v) if k == "product_id" and str(v).isdigit() else (v.strip() if isinstance(v, str) else v)
    return out


class CustomerIn(BaseModel):
    name: str = ""
    phone: str = ""
    email: str = ""
    company: str = ""
    city: str = ""
    address: str = ""
    product_id: int | None = None
    notes: str = ""
    temperature: str = ""


class StartIn(BaseModel):
    customer_id: int | None = None
    customer: CustomerIn | None = None      # a new customer / lead captured on the spot (can be completed later)
    task_id: int | None = None
    lat: float | None = None
    lng: float | None = None


@router.post("/start")
def start(body: StartIn, ctx: Ctx = Depends(current)):
    return begin_visit(ctx, body.customer_id, body.task_id, body.lat, body.lng, body.customer.model_dump() if body.customer else None)


def begin_visit(ctx: Ctx, customer_id, task_id, lat, lng, prospect: dict | None = None) -> dict:
    """Check-in at a customer: records where you are and makes the visit ACTIVE. No OTP. Pick an existing customer or type a new one (name is enough to start)."""
    cust, pdata = None, _clean(prospect)
    if customer_id:
        cust = get_or_404(ctx.db, Customer, customer_id, ctx.tid)
    elif not pdata.get("name"):
        raise HTTPException(422, "Choose a customer, or type the new customer's name.")
    if lat is None or lng is None:
        raise HTTPException(422, "Location is needed to check in at the customer. Turn on location and try again.")
    if ctx.db.scalar(select(Visit.id).where(Visit.tenant_id == ctx.tid, Visit.user_id == ctx.user.id, Visit.ended_at.is_(None))):
        raise HTTPException(409, "You already have a visit in progress. Check out of it first.")
    dist = None
    if cust and cust.lat is not None:
        dist = haversine_m(lat, lng, cust.lat, cust.lng)
        if ctx.setting("enforce_visit_geofence", False) and dist > ctx.setting("geofence_default_m", 150) * 3:
            raise HTTPException(403, f"You are {int(dist)} m from {cust.name}. Reach the customer's location to check in.")
    v = Visit(tenant_id=ctx.tid, user_id=ctx.user.id, customer_id=cust.id if cust else None, task_id=task_id, lat=lat, lng=lng, start_dist_m=dist, prospect=pdata or None)
    ctx.db.add(v)
    ctx.db.commit()
    return {"visit": to_dict(v), "otp_required": False, "distance_m": round(dist) if dist is not None else None}


@router.post("/{vid}/customer")
def save_customer(vid: int, body: CustomerIn, bg: BackgroundTasks, ctx: Ctx = Depends(current)):
    """Add or update customer details during (or after) the visit. For a known customer the details are written to the customer record straight away."""
    v = get_or_404(ctx.db, Visit, vid, ctx.tid)
    if v.user_id != ctx.user.id:
        raise HTTPException(403, "This is not your visit.")
    data = _clean(body.model_dump())
    if v.customer_id and not v.ended_at:
        _update_customer(ctx, ctx.db.get(Customer, v.customer_id), data, v, bg, log=False)
    v.prospect = {**(v.prospect or {}), **data}
    ctx.db.commit()
    return to_dict(v)


def _update_customer(ctx: Ctx, cust: Customer, data: dict, v: Visit, bg, log=True):
    changed = []
    for k in ("phone", "email", "address", "city"):
        val = data.get(k)
        if val and getattr(cust, k) != val:
            setattr(cust, k, val[:400])
            changed.append(k)
    if "phone" in changed:
        cust.phone_key = svc.phone_key(cust.phone)
    if log:
        pr = ctx.db.get(Product, data["product_id"]) if data.get("product_id") else None
        bits = [f"Field visit by {ctx.user.name}"]
        if changed:
            bits.append("updated " + ", ".join(changed))
        if pr and pr.tenant_id == ctx.tid:
            bits.append(f"interested in {pr.name}")
        if v.mom:
            bits.append(f"summary: {v.mom[:200]}")
        log_activity(ctx.db, ctx.tid, "customer", cust.id, ctx.user.id, ". ".join(bits))
    if changed and (ctx.tenant.settings.get("erpnext") or {}).get("enabled"):
        from ..integrations.erpnext import push_entity_task
        bg.add_task(push_entity_task, ctx.tid, "customers", cust.id)
    return changed


def _missing(ctx: Ctx, v: Visit, cust: Customer | None, data: dict) -> list[str]:
    miss = []
    if cust:
        if not (cust.name or data.get("name")):
            miss.append("customer name")
        has_phone = cust.phone or data.get("phone") or ctx.db.scalar(select(Contact.id).where(Contact.tenant_id == ctx.tid, Contact.customer_id == cust.id, Contact.phone != ""))
        if not has_phone:
            miss.append("customer mobile number")
    else:
        if not data.get("name"):
            miss.append("customer name")
        if not data.get("phone"):
            miss.append("customer mobile number")
        if not data.get("product_id"):
            miss.append("product of interest")
    return miss


class EndIn(BaseModel):
    mom: str = ""                       # visit summary (required)
    customer: CustomerIn | None = None  # customer details (required if not already on record)
    email_to: str = ""
    photo: str | None = None
    lat: float | None = None
    lng: float | None = None


@router.post("/{vid}/end")
def end(vid: int, body: EndIn, bg: BackgroundTasks, ctx: Ctx = Depends(current)):
    """Check-out. Needs the customer's details and a visit summary. A new customer becomes a lead; a known one is updated; the visit is recorded either way."""
    v = get_or_404(ctx.db, Visit, vid, ctx.tid)
    if v.user_id != ctx.user.id:
        raise HTTPException(403, "This is not your visit.")
    if v.ended_at:
        raise HTTPException(409, "This visit has already ended.")
    data = {**(v.prospect or {}), **_clean(body.customer.model_dump() if body.customer else None)}
    summary = (body.mom or "").strip()
    cust = ctx.db.get(Customer, v.customer_id) if v.customer_id else None
    if not cust and svc.phone_key(data.get("phone")):   # a new customer who is already on record: use the record
        cust = ctx.db.scalar(select(Customer).where(Customer.tenant_id == ctx.tid, Customer.phone_key == svc.phone_key(data["phone"]), Customer.deleted_at.is_(None)))
    problems = _missing(ctx, v, cust, data)
    if len(summary) < 5:
        problems.append("visit summary")
    if problems:
        raise HTTPException(422, "Before you check out, please enter: " + ", ".join(problems) + ".")
    from .fieldx import pending_mandatory
    missing = pending_mandatory(ctx.db, ctx.tid, v.id)
    if missing:
        raise HTTPException(422, "Fill in the required form before ending the visit: " + ", ".join(missing))
    if ctx.setting("require_visit_photo", False) and not body.photo:
        raise HTTPException(422, "A photo of the visit is required (proof of visit).")
    pr = ctx.db.get(Product, data["product_id"]) if data.get("product_id") else None
    if pr and pr.tenant_id != ctx.tid:
        pr = None
        data.pop("product_id", None)
    v.ended_at, v.mom, v.end_lat, v.end_lng = utcnow(), summary, body.lat, body.lng
    v.prospect = data or None
    # ---- the customer side: update the known customer, or turn a new customer into a lead (or update the lead already on record)
    if cust:
        v.customer_id = cust.id
        _update_customer(ctx, cust, data, v, bg)
        v.outcome = f"Updated customer {cust.name}"
    else:
        lead = svc.find_duplicate_lead(ctx.db, ctx.tid, data.get("phone", ""), data.get("email", ""))
        if lead:
            for k in ("company", "email", "city"):
                if data.get(k) and not getattr(lead, k):
                    setattr(lead, k, data[k][:200])
            if pr and (not lead.product_id or (ctx.db.get(Product, lead.product_id) and ctx.db.get(Product, lead.product_id).code == "UNSPEC")):
                lead.product_id = pr.id
            v.outcome = f"Updated existing lead {lead.name}"
        else:
            lead, _ = svc.create_lead_core(ctx.db, ctx.tenant, ctx.user, {"name": data["name"], "company": data.get("company", ""), "phone": data["phone"], "email": data.get("email", ""),
                                                                       "city": data.get("city", ""), "product_id": data.get("product_id"), "notes": f"Captured during a field visit. {summary[:300]}",
                                                                       "owner_id": ctx.user.id, "temperature": data.get("temperature") or "warm"}, source="Field visit", dedupe="create", actor=ctx.user.name)
            v.outcome = f"New lead {lead.name}"
        v.lead_id = lead.id
        log_activity(ctx.db, ctx.tid, "lead", lead.id, ctx.user.id, f"Field visit by {ctx.user.name} ({int((v.ended_at - v.started_at).total_seconds() // 60)} min): {summary[:300]}")
        if (ctx.tenant.settings.get("erpnext") or {}).get("enabled"):
            from ..integrations.erpnext import push_entity_task
            bg.add_task(push_entity_task, ctx.tid, "leads", lead.id)
    from ..models import BeatVisit
    bv = ctx.db.scalar(select(BeatVisit).where(BeatVisit.tenant_id == ctx.tid, BeatVisit.visit_id == v.id))
    if bv:  # closes the planned beat stop
        bv.status, bv.completed_at = "visited", utcnow()
    if body.photo:
        raw, ext = decode_data_url(body.photo)
        v.photo = save_upload(ctx.tid, raw, ext, watermark_text(ctx.user.name, body.lat, body.lng, ctx.tenant))
    name = (cust.name if cust else data.get("name")) or "Visit"
    recipients = [e.strip() for e in (body.email_to or ctx.setting("mom_recipients_default", "")).split(",") if "@" in e]
    cust_email = (cust.email if cust else data.get("email")) or ""
    if cust_email and cust_email not in recipients:
        recipients.append(cust_email)
    if ctx.user.manager_id:
        m = ctx.db.get(User, ctx.user.manager_id)
        if m and m.email not in recipients:
            recipients.append(m.email)
    if recipients:
        mins = int((v.ended_at - v.started_at).total_seconds() // 60)
        subject = f"Minutes of Meeting - {name} ({ctx.user.name})"
        text = f"Customer: {name}\nAttended by: {ctx.user.name}\nDuration: {mins} min\n\n{summary}\n\n- {ctx.tenant.name} via Fieldwise"
        for r in recipients:
            send_email(r, subject, text)
        v.mom_emailed_to = ",".join(recipients)
    if ctx.user.manager_id:
        notify(ctx.db, ctx.tid, ctx.user.manager_id, "Visit completed", f"{ctx.user.name} - {name}: {summary[:120]}", "mom")
    ctx.db.commit()
    return to_dict(v)


def _row(ctx: Ctx, v: Visit, cname=None):
    d = {**to_dict(v), "customer_name": cname}
    if not cname and v.prospect:
        d["customer_name"] = v.prospect.get("name")
    d["is_new_customer"] = v.customer_id is None
    return d


@router.get("/active")
def active(ctx: Ctx = Depends(current)):
    v = ctx.db.scalar(select(Visit).where(Visit.tenant_id == ctx.tid, Visit.user_id == ctx.user.id, Visit.ended_at.is_(None)))
    if not v:
        return None
    c = ctx.db.get(Customer, v.customer_id) if v.customer_id else None
    out = _row(ctx, v, c.name if c else None)
    if c:
        out["customer"] = {"name": c.name, "phone": c.phone, "email": c.email, "city": c.city, "address": c.address}
    return out


@router.get("")
def list_visits(user_id: int | None = None, start: str | None = None, end: str | None = None, ctx: Ctx = Depends(current)):
    """Visits of me / my team, newest first. start and end (YYYY-MM-DD, inclusive) limit the period."""
    ids = ctx.team_ids()
    q = select(Visit, Customer.name, User.name).outerjoin(Customer, Customer.id == Visit.customer_id).join(User, User.id == Visit.user_id).where(
        Visit.tenant_id == ctx.tid, Visit.user_id.in_([user_id] if user_id in ids else ids))
    if start:
        q = q.where(Visit.started_at >= local_day_bounds_utc(ctx.tenant, parse_day(start, local_today(ctx.tenant)))[0])
    if end:
        q = q.where(Visit.started_at < local_day_bounds_utc(ctx.tenant, parse_day(end, local_today(ctx.tenant)))[1])
    out = []
    for v, cn, un in ctx.db.execute(q.order_by(Visit.id.desc()).limit(500)):
        out.append({**_row(ctx, v, cn), "user_name": un})
    return out
