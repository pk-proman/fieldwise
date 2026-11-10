import re
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select, or_, func
from ..deps import Ctx, current, require
from datetime import date, datetime, timedelta
from ..models import Lead, Customer, Contact, Product, Activity, CustomField, User, Quotation, Followup, utcnow
from ..common import to_dict, get_or_404, log_activity, csv_response_text
from .. import svc
from ..refs import own, own_all
from .. import config

router = APIRouter(prefix="/crm", tags=["crm"])

ENT = {
    "leads": dict(model=Lead, label="lead", owned=True, search=["name", "company", "email", "phone", "city"],
                  writable=["name", "company", "phone", "email", "source", "status", "value", "owner_id", "sales_person_id", "city", "country", "notes", "tags", "custom", "product_id", "expected_close", "lost_reason", "temperature", "starred", "observers", "products", "region", "event_id"]),
    "customers": dict(model=Customer, label="customer", owned=True, search=["name", "email", "phone", "city", "gstin"],
                      writable=["name", "industry", "phone", "email", "address", "city", "country", "gstin", "lat", "lng", "owner_id", "tags", "custom", "credit_limit", "credit_days", "parent_id", "business_type"]),
    "contacts": dict(model=Contact, label="contact", owned=True, search=["name", "email", "phone", "designation"],
                     writable=["name", "designation", "phone", "email", "customer_id", "owner_id", "tags", "custom"]),
    "products": dict(model=Product, label="product", owned=False, search=["name", "code", "group", "category", "hsn"],
                     writable=["code", "name", "group", "uom", "price", "category", "hsn", "gst_pct", "video_url", "description", "moq", "vendor_ids", "amc_charge", "disabled", "kind", "parent_id"]),
}
LEAD_STATUSES = [x["key"] for x in svc.DEFAULT_STAGES]  # defaults; each workspace can configure its own (svc.stages_of)
STAGE_PROB = {x["key"]: x["prob"] for x in svc.DEFAULT_STAGES}
STAGE_PTS = STAGE_PROB
BANT = ("budget", "authority", "need", "timeline")


def _cfg(entity):
    if entity not in ENT:
        raise HTTPException(404, "Unknown module.")
    return ENT[entity]


def _validate_custom(ctx: Ctx, entity_label: str, custom: dict):
    defs = ctx.db.scalars(select(CustomField).where(CustomField.tenant_id == ctx.tid, CustomField.entity == entity_label,
                                                    CustomField.deleted_at.is_(None))).all()
    for d in defs:
        v = (custom or {}).get(d.key)
        if d.required and (v is None or v == ""):
            raise HTTPException(422, f"'{d.label}' is required.")
        if v not in (None, "") and d.type == "number":
            try:
                float(v)
            except (TypeError, ValueError):
                raise HTTPException(422, f"'{d.label}' must be a number.")
        if v not in (None, "") and d.type == "select" and d.options and v not in d.options:
            raise HTTPException(422, f"'{d.label}' must be one of: {', '.join(d.options)}.")


def _visible(ctx: Ctx, model, owned: bool):
    q = select(model).where(model.tenant_id == ctx.tid, model.deleted_at.is_(None))
    if owned and not ctx.is_at_least("admin"):
        ids = ctx.team_ids()
        cond = or_(model.owner_id.in_(ids), model.owner_id.is_(None))
        if model is Lead:
            shared = [i for i, sw in ctx.db.execute(select(Lead.id, Lead.shared_with).where(Lead.tenant_id == ctx.tid)) if ctx.user.id in (sw or [])]
            if shared:
                cond = or_(cond, Lead.id.in_(shared))
        q = q.where(cond)
    return q


def _apply_filters(q, model, cfg, params: dict):
    s = params.pop("q", None)
    if s:
        like = f"%{s}%"
        q = q.where(or_(*[getattr(model, c).ilike(like) for c in cfg["search"]]))
    for k, v in params.items():
        if k in ("limit", "offset", "sort", "fmt", "tag"):
            continue
        if hasattr(model, k) and v != "":
            if k in ("disabled", "starred", "in_pool"):
                v = v in ("1", "true", "True")
            q = q.where(getattr(model, k) == (int(v) if k.endswith("_id") else v))
    return q



def _prep_lead(ctx: Ctx, data: dict, existing: Lead | None = None):
    """Validate the mandatory product (from the product master) and coerce dates."""
    if "product_id" in data or existing is None or existing.product_id is None:
        pid = data.get("product_id", existing.product_id if existing else None)
        pr = ctx.db.get(Product, int(pid)) if pid not in (None, "") else None
        if not pr or pr.tenant_id != ctx.tid or pr.deleted_at:
            raise HTTPException(422, "Product is required. Choose one from the product master.")
        data["product_id"] = pr.id
    if "expected_close" in data:
        v = data["expected_close"]
        try:
            data["expected_close"] = date.fromisoformat(v[:10]) if v else None
        except (TypeError, ValueError):
            raise HTTPException(422, "Expected close must be a date (YYYY-MM-DD).")


def _quote_count(ctx: Ctx, lead_id: int) -> int:
    return ctx.db.scalar(select(func.count(Quotation.id)).where(Quotation.tenant_id == ctx.tid, Quotation.lead_id == lead_id,
                                                                Quotation.status != "rejected", Quotation.deleted_at.is_(None))) or 0


def _gate(ctx: Ctx, lead: Lead, new: str, body: dict):
    """Stage rules from the workspace's stage configuration: gate 'qualified' needs the BANT checklist, 'quoted' also needs a quotation; lost stages need a reason."""
    if new == lead.status:
        return
    st = svc.stage_map(ctx.tenant).get(new)
    if not st:
        raise HTTPException(422, "Unknown lead stage.")
    if st["gate"] in ("qualified", "quoted") and not lead.qualified_at:
        raise HTTPException(422, "Qualify the lead first: open it and complete the qualification checklist.")
    if st["gate"] == "quoted" and _quote_count(ctx, lead.id) == 0:
        raise HTTPException(422, "Create a quotation for this lead before moving it to " + st["label"] + ".")
    if st["kind"] == "lost":
        reason = (body.get("lost_reason") or lead.lost_reason or "").strip()
        if not reason:
            raise HTTPException(422, "Tell us why the lead was lost (a reason is required).")
        lead.lost_reason = reason[:200]


def convert_lead_to_customer(ctx: Ctx, lead: Lead):
    cust = Customer(tenant_id=ctx.tid, name=lead.company or lead.name, phone=lead.phone, email=lead.email, city=lead.city,
                    country=lead.country, owner_id=lead.owner_id or ctx.user.id, tags=lead.tags or [], custom=lead.custom or {})
    ctx.db.add(cust)
    ctx.db.flush()
    contact = Contact(tenant_id=ctx.tid, name=lead.name, phone=lead.phone, email=lead.email, customer_id=cust.id, owner_id=cust.owner_id)
    ctx.db.add(contact)
    won = next((x["key"] for x in svc.stages_of(ctx.tenant) if x["kind"] == "won"), "won")
    lead.status, lead.converted_customer_id, lead.stage_changed_at = won, cust.id, utcnow()
    ctx.db.query(Quotation).filter(Quotation.tenant_id == ctx.tid, Quotation.lead_id == lead.id).update({"customer_id": cust.id})
    log_activity(ctx.db, ctx.tid, "lead", lead.id, ctx.user.id, f"{ctx.user.name} converted this lead to customer '{cust.name}'.")
    log_activity(ctx.db, ctx.tid, "customer", cust.id, ctx.user.id, f"Created from lead '{lead.name}'.")
    return cust, contact


def lead_insight(lead: Lead, last_activity, overdue_followups: int, has_pending_followup: bool, has_quote: bool) -> dict:
    """Transparent rule-based lead score (0-100) and next-best-action. Not a trained ML model."""
    q = lead.qualification or {}
    score = STAGE_PTS.get(lead.status, 10) * 0.6 + sum(int(q.get(k, 0)) for k in BANT) * 3
    score += 12 if (lead.value or 0) >= 1_000_000 else 6 if (lead.value or 0) >= 300_000 else 0
    age = (utcnow() - (last_activity or lead.created_at)).days
    score += 10 if age <= 3 else 4 if age <= 7 else -12 if age > 30 else 0
    score -= 8 * min(overdue_followups, 2)
    score = max(0, min(100, round(score)))
    if lead.status in ("won", "lost", "regretted") or lead.status not in STAGE_PTS:
        action = "Closed" if lead.status in ("won", "lost", "regretted") else "On track"
    elif overdue_followups:
        action = "Overdue follow-up: contact the customer today"
    elif lead.status in ("new", "contacted") and not lead.qualified_at:
        action = "Qualify this lead (budget, authority, need, timeline)"
    elif lead.status == "qualified" and not has_quote:
        action = "Send a quotation"
    elif lead.status in ("proposal", "negotiation") and not has_pending_followup:
        action = "Schedule a follow-up on the quotation"
    elif not has_pending_followup:
        action = "No follow-up planned: schedule one"
    else:
        action = "On track"
    return {"score": score, "score_band": "hot" if score >= 70 else "warm" if score >= 40 else "cold", "next_action": action}


def _enrich_leads(ctx: Ctx, rows: list, data: list):
    ids = [r.id for r in rows]
    if not ids:
        return
    last = dict(ctx.db.execute(select(Activity.entity_id, func.max(Activity.created_at)).where(
        Activity.tenant_id == ctx.tid, Activity.entity == "lead", Activity.entity_id.in_(ids)).group_by(Activity.entity_id)).all())
    fu = ctx.db.execute(select(Followup.lead_id, Followup.due_at).where(Followup.tenant_id == ctx.tid, Followup.lead_id.in_(ids),
                                                                       Followup.status == "pending", Followup.deleted_at.is_(None))).all()
    over, pend, now, nxt = {}, set(), utcnow(), {}
    for lid, due in fu:
        pend.add(lid)
        nxt[lid] = min(due, nxt.get(lid, due))
        if due < now:
            over[lid] = over.get(lid, 0) + 1
    quoted = {i for (i,) in ctx.db.execute(select(Quotation.lead_id).where(Quotation.tenant_id == ctx.tid, Quotation.lead_id.in_(ids), Quotation.deleted_at.is_(None)))}
    prods = {p.id: p.name for p in ctx.db.scalars(select(Product).where(Product.tenant_id == ctx.tid))}
    from ..models import CallLog, Meeting
    calls = dict(ctx.db.execute(select(CallLog.lead_id, func.count()).where(CallLog.tenant_id == ctx.tid, CallLog.lead_id.in_(ids)).group_by(CallLog.lead_id)).all())
    fcalls = dict(ctx.db.execute(select(Followup.lead_id, func.count()).where(Followup.tenant_id == ctx.tid, Followup.lead_id.in_(ids), Followup.status == "done", Followup.kind == "call").group_by(Followup.lead_id)).all())
    meets = dict(ctx.db.execute(select(Meeting.lead_id, func.count()).where(Meeting.tenant_id == ctx.tid, Meeting.lead_id.in_(ids), Meeting.status == "completed").group_by(Meeting.lead_id)).all())
    for r, d in zip(rows, data):
        d["call_attempts"], d["meeting_attempts"] = calls.get(r.id, 0) + fcalls.get(r.id, 0), meets.get(r.id, 0)
        d["next_followup"] = nxt[r.id].isoformat() + "Z" if r.id in nxt else None
        d.update(lead_insight(r, last.get(r.id), over.get(r.id, 0), r.id in pend, r.id in quoted))
        d["product_name"] = prods.get(r.product_id)
        d["probability"] = (svc.stage_map(ctx.tenant).get(r.status) or {}).get("prob", 0)

def _check_refs(ctx: Ctx, entity: str, d: dict, obj=None):
    """Every id in a lead / customer / contact / product payload must belong to this workspace."""
    from ..models import Event, Vendor, SalesPerson
    for k, M in (("owner_id", User), ("event_id", Event), ("customer_id", Customer), ("sales_person_id", SalesPerson)):
        if k in d and d[k] not in (None, ""):
            own(ctx, M, d[k], k.replace("_id", "").title())
    if d.get("parent_id") not in (None, ""):
        p = own(ctx, Customer, d["parent_id"], "Parent company")
        cur, seen = p, set()
        while cur is not None and cur.id not in seen:  # no loops in the company tree
            if obj is not None and cur.id == obj.id:
                raise HTTPException(422, "A company can't be its own parent.")
            seen.add(cur.id)
            cur = ctx.db.get(Customer, cur.parent_id) if cur.parent_id else None
    if d.get("observers"):
        own_all(ctx, User, d["observers"], "Observer")
    for x in d.get("products") or []:
        own(ctx, Product, x.get("product_id") if isinstance(x, dict) else x, "Product")
    if d.get("vendor_ids"):
        own_all(ctx, Vendor, d["vendor_ids"], "Vendor")


@router.get("/{entity}")
def list_records(entity: str, request: Request, ctx: Ctx = Depends(current)):
    cfg = _cfg(entity)
    model = cfg["model"]
    params = dict(request.query_params)
    limit = min(int(params.pop("limit", 50)), 500)
    offset = int(params.pop("offset", 0))
    sort = params.pop("sort", "-id")
    fmt = params.pop("fmt", "json")
    tag = params.pop("tag", None)
    base = _visible(ctx, model, cfg["owned"])
    if entity == "products" and "disabled" not in params:
        base = base.where(Product.disabled.is_(False))
    if entity == "leads" and "in_pool" not in params:
        base = base.where(Lead.in_pool.is_(False))
    q = _apply_filters(base, model, cfg, params)
    col = getattr(model, sort.lstrip("-"), model.id)
    q = q.order_by(col.desc() if sort.startswith("-") else col.asc())
    rows = ctx.db.scalars(q).all()
    if tag:
        rows = [r for r in rows if tag in (getattr(r, "tags", None) or [])]
    total = len(rows)
    page = rows[offset:offset + limit] if fmt == "json" else rows
    data = [to_dict(r) for r in page]
    if entity == "leads":
        _enrich_leads(ctx, page, data)
    if entity == "customers" and page:
        from ..models import SalesOrder
        cids = [c.id for c in page]
        lc = dict(ctx.db.execute(select(Lead.converted_customer_id, func.count()).where(Lead.tenant_id == ctx.tid, Lead.converted_customer_id.in_(cids)).group_by(Lead.converted_customer_id)).all())
        oc = dict(ctx.db.execute(select(SalesOrder.customer_id, func.count()).where(SalesOrder.tenant_id == ctx.tid, SalesOrder.customer_id.in_(cids), SalesOrder.status != "cancelled").group_by(SalesOrder.customer_id)).all())
        cc = dict(ctx.db.execute(select(Contact.customer_id, func.count()).where(Contact.tenant_id == ctx.tid, Contact.customer_id.in_(cids), Contact.deleted_at.is_(None)).group_by(Contact.customer_id)).all())
        for r, d in zip(page, data):
            d["leads_count"], d["orders_count"], d["contacts_count"] = lc.get(r.id, 0), oc.get(r.id, 0), cc.get(r.id, 0)
    if fmt == "csv":
        flat = [{k: (",".join(map(str, v)) if isinstance(v, list) else ("" if isinstance(v, dict) else v)) for k, v in d.items()} for d in data]
        return PlainTextResponse(csv_response_text(flat), media_type="text/csv")
    return {"total": total, "items": data}


@router.post("/{entity}")
def create_record(entity: str, body: dict, bg: BackgroundTasks, ctx: Ctx = Depends(current)):
    cfg = _cfg(entity)
    if entity == "products" and not ctx.is_at_least("admin"):
        raise HTTPException(403, "Only admins can manage the product catalog.")
    data = {k: v for k, v in body.items() if k in cfg["writable"]}
    _check_refs(ctx, entity, data)
    if entity == "leads":
        svc.sync_owner_fields(ctx.db, ctx.tid, data)
    if not data.get("name"):
        raise HTTPException(422, "Name is required.")
    if entity in ("leads", "customers", "contacts"):
        _validate_custom(ctx, cfg["label"], data.get("custom") or {})
        data.setdefault("owner_id", ctx.user.id)
    if entity == "leads":
        st0 = svc.stage_map(ctx.tenant).get(data.get("status", svc.entry_stage(ctx.tenant)))
        if not st0 or st0["kind"] != "open" or st0["gate"] != "none":
            raise HTTPException(422, "New leads start in an early stage. Qualify and quote to move them forward.")
        _prep_lead(ctx, data)
        if not data.get("phone") and not data.get("email"):
            raise HTTPException(422, "A mobile number or an email is required.")
        dup = svc.find_duplicate_lead(ctx.db, ctx.tid, data.get("phone", ""), data.get("email", ""))
        mode = body.get("on_duplicate", "reject")
        if dup and mode != "create":
            if mode == "repeat":
                lead, _ = svc.create_lead_core(ctx.db, ctx.tenant, ctx.user, {**data, "notes": data.get("notes", "")}, "manual", "repeat", ctx.user.name)
                ctx.db.commit()
                return {**to_dict(lead), "_repeat": True}
            raise HTTPException(409, f"Duplicate: lead #{dup.id} ({dup.name}) already has this mobile or email.")
        data["stage_changed_at"] = utcnow()
        data["phone_key"] = svc.phone_key(data.get("phone", ""))
        if "owner_id" not in body and (ctx.tenant.settings.get("assignment") or {}).get("apply_manual"):
            o, pool = svc.pick_owner(ctx.db, ctx.tenant, {**data})
            if o or pool:
                data["owner_id"], data["in_pool"] = o, pool
    if entity == "customers":
        data["phone_key"] = svc.phone_key(data.get("phone", ""))
    obj = cfg["model"](tenant_id=ctx.tid, **data)
    ctx.db.add(obj)
    ctx.db.flush()
    if cfg["owned"]:
        log_activity(ctx.db, ctx.tid, cfg["label"], obj.id, ctx.user.id, f"{ctx.user.name} created this {cfg['label']}.")
    if entity == "leads":
        from .. import qualifier
        qualifier.qualify_new_lead(ctx.db, ctx.tenant, obj)  # automation: score the lead and add the reasoning as a remark
    if entity == "leads":  # automation: first follow-up is scheduled automatically
        days = int(ctx.setting("auto_followup_days", 1) or 0)
        if days > 0:
            due = (utcnow() + timedelta(days=days)).replace(hour=5, minute=0, second=0, microsecond=0)
            ctx.db.add(Followup(tenant_id=ctx.tid, lead_id=obj.id, owner_id=obj.owner_id or ctx.user.id, kind="call", due_at=due, notes="Introductory follow-up (auto-scheduled)"))
    ctx.db.commit()
    _erp_push(bg, ctx, entity, obj.id)
    return to_dict(obj)


@router.get("/{entity}/{rid}")
def get_record(entity: str, rid: int, ctx: Ctx = Depends(current)):
    cfg = _cfg(entity)
    return to_dict(get_or_404(ctx.db, cfg["model"], rid, ctx.tid))


@router.patch("/{entity}/{rid}")
def update_record(entity: str, rid: int, body: dict, bg: BackgroundTasks, ctx: Ctx = Depends(current)):
    cfg = _cfg(entity)
    if entity == "products" and not ctx.is_at_least("admin"):
        raise HTTPException(403, "Only admins can manage the product catalog.")
    obj = get_or_404(ctx.db, cfg["model"], rid, ctx.tid)
    changes = []
    body = dict(body)
    _check_refs(ctx, entity, {k: v for k, v in body.items() if k in cfg["writable"]}, obj)
    if entity == "leads":
        if "sales_person_id" in body or "owner_id" in body:
            svc.sync_owner_fields(ctx.db, ctx.tid, body)
        if svc.stage_map(ctx.tenant).get(obj.status, {}).get("kind") == "won" and "status" in body and body["status"] != obj.status:
            raise HTTPException(422, "A won lead is already a customer and can't be moved back.")
        if body.get("temperature") not in (None, "hot", "warm", "cold"):
            raise HTTPException(422, "Temperature must be hot, warm or cold.")
        if "status" in body:
            if body["status"] not in svc.stage_map(ctx.tenant):
                raise HTTPException(422, "Unknown lead stage.")
            _gate(ctx, obj, body["status"], body)
            if body["status"] != obj.status:
                obj.stage_changed_at = utcnow()
        _prep_lead(ctx, body, obj)
    for k, v in body.items():
        if k not in cfg["writable"]:
            continue
        if k == "custom":
            _validate_custom(ctx, cfg["label"], {**(obj.custom or {}), **(v or {})})
            v = {**(obj.custom or {}), **(v or {})}
        if getattr(obj, k) != v:
            if k in ("status", "owner_id", "value", "name"):
                changes.append(f"{k}: {getattr(obj, k)} → {v}")
            setattr(obj, k, v)
    if changes and cfg["owned"]:
        log_activity(ctx.db, ctx.tid, cfg["label"], obj.id, ctx.user.id, f"{ctx.user.name} changed " + "; ".join(changes))
    if entity in ("leads", "customers") and "phone" in body:
        obj.phone_key = svc.phone_key(obj.phone)
    if entity == "leads" and svc.stage_map(ctx.tenant).get(obj.status, {}).get("kind") == "won" and not obj.converted_customer_id:
        convert_lead_to_customer(ctx, obj)
    ctx.db.commit()
    _erp_push(bg, ctx, entity, obj.id)
    return to_dict(obj)


@router.delete("/{entity}/{rid}")
def delete_record(entity: str, rid: int, ctx: Ctx = Depends(current)):
    cfg = _cfg(entity)
    obj = get_or_404(ctx.db, cfg["model"], rid, ctx.tid)
    obj.deleted_at = utcnow()  # recycle bin: restorable for 90 days
    ctx.db.commit()
    return {"ok": True, "recoverable_days": config.RECYCLE_DAYS}


class BulkIn(BaseModel):
    ids: list[int]
    action: str            # update | delete
    changes: dict = {}


@router.post("/{entity}/bulk")
def bulk(entity: str, body: BulkIn, ctx: Ctx = Depends(current)):
    cfg = _cfg(entity)
    if len(body.ids) > 500:
        raise HTTPException(413, "Select at most 500 records at a time.")
    n = 0
    for rid in body.ids:
        obj = ctx.db.get(cfg["model"], rid)
        if not obj or obj.tenant_id != ctx.tid or obj.deleted_at:
            continue
        if body.action == "delete":
            obj.deleted_at = utcnow()
        else:
            ch = {k: v for k, v in body.changes.items() if k in cfg["writable"] and k != "custom"}
            _check_refs(ctx, entity, ch, obj)
            if entity == "leads" and ("owner_id" in ch or "sales_person_id" in ch):
                svc.sync_owner_fields(ctx.db, ctx.tid, ch)
            for k, v in ch.items():
                setattr(obj, k, v)
        n += 1
    ctx.db.commit()
    return {"affected": n}


@router.get("/{entity}/{rid}/timeline")
def timeline(entity: str, rid: int, ctx: Ctx = Depends(current)):
    cfg = _cfg(entity)
    get_or_404(ctx.db, cfg["model"], rid, ctx.tid)
    rows = ctx.db.execute(select(Activity, User.name).outerjoin(User, User.id == Activity.user_id).where(
        Activity.tenant_id == ctx.tid, Activity.entity == cfg["label"], Activity.entity_id == rid).order_by(Activity.id.desc())).all()
    return [{**to_dict(a), "user_name": n} for a, n in rows]


class NoteIn(BaseModel):
    text: str


@router.post("/{entity}/{rid}/timeline")
def add_note(entity: str, rid: int, body: NoteIn, ctx: Ctx = Depends(current)):
    cfg = _cfg(entity)
    get_or_404(ctx.db, cfg["model"], rid, ctx.tid)
    log_activity(ctx.db, ctx.tid, cfg["label"], rid, ctx.user.id, body.text.strip()[:2000])
    ctx.db.commit()
    return {"ok": True}


class ShareIn(BaseModel):
    user_ids: list[int]


@router.post("/leads/{rid}/share")
def share_lead(rid: int, body: ShareIn, ctx: Ctx = Depends(current)):
    lead = get_or_404(ctx.db, Lead, rid, ctx.tid)
    valid = {u for (u,) in ctx.db.execute(select(User.id).where(User.tenant_id == ctx.tid, User.id.in_(body.user_ids)))}
    lead.shared_with = sorted(valid)
    log_activity(ctx.db, ctx.tid, "lead", rid, ctx.user.id, f"{ctx.user.name} shared this lead with {len(valid)} user(s).")
    ctx.db.commit()
    return {"shared_with": lead.shared_with}


@router.post("/leads/{rid}/convert")
def convert_lead(rid: int, bg: BackgroundTasks, ctx: Ctx = Depends(current)):
    lead = get_or_404(ctx.db, Lead, rid, ctx.tid)
    if lead.converted_customer_id:
        raise HTTPException(409, "This lead is already converted.")
    _gate(ctx, lead, next((x["key"] for x in svc.stages_of(ctx.tenant) if x["kind"] == "won"), "won"), {})
    cust, contact = convert_lead_to_customer(ctx, lead)
    ctx.db.commit()
    _erp_push(bg, ctx, "customers", cust.id)
    return {"customer": to_dict(cust), "contact": to_dict(contact)}


class QualifyIn(BaseModel):
    budget: int
    authority: int
    need: int
    timeline: int
    notes: str = ""
    expected_close: str | None = None


@router.post("/leads/{rid}/qualify")
def qualify_lead(rid: int, body: QualifyIn, ctx: Ctx = Depends(current)):
    """BANT checklist, each answer 0 (no) / 1 (partly) / 2 (yes). Qualified when the total reaches the workspace threshold."""
    lead = get_or_404(ctx.db, Lead, rid, ctx.tid)
    if svc.stage_map(ctx.tenant).get(lead.status, {}).get("kind") in ("won", "lost"):
        raise HTTPException(409, "This lead is closed.")
    vals = {k: getattr(body, k) for k in BANT}
    if any(v not in (0, 1, 2) for v in vals.values()):
        raise HTTPException(422, "Each answer must be No, Partly or Yes.")
    total, need = sum(vals.values()), int(ctx.setting("qualify_min_score", 5))
    lead.qualification = {**vals, "notes": body.notes[:500], "total": total, "by": ctx.user.name}
    if body.expected_close:
        lead.expected_close = date.fromisoformat(body.expected_close[:10])
    ok = total >= need
    if ok:
        lead.qualified_at = lead.qualified_at or utcnow()
        if svc.stage_map(ctx.tenant).get(lead.status, {}).get("gate") == "none" and svc.stage_map(ctx.tenant).get(lead.status, {}).get("kind") == "open":
            lead.status, lead.stage_changed_at = "qualified", utcnow()
    log_activity(ctx.db, ctx.tid, "lead", lead.id, ctx.user.id, f"{ctx.user.name} scored qualification {total}/8 ({'qualified' if ok else 'not yet qualified, needs ' + str(need)}).")
    ctx.db.commit()
    return {"qualified": ok, "score": total, "needed": need, "lead": to_dict(lead)}


# -------- Business card OCR: client runs OCR (Tesseract.js/Vision); server parses text into fields ---------
class OcrIn(BaseModel):
    text: str


@router.post("/ocr/parse-card")
def parse_card(body: OcrIn, ctx: Ctx = Depends(current)):
    text = body.text.replace("\r", "")
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    email = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", text)
    phones = re.findall(r"(?:\+?\d[\d\s().-]{7,}\d)", text)
    web = re.search(r"(?:https?://|www\.)\S+", text, re.I)
    titles = re.compile(r"manager|director|engineer|head|ceo|cto|cfo|founder|partner|executive|officer|sales|proprietor|owner|vp|president", re.I)
    company_hint = re.compile(r"pvt|ltd|llp|inc|llc|corp|industries|enterprises|solutions|technologies|group|co\.|fze|trading|infra", re.I)
    clean = [l for l in lines if not (email and email.group() in l) and not re.search(r"\d{6,}", l) and not (web and web.group() in l)]
    designation = next((l for l in clean if titles.search(l)), "")
    company = next((l for l in clean if company_hint.search(l)), "")
    name = next((l for l in clean if l not in (designation, company) and re.fullmatch(r"[A-Za-z][A-Za-z.' -]{2,40}", l)), "")
    return {"name": name, "designation": designation, "company": company, "email": email.group() if email else "",
            "phone": re.sub(r"\s+", " ", phones[0]).strip() if phones else "", "website": web.group() if web else ""}


def _erp_push(bg: BackgroundTasks, ctx: Ctx, entity: str, rid: int):
    cfg = (ctx.tenant.settings or {}).get("erpnext") or {}
    if cfg.get("enabled") and entity in ("leads", "customers"):
        from ..integrations.erpnext import push_entity_task
        bg.add_task(push_entity_task, ctx.tid, entity, rid)
