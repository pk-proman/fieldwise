"""Sales workflow: quotations (ERPNext), follow-ups, meetings with action items, and related-records panel."""
from datetime import date, datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from ..deps import Ctx, current, require
from ..models import (Quotation, Followup, Meeting, Lead, Customer, Contact, Product, Task, User, Activity, utcnow)
from ..common import to_dict, get_or_404, log_activity, decode_data_url, save_upload, watermark_text
from ..geo import haversine_m
from ..notify import notify, send_email
from ..integrations import erpnext as erp
from .crm import convert_lead_to_customer, _quote_count
from .. import svc

router = APIRouter(tags=["sales"])
OUTCOMES = ["order_placed", "quotation_requested", "follow_up_required", "negotiation", "not_interested", "no_answer", "rescheduled"]
KINDS = ("call", "visit", "email", "whatsapp", "meeting")


def _erp_client(ctx: Ctx):
    cfg = erp._cfg(ctx.tenant)
    return erp.ERPNextClient(cfg) if cfg.get("enabled") else None


def _own(ctx: Ctx, uid: int):
    if uid not in ctx.team_ids():
        raise HTTPException(403, "This belongs to someone outside your team.")


# ======================================================================= Quotations
class ItemIn(BaseModel):
    product_id: int
    qty: float = Field(gt=0, le=1_000_000)
    rate: float | None = Field(None, ge=0)
    discount_pct: float = Field(0, ge=0, le=100)


class ChargeIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    amount: float = Field(ge=0, le=100_000_000)


class QuoteIn(BaseModel):
    lead_id: int | None = None
    customer_id: int | None = None
    items: list[ItemIn] = Field(min_length=1, max_length=50)
    valid_till: date | None = None
    tax_pct: float = Field(18, ge=0, le=100)
    notes: str = ""
    terms: str | None = None
    charges: list[ChargeIn] = []


def _try_push(ctx: Ctx, q: Quotation):
    client = _erp_client(ctx)
    if not client:
        return
    try:
        erp.push_quotation(ctx.db, ctx.tenant, client, q)
    except Exception as e:  # keep the quotation; user can retry
        q.erp_error = str(e)[:500]


@router.post("/quotations")
def create_quotation(body: QuoteIn, ctx: Ctx = Depends(current)):
    if bool(body.lead_id) == bool(body.customer_id):
        raise HTTPException(422, "Create a quotation for either a lead or a customer.")
    lead = get_or_404(ctx.db, Lead, body.lead_id, ctx.tid) if body.lead_id else None
    if body.customer_id:
        get_or_404(ctx.db, Customer, body.customer_id, ctx.tid)
    if lead and not lead.qualified_at:
        raise HTTPException(422, "Qualify the lead before quoting: open it and complete the qualification checklist.")
    if lead and svc.stage_map(ctx.tenant).get(lead.status, {}).get("kind") in ("won", "lost"):
        raise HTTPException(409, "This lead is closed.")
    items, sub, disc = [], 0.0, 0.0
    for it in body.items:
        p = ctx.db.get(Product, it.product_id)
        if not p or p.tenant_id != ctx.tid or p.deleted_at:
            raise HTTPException(422, "One of the products is not in the product master.")
        rate = it.rate if it.rate is not None else p.price
        gross = it.qty * rate
        d = gross * it.discount_pct / 100
        items.append({"product_id": p.id, "code": p.code, "name": p.name, "uom": p.uom, "qty": it.qty, "rate": rate, "discount_pct": it.discount_pct, "amount": round(gross - d, 2)})
        sub, disc = sub + gross, disc + d
    net = sub - disc
    charges = [{"name": c.name, "amount": round(c.amount, 2)} for c in body.charges]
    ch_total = round(sum(c["amount"] for c in charges), 2)
    total = round((net + ch_total) * (1 + body.tax_pct / 100), 2)
    from .docs import next_number, DEFAULT_TERMS
    qset = (ctx.tenant.settings or {}).get("quote") or {}
    terms = body.terms if body.terms is not None else qset.get("terms", DEFAULT_TERMS)
    validity = int(qset.get("validity_days", 15))
    limit = float(ctx.setting("discount_approval_pct", 10))
    needs_approval = max(i["discount_pct"] for i in items) > limit and not ctx.is_at_least("manager")
    q = Quotation(tenant_id=ctx.tid, number=next_number(ctx.tenant, "quote", ctx.db), terms=terms, charges=charges, charges_total=ch_total, lead_id=body.lead_id, customer_id=body.customer_id, created_by=ctx.user.id, items=items,
                  subtotal=round(sub, 2), discount_total=round(disc, 2), tax_pct=body.tax_pct, total=total, notes=body.notes,
                  valid_till=body.valid_till or date.today() + timedelta(days=validity), status="pending_approval" if needs_approval else "draft")
    ctx.db.add(q)
    ctx.db.flush()
    if needs_approval:
        mgr = ctx.user.manager_id
        if mgr:
            notify(ctx.db, ctx.tid, mgr, "Quotation needs approval", f"{ctx.user.name}: {q.number} has a discount above {limit:g}%.", "approval")
    else:
        _try_push(ctx, q)
    if lead:
        stm = svc.stage_map(ctx.tenant)
        qs = next((x["key"] for x in svc.stages_of(ctx.tenant) if x["kind"] == "open" and x["gate"] == "quoted"), None)
        cur = stm.get(lead.status, {})
        if qs and cur.get("kind") == "open" and cur.get("gate") != "quoted":
            lead.status, lead.stage_changed_at = qs, utcnow()
        log_activity(ctx.db, ctx.tid, "lead", lead.id, ctx.user.id, f"{ctx.user.name} created quotation {q.number}" + (f" (ERPNext {q.erp_name})" if q.erp_name else "") + f" for {q.total:,.0f}.")
    ctx.db.commit()
    return to_dict(q)


@router.get("/quotations")
def list_quotations(lead_id: int | None = None, customer_id: int | None = None, status: str | None = None, ctx: Ctx = Depends(current)):
    q = select(Quotation).where(Quotation.tenant_id == ctx.tid, Quotation.deleted_at.is_(None), Quotation.created_by.in_(ctx.team_ids()))
    if lead_id:
        q = q.where(Quotation.lead_id == lead_id)
    if customer_id:
        q = q.where(Quotation.customer_id == customer_id)
    if status:
        q = q.where(Quotation.status == status)
    return [to_dict(x) for x in ctx.db.scalars(q.order_by(Quotation.id.desc()).limit(200))]


def _q(ctx: Ctx, qid: int) -> Quotation:
    q = get_or_404(ctx.db, Quotation, qid, ctx.tid)
    _own(ctx, q.created_by)
    return q


@router.post("/quotations/{qid}/approve")
def approve_quotation(qid: int, ctx: Ctx = Depends(require("manager"))):
    q = _q(ctx, qid)
    if q.status != "pending_approval":
        raise HTTPException(409, "This quotation isn't waiting for approval.")
    if q.created_by == ctx.user.id:
        raise HTTPException(403, "You can't approve your own discount.")
    q.status, q.approver_id = "draft", ctx.user.id
    _try_push(ctx, q)
    notify(ctx.db, ctx.tid, q.created_by, "Quotation approved", f"{q.number} was approved.", "approval")
    ctx.db.commit()
    return to_dict(q)


@router.post("/quotations/{qid}/reject-discount")
def reject_quotation(qid: int, ctx: Ctx = Depends(require("manager"))):
    q = _q(ctx, qid)
    q.status, q.approver_id = "rejected", ctx.user.id
    notify(ctx.db, ctx.tid, q.created_by, "Quotation rejected", f"{q.number}: discount not approved. Revise and resubmit.", "approval")
    ctx.db.commit()
    return to_dict(q)


@router.post("/quotations/{qid}/push")
def push_quotation(qid: int, ctx: Ctx = Depends(current)):
    q = _q(ctx, qid)
    if q.status == "pending_approval":
        raise HTTPException(409, "Waiting for discount approval.")
    if q.erp_name:
        raise HTTPException(409, f"Already in ERPNext as {q.erp_name}.")
    client = _erp_client(ctx)
    if not client:
        raise HTTPException(422, "ERPNext integration is not enabled. Ask an admin to set it up in Settings.")
    try:
        erp.push_quotation(ctx.db, ctx.tenant, client, q)
    except Exception as e:
        q.erp_error = str(e)[:500]
        ctx.db.commit()
        raise HTTPException(502, f"ERPNext said: {e}")
    ctx.db.commit()
    return to_dict(q)


@router.post("/quotations/{qid}/refresh")
def refresh_quotation(qid: int, ctx: Ctx = Depends(current)):
    q = _q(ctx, qid)
    client = _erp_client(ctx)
    if not (client and q.erp_name):
        raise HTTPException(422, "This quotation isn't in ERPNext yet.")
    try:
        erp.refresh_quotation(ctx.db, ctx.tenant, client, q)
    except Exception as e:
        raise HTTPException(502, f"ERPNext said: {e}")
    ctx.db.commit()
    return to_dict(q)


class QStatusIn(BaseModel):
    status: str  # sent|accepted|rejected


@router.post("/quotations/{qid}/status")
def quotation_status(qid: int, body: QStatusIn, ctx: Ctx = Depends(current)):
    q = _q(ctx, qid)
    if body.status not in ("sent", "accepted", "rejected"):
        raise HTTPException(422, "Status must be sent, accepted or rejected.")
    if q.status == "pending_approval":
        raise HTTPException(409, "Waiting for discount approval.")
    q.status = body.status
    if q.lead_id:
        lead = ctx.db.get(Lead, q.lead_id)
        if body.status == "sent":
            log_activity(ctx.db, ctx.tid, "lead", lead.id, ctx.user.id, f"{q.number} sent to the customer.")
        if body.status == "accepted" and svc.stage_map(ctx.tenant).get(lead.status, {}).get("kind") == "open":
            neg = next((x["key"] for x in svc.stages_of(ctx.tenant) if x["kind"] == "open" and x["gate"] == "quoted" and x["key"] != "proposal"), None) or lead.status
            lead.status, lead.stage_changed_at = neg, utcnow()
            log_activity(ctx.db, ctx.tid, "lead", lead.id, ctx.user.id, f"Customer accepted {q.number}. Convert the lead to close the deal.")
    ctx.db.commit()
    return to_dict(q)


# ======================================================================= Follow-ups
class FollowIn(BaseModel):
    lead_id: int | None = None
    customer_id: int | None = None
    kind: str = "call"
    due_at: datetime
    notes: str = ""
    owner_id: int | None = None
    repeat: str = ""


def _fu_dict(ctx, rows):
    lead_ids = {f.lead_id for f in rows if f.lead_id}
    cust_ids = {f.customer_id for f in rows if f.customer_id}
    leads = {l.id: l for l in ctx.db.scalars(select(Lead).where(Lead.id.in_(lead_ids or {0})))}
    custs = {c.id: c for c in ctx.db.scalars(select(Customer).where(Customer.id.in_(cust_ids or {0})))}
    users = {u.id: u.name for u in ctx.db.scalars(select(User).where(User.tenant_id == ctx.tid))}
    out = []
    for f in rows:
        l, c = leads.get(f.lead_id), custs.get(f.customer_id)
        out.append({**to_dict(f), "lead_name": l.name if l else None, "customer_name": (c.name if c else (l.company or l.name) if l else None),
                    "phone": (l.phone if l else c.phone if c else ""), "email": (l.email if l else c.email if c else ""), "owner_name": users.get(f.owner_id)})
    return out


@router.post("/followups")
def create_followup(body: FollowIn, ctx: Ctx = Depends(current)):
    if body.kind not in KINDS:
        raise HTTPException(422, "Follow-up type must be call, visit, email, whatsapp or meeting.")
    if body.repeat not in ("", "daily", "weekly", "monthly"):
        raise HTTPException(422, "Repeat must be daily, weekly or monthly.")
    if not (body.lead_id or body.customer_id):
        raise HTTPException(422, "Pick a lead or customer for this follow-up.")
    if body.lead_id:
        get_or_404(ctx.db, Lead, body.lead_id, ctx.tid)
    if body.customer_id:
        get_or_404(ctx.db, Customer, body.customer_id, ctx.tid)
    owner = body.owner_id or ctx.user.id
    _own(ctx, owner)
    f = Followup(tenant_id=ctx.tid, lead_id=body.lead_id, customer_id=body.customer_id, owner_id=owner, kind=body.kind,
                 due_at=body.due_at.replace(tzinfo=None), notes=body.notes[:500], repeat=body.repeat)
    ctx.db.add(f)
    if owner != ctx.user.id:
        notify(ctx.db, ctx.tid, owner, "Follow-up assigned", f"{ctx.user.name}: {body.kind} {body.notes[:80]}", "followup")
    ctx.db.commit()
    return to_dict(f)


@router.get("/followups")
def list_followups(scope: str = "mine", bucket: str = "open", lead_id: int | None = None, customer_id: int | None = None, ctx: Ctx = Depends(current)):
    ids = [ctx.user.id] if scope == "mine" else ctx.team_ids()
    q = select(Followup).where(Followup.tenant_id == ctx.tid, Followup.deleted_at.is_(None), Followup.owner_id.in_(ids))
    now = utcnow()
    if lead_id:
        q = q.where(Followup.lead_id == lead_id)
    if customer_id:
        q = q.where(Followup.customer_id == customer_id)
    if bucket == "done":
        q = q.where(Followup.status == "done").order_by(Followup.completed_at.desc())
    else:
        q = q.where(Followup.status == "pending").order_by(Followup.due_at)
    rows = ctx.db.scalars(q.limit(400)).all()
    out = _fu_dict(ctx, rows)
    for r, f in zip(out, rows):
        r["overdue"] = f.status == "pending" and f.due_at < now
    return out


class DoneIn(BaseModel):
    outcome: str
    notes: str = ""
    next_at: datetime | None = None
    next_kind: str = "call"


@router.post("/followups/{fid}/complete")
def complete_followup(fid: int, body: DoneIn, ctx: Ctx = Depends(current)):
    f = get_or_404(ctx.db, Followup, fid, ctx.tid)
    _own(ctx, f.owner_id)
    if f.status != "pending":
        raise HTTPException(409, "This follow-up is already closed.")
    if body.outcome not in OUTCOMES:
        raise HTTPException(422, "Choose an outcome.")
    f.status, f.outcome, f.outcome_notes, f.completed_at = "done", body.outcome, body.notes[:500], utcnow()
    text = f"{f.kind.title()} follow-up done: {body.outcome.replace('_', ' ')}. {body.notes}".strip()
    if f.lead_id:
        log_activity(ctx.db, ctx.tid, "lead", f.lead_id, ctx.user.id, text)
        lead = ctx.db.get(Lead, f.lead_id)
        if lead.status == "new" and body.outcome != "no_answer":
            lead.status, lead.stage_changed_at = "contacted", utcnow()
        if body.outcome == "not_interested" and lead.status not in ("won", "lost"):
            notify(ctx.db, ctx.tid, f.owner_id, "Lead not interested", f"Mark '{lead.name}' as lost with a reason, or keep nurturing.", "followup")
    if f.customer_id:
        log_activity(ctx.db, ctx.tid, "customer", f.customer_id, ctx.user.id, text)
    nxt_at = body.next_at.replace(tzinfo=None) if body.next_at else None
    step = {"daily": 1, "weekly": 7, "monthly": 30}.get(f.repeat)
    if not nxt_at and step:  # recurring visit plan
        nxt_at = f.due_at + timedelta(days=step)
        while nxt_at < utcnow():
            nxt_at += timedelta(days=step)
    if nxt_at:
        ctx.db.add(Followup(tenant_id=ctx.tid, lead_id=f.lead_id, customer_id=f.customer_id, owner_id=f.owner_id, kind=body.next_kind if body.next_at else f.kind,
                            due_at=nxt_at, repeat=f.repeat, notes="Next follow-up"))
    ctx.db.commit()
    return to_dict(f)


@router.patch("/followups/{fid}")
def edit_followup(fid: int, body: dict, ctx: Ctx = Depends(current)):
    f = get_or_404(ctx.db, Followup, fid, ctx.tid)
    _own(ctx, f.owner_id)
    if "due_at" in body:
        f.due_at, f.reminded = datetime.fromisoformat(str(body["due_at"]).replace("Z", "+00:00")).replace(tzinfo=None), False
    for k in ("notes", "kind"):
        if k in body:
            setattr(f, k, body[k])
    if body.get("status") == "cancelled":
        f.status = "cancelled"
    ctx.db.commit()
    return to_dict(f)


# ======================================================================= Meetings
class MeetingIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    customer_id: int | None = None
    lead_id: int | None = None
    scheduled_at: datetime
    duration_min: int = Field(45, ge=5, le=600)
    mode: str = "in_person"
    location: str = ""
    attendees: str = ""
    agenda: str = ""
    owner_id: int | None = None


def _meeting_dicts(ctx, rows):
    custs = {c.id: c.name for c in ctx.db.scalars(select(Customer).where(Customer.tenant_id == ctx.tid, Customer.id.in_({m.customer_id for m in rows if m.customer_id} or {0})))}
    leads = {l.id: l.name for l in ctx.db.scalars(select(Lead).where(Lead.tenant_id == ctx.tid, Lead.id.in_({m.lead_id for m in rows if m.lead_id} or {0})))}
    users = {u.id: u.name for u in ctx.db.scalars(select(User).where(User.tenant_id == ctx.tid))}
    counts = dict(ctx.db.execute(select(Task.meeting_id, func.count()).where(Task.tenant_id == ctx.tid, Task.meeting_id.in_({m.id for m in rows} or {0}), Task.deleted_at.is_(None)).group_by(Task.meeting_id)).all())
    return [{**to_dict(m), "customer_name": custs.get(m.customer_id), "lead_name": leads.get(m.lead_id), "owner_name": users.get(m.owner_id), "task_count": counts.get(m.id, 0)} for m in rows]


@router.post("/meetings")
def create_meeting(body: MeetingIn, ctx: Ctx = Depends(current)):
    if body.mode not in ("in_person", "call", "video"):
        raise HTTPException(422, "Mode must be in person, call or video.")
    if body.customer_id:
        get_or_404(ctx.db, Customer, body.customer_id, ctx.tid)
    if body.lead_id:
        get_or_404(ctx.db, Lead, body.lead_id, ctx.tid)
    owner = body.owner_id or ctx.user.id
    _own(ctx, owner)
    m = Meeting(tenant_id=ctx.tid, owner_id=owner, **body.model_dump(exclude={"owner_id", "scheduled_at"}), scheduled_at=body.scheduled_at.replace(tzinfo=None))
    ctx.db.add(m)
    ctx.db.flush()
    if m.lead_id:
        log_activity(ctx.db, ctx.tid, "lead", m.lead_id, ctx.user.id, f"Meeting scheduled: {m.title} on {m.scheduled_at:%d %b %H:%M}.")
    if owner != ctx.user.id:
        notify(ctx.db, ctx.tid, owner, "Meeting assigned", f"{m.title} on {m.scheduled_at:%d %b %H:%M}", "meeting")
    ctx.db.commit()
    return to_dict(m)


@router.get("/meetings")
def list_meetings(scope: str = "mine", status: str | None = None, lead_id: int | None = None, customer_id: int | None = None, ctx: Ctx = Depends(current)):
    ids = [ctx.user.id] if scope == "mine" else ctx.team_ids()
    q = select(Meeting).where(Meeting.tenant_id == ctx.tid, Meeting.deleted_at.is_(None), Meeting.owner_id.in_(ids))
    if status:
        q = q.where(Meeting.status == status)
    if lead_id:
        q = q.where(Meeting.lead_id == lead_id)
    if customer_id:
        q = q.where(Meeting.customer_id == customer_id)
    return _meeting_dicts(ctx, ctx.db.scalars(q.order_by(Meeting.scheduled_at.desc()).limit(300)).all())


def _m(ctx: Ctx, mid: int) -> Meeting:
    m = get_or_404(ctx.db, Meeting, mid, ctx.tid)
    _own(ctx, m.owner_id)
    return m


@router.patch("/meetings/{mid}")
def edit_meeting(mid: int, body: dict, ctx: Ctx = Depends(current)):
    m = _m(ctx, mid)
    if m.status in ("completed", "cancelled"):
        raise HTTPException(409, "This meeting is closed.")
    if "scheduled_at" in body:
        m.scheduled_at, m.reminded = datetime.fromisoformat(str(body["scheduled_at"]).replace("Z", "+00:00")).replace(tzinfo=None), False
    for k in ("title", "location", "agenda", "attendees", "duration_min"):
        if k in body:
            setattr(m, k, body[k])
    if body.get("status") == "cancelled":
        m.status = "cancelled"
    ctx.db.commit()
    return to_dict(m)


class StartIn(BaseModel):
    lat: float | None = None
    lng: float | None = None


@router.post("/meetings/{mid}/start")
def start_meeting(mid: int, body: StartIn, ctx: Ctx = Depends(current)):
    m = _m(ctx, mid)
    if m.status != "scheduled":
        raise HTTPException(409, f"This meeting is already {m.status.replace('_', ' ')}.")
    if m.mode == "in_person" and m.customer_id and ctx.setting("enforce_geofence", True):
        c = ctx.db.get(Customer, m.customer_id)
        if c and c.lat is not None:
            if body.lat is None:
                raise HTTPException(422, "Turn on GPS to check in to this meeting.")
            d = haversine_m(body.lat, body.lng, c.lat, c.lng)
            if d > ctx.setting("geofence_default_m", 150) * 3:
                raise HTTPException(403, f"You are {int(d)} m from {c.name}. Reach the customer's location to check in.")
    m.status, m.started_at, m.start_lat, m.start_lng = "in_progress", utcnow(), body.lat, body.lng
    ctx.db.commit()
    return to_dict(m)


class ActionItem(BaseModel):
    title: str = Field(min_length=2, max_length=300)
    due_at: datetime | None = None
    assignee_id: int | None = None
    priority: str = "medium"


class CompleteIn(BaseModel):
    outcome: str
    mom: str = Field(min_length=3)
    signature: str | None = None
    photo: str | None = None
    action_items: list[ActionItem] = []
    next_followup_at: datetime | None = None
    next_followup_kind: str = "call"
    email_to: str = ""
    lat: float | None = None
    lng: float | None = None


@router.post("/meetings/{mid}/complete")
def complete_meeting(mid: int, body: CompleteIn, ctx: Ctx = Depends(current)):
    m = _m(ctx, mid)
    if m.status not in ("scheduled", "in_progress"):
        raise HTTPException(409, "This meeting is already closed.")
    if body.outcome not in OUTCOMES:
        raise HTTPException(422, "Choose the meeting outcome.")
    m.status, m.ended_at, m.outcome, m.mom = "completed", utcnow(), body.outcome, body.mom.strip()
    if not m.started_at:
        m.started_at = m.scheduled_at
    wm = watermark_text(ctx.user.name, body.lat, body.lng, ctx.tenant)
    if body.signature:
        raw, ext = decode_data_url(body.signature)
        m.signature = save_upload(ctx.tid, raw, "png")
    if body.photo:
        raw, ext = decode_data_url(body.photo)
        m.photo = save_upload(ctx.tid, raw, ext, wm)
    team = set(ctx.team_ids())
    for a in body.action_items:
        who = a.assignee_id if a.assignee_id in team else ctx.user.id
        ctx.db.add(Task(tenant_id=ctx.tid, title=a.title, description=f"Action item from meeting '{m.title}'", assignee_id=who, created_by=ctx.user.id,
                        customer_id=m.customer_id, lead_id=m.lead_id, meeting_id=m.id, priority=a.priority if a.priority in ("low", "medium", "high", "urgent") else "medium",
                        due_at=a.due_at.replace(tzinfo=None) if a.due_at else None))
        if who != ctx.user.id:
            notify(ctx.db, ctx.tid, who, "Action item assigned", f"{a.title} (from {m.title})", "task")
    if body.next_followup_at:
        if m.lead_id or m.customer_id:
            ctx.db.add(Followup(tenant_id=ctx.tid, lead_id=m.lead_id, customer_id=m.customer_id, owner_id=m.owner_id, kind=body.next_followup_kind if body.next_followup_kind in KINDS else "call",
                                due_at=body.next_followup_at.replace(tzinfo=None), notes=f"After meeting: {m.title}"))
    summary = f"Meeting '{m.title}' completed: {body.outcome.replace('_', ' ')}. {m.mom[:300]}"
    if m.lead_id:
        log_activity(ctx.db, ctx.tid, "lead", m.lead_id, ctx.user.id, summary)
        lead = ctx.db.get(Lead, m.lead_id)
        if lead.status == "new":
            lead.status, lead.stage_changed_at = "contacted", utcnow()
    if m.customer_id:
        log_activity(ctx.db, ctx.tid, "customer", m.customer_id, ctx.user.id, summary)
    recips = [e.strip() for e in (body.email_to or ctx.setting("mom_recipients_default", "")).split(",") if "@" in e]
    cust = ctx.db.get(Customer, m.customer_id) if m.customer_id else None
    if cust and cust.email and cust.email not in recips:
        recips.append(cust.email)
    if ctx.user.manager_id:
        mg = ctx.db.get(User, ctx.user.manager_id)
        if mg and mg.email not in recips:
            recips.append(mg.email)
        notify(ctx.db, ctx.tid, ctx.user.manager_id, "Meeting completed", summary[:200], "mom")
    actions = "\n".join(f"- {a.title}" for a in body.action_items) or "- none"
    for r in recips:
        send_email(r, f"Minutes of Meeting - {m.title}", f"Meeting: {m.title}\nWith: {cust.name if cust else '-'}\nBy: {ctx.user.name}\nOutcome: {body.outcome.replace('_', ' ')}\n\n{m.mom}\n\nAction items:\n{actions}\n\n- {ctx.tenant.name}")
    ctx.db.commit()
    return {**to_dict(m), "tasks_created": len(body.action_items)}


# ======================================================================= Related records
@router.get("/crm/{entity}/{rid}/related")
def related(entity: str, rid: int, ctx: Ctx = Depends(current)):
    if entity not in ("leads", "customers"):
        raise HTTPException(404, "Unknown module.")
    key = "lead_id" if entity == "leads" else "customer_id"
    get_or_404(ctx.db, Lead if entity == "leads" else Customer, rid, ctx.tid)
    tasks = ctx.db.scalars(select(Task).where(Task.tenant_id == ctx.tid, getattr(Task, key) == rid, Task.deleted_at.is_(None)).order_by(Task.id.desc()).limit(50)).all()
    fus = ctx.db.scalars(select(Followup).where(Followup.tenant_id == ctx.tid, getattr(Followup, key) == rid, Followup.deleted_at.is_(None)).order_by(Followup.due_at.desc()).limit(50)).all()
    ms = ctx.db.scalars(select(Meeting).where(Meeting.tenant_id == ctx.tid, getattr(Meeting, key) == rid, Meeting.deleted_at.is_(None)).order_by(Meeting.scheduled_at.desc()).limit(50)).all()
    qs = ctx.db.scalars(select(Quotation).where(Quotation.tenant_id == ctx.tid, getattr(Quotation, key) == rid, Quotation.deleted_at.is_(None)).order_by(Quotation.id.desc())).all()
    return {"tasks": [to_dict(t) for t in tasks], "followups": _fu_dict(ctx, fus), "meetings": _meeting_dicts(ctx, ms), "quotations": [to_dict(q) for q in qs]}
