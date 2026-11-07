"""CRM extensions: masters, stage & assignment configuration, lead pool, imports, events, renewals, notes with replies, global search."""
import csv
import io
import re
from datetime import date, datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, func, or_
from sqlalchemy.orm.attributes import flag_modified
from ..deps import Ctx, current, require
from ..models import (Lead, Customer, Contact, Product, Quotation, SalesOrder, Master, Event, Renewal, Comment, Followup, Activity, User, utcnow)
from ..common import to_dict, get_or_404, log_activity, local_today, decode_data_url, save_upload
from .. import svc
from ..notify import notify
from .crm import _visible

router = APIRouter(tags=["crm-ext"])


# ================================================================== Masters (pick lists)
MASTER_KINDS = list(svc.DEFAULT_MASTERS)


@router.get("/masters")
def list_masters(kind: str, ctx: Ctx = Depends(current)):
    if kind not in MASTER_KINDS:
        raise HTTPException(404, "Unknown list.")
    rows = ctx.db.scalars(select(Master).where(Master.tenant_id == ctx.tid, Master.kind == kind, Master.deleted_at.is_(None)).order_by(Master.sort, Master.id)).all()
    if rows:
        return [{"id": m.id, "name": m.name} for m in rows]
    return [{"id": None, "name": n} for n in svc.DEFAULT_MASTERS[kind]]  # built-in defaults until the admin customises the list


@router.post("/masters")
def add_master(body: dict, ctx: Ctx = Depends(require("admin"))):
    kind, name = body.get("kind"), str(body.get("name", "")).strip()
    if kind not in MASTER_KINDS or not name:
        raise HTTPException(422, "Choose a list and enter a name.")
    existing = ctx.db.scalars(select(Master).where(Master.tenant_id == ctx.tid, Master.kind == kind, Master.deleted_at.is_(None))).all()
    if not existing:  # first edit: materialise the defaults so they stay selectable
        for i, n in enumerate(svc.DEFAULT_MASTERS[kind]):
            ctx.db.add(Master(tenant_id=ctx.tid, kind=kind, name=n, sort=i))
        ctx.db.flush()
        existing = ctx.db.scalars(select(Master).where(Master.tenant_id == ctx.tid, Master.kind == kind, Master.deleted_at.is_(None))).all()
    if any(m.name.lower() == name.lower() for m in existing):
        raise HTTPException(409, "That name already exists.")
    m = Master(tenant_id=ctx.tid, kind=kind, name=name[:120], sort=len(existing))
    ctx.db.add(m)
    ctx.db.commit()
    return {"id": m.id, "name": m.name}


@router.delete("/masters/{mid}")
def del_master(mid: int, ctx: Ctx = Depends(require("admin"))):
    get_or_404(ctx.db, Master, mid, ctx.tid).deleted_at = utcnow()
    ctx.db.commit()
    return {"ok": True}


# ================================================================== Stage configuration
@router.get("/stages")
def get_stages(ctx: Ctx = Depends(current)):
    return svc.stages_of(ctx.tenant)


@router.put("/stages")
def put_stages(body: list[dict], ctx: Ctx = Depends(require("admin"))):
    """Ordered list of {key,label,kind(open|won|lost),gate(none|qualified|quoted),prob}. Stages in use can't be removed."""
    out, seen = [], set()
    for i, s in enumerate(body):
        key = re.sub(r"[^a-z0-9_]+", "_", str(s.get("key") or s.get("label", "")).lower()).strip("_")[:30]
        if not key or key in seen:
            raise HTTPException(422, "Every stage needs a unique name.")
        if s.get("kind") not in ("open", "won", "lost") or s.get("gate", "none") not in ("none", "qualified", "quoted"):
            raise HTTPException(422, f"Check the type and rule of '{s.get('label')}'.")
        seen.add(key)
        out.append({"key": key, "label": str(s.get("label") or key)[:40], "kind": s["kind"], "gate": s.get("gate", "none"), "prob": max(0, min(100, int(s.get("prob", 0))))})
    if not out or out[0]["kind"] != "open" or out[0]["gate"] != "none":
        raise HTTPException(422, "The first stage must be an open stage with no rule: new leads start there.")
    if sum(1 for s in out if s["kind"] == "won") != 1 or not any(s["kind"] == "lost" for s in out):
        raise HTTPException(422, "You need exactly one 'won' stage and at least one 'lost' stage.")
    used = dict(ctx.db.execute(select(Lead.status, func.count()).where(Lead.tenant_id == ctx.tid, Lead.deleted_at.is_(None)).group_by(Lead.status)).all())
    missing = [f"{k} ({n})" for k, n in used.items() if k not in seen]
    if missing:
        raise HTTPException(409, "These stages still have leads, so they can't be removed: " + ", ".join(missing))
    s = dict(ctx.tenant.settings or {})
    s["stages"] = out
    ctx.tenant.settings = s
    flag_modified(ctx.tenant, "settings")
    ctx.db.commit()
    return out


@router.get("/assignment")
def get_assignment(ctx: Ctx = Depends(require("manager"))):
    a = dict((ctx.tenant.settings or {}).get("assignment") or {})
    a.pop("rr", None)
    return {"mode": a.get("mode", "none"), "apply_manual": a.get("apply_manual", False), "default_user_ids": a.get("default_user_ids", []), "rules": a.get("rules", [])}


@router.put("/assignment")
def put_assignment(body: dict, ctx: Ctx = Depends(require("admin"))):
    if body.get("mode") not in ("none", "round_robin", "pool"):
        raise HTTPException(422, "Mode must be none, round_robin or pool.")
    valid = {u for (u,) in ctx.db.execute(select(User.id).where(User.tenant_id == ctx.tid))}
    clean = lambda ids: [i for i in (ids or []) if i in valid]
    rules = [{"match": {k: v for k, v in (r.get("match") or {}).items() if k in ("source", "region", "product_id", "country") and v not in (None, "")}, "user_ids": clean(r.get("user_ids"))} for r in (body.get("rules") or [])[:30]]
    s = dict(ctx.tenant.settings or {})
    old = (s.get("assignment") or {}).get("rr", {})
    s["assignment"] = {"mode": body["mode"], "apply_manual": bool(body.get("apply_manual")), "default_user_ids": clean(body.get("default_user_ids")), "rules": rules, "rr": old}
    ctx.tenant.settings = s
    flag_modified(ctx.tenant, "settings")
    ctx.db.commit()
    return get_assignment(ctx)


# ================================================================== Lead pool, ownership, star, observers
@router.get("/pool")
def pool(ctx: Ctx = Depends(current)):
    rows = ctx.db.scalars(select(Lead).where(Lead.tenant_id == ctx.tid, Lead.deleted_at.is_(None), Lead.in_pool.is_(True)).order_by(Lead.id.desc()).limit(300)).all()
    prods = {p.id: p.name for p in ctx.db.scalars(select(Product).where(Product.tenant_id == ctx.tid))}
    return [{**to_dict(l), "product_name": prods.get(l.product_id)} for l in rows]


@router.post("/crm/leads/{rid}/claim")
def claim(rid: int, ctx: Ctx = Depends(current)):
    lead = get_or_404(ctx.db, Lead, rid, ctx.tid)
    if not lead.in_pool:
        raise HTTPException(409, "This lead has already been taken.")
    lead.in_pool, lead.owner_id = False, ctx.user.id
    log_activity(ctx.db, ctx.tid, "lead", lead.id, ctx.user.id, f"{ctx.user.name} claimed this lead from the pool.")
    ctx.db.add(Followup(tenant_id=ctx.tid, lead_id=lead.id, owner_id=ctx.user.id, kind="call", due_at=utcnow() + timedelta(hours=2), notes="First contact (claimed from pool)"))
    ctx.db.commit()
    return to_dict(lead)


class AssignIn(BaseModel):
    owner_id: int | None = None  # null = back to the pool


@router.post("/crm/leads/{rid}/assign")
def assign(rid: int, body: AssignIn, ctx: Ctx = Depends(require("manager"))):
    lead = get_or_404(ctx.db, Lead, rid, ctx.tid)
    if body.owner_id is not None and body.owner_id not in ctx.team_ids():
        raise HTTPException(403, "You can only assign to people in your team.")
    lead.owner_id, lead.in_pool = body.owner_id, body.owner_id is None
    who = ctx.db.get(User, body.owner_id).name if body.owner_id else "the lead pool"
    log_activity(ctx.db, ctx.tid, "lead", lead.id, ctx.user.id, f"{ctx.user.name} assigned this lead to {who}.")
    if body.owner_id and body.owner_id != ctx.user.id:
        notify(ctx.db, ctx.tid, body.owner_id, "Lead assigned", f"{lead.name} was assigned to you.", "lead")
    ctx.db.commit()
    return to_dict(lead)


def auto_regret(db):
    """Workspaces can set auto_regret_days: open leads with no activity for that long move to a lost stage automatically."""
    from ..models import Tenant
    for t in db.scalars(select(Tenant).where(Tenant.active.is_(True))).all():
        days = int((t.settings or {}).get("auto_regret_days", 0) or 0)
        if days <= 0:
            continue
        stm = svc.stage_map(t)
        lost = next((s["key"] for s in svc.stages_of(t) if s["kind"] == "lost" and "regret" in s["key"]), None) or next((s["key"] for s in svc.stages_of(t) if s["kind"] == "lost"), None)
        open_keys = [k for k, s in stm.items() if s["kind"] == "open"]
        cutoff = utcnow() - timedelta(days=days)
        last = dict(db.execute(select(Activity.entity_id, func.max(Activity.created_at)).where(Activity.tenant_id == t.id, Activity.entity == "lead").group_by(Activity.entity_id)).all())
        for l in db.scalars(select(Lead).where(Lead.tenant_id == t.id, Lead.deleted_at.is_(None), Lead.status.in_(open_keys), Lead.created_at < cutoff)).all():
            if (last.get(l.id) or l.created_at) < cutoff:
                l.status, l.lost_reason, l.stage_changed_at = lost, f"Auto-regretted: no activity for {days} days", utcnow()
                log_activity(db, t.id, "lead", l.id, None, f"Auto-regretted after {days} days without activity.")
    db.commit()


# ================================================================== CSV import
ALIASES = {
    "leads": {"name": ["name", "contact", "contact person", "buyer", "customer name"], "company": ["company", "company name", "organisation", "organization"], "phone": ["phone", "mobile", "mobile no", "contact number", "number"],
              "email": ["email", "e-mail", "email id"], "source": ["source", "lead source"], "city": ["city"], "region": ["state", "region"], "country": ["country"], "value": ["value", "deal value", "amount"], "notes": ["notes", "requirement", "message", "remarks"],
              "product_text": ["product", "product name", "item"], "owner_email": ["owner", "owner email", "assigned to", "assigned email"], "temperature": ["temperature", "status"]},
    "customers": {"name": ["name", "company", "company name", "customer"], "phone": ["phone", "mobile"], "email": ["email"], "city": ["city"], "country": ["country"], "gstin": ["gstin", "gst", "tax id"], "address": ["address"], "industry": ["industry"], "credit_limit": ["credit limit"], "credit_days": ["credit days"]},
    "products": {"code": ["code", "item code", "sku"], "name": ["name", "product name", "item name"], "group": ["group"], "category": ["category"], "uom": ["unit", "uom"], "price": ["price", "selling price", "rate"], "hsn": ["hsn", "hsn/sac", "sac"], "gst_pct": ["gst", "gst%", "gst pct"], "description": ["description"], "moq": ["moq", "min order quantity"]},
    "renewals": {"customer": ["customer", "company"], "title": ["title", "renewal", "service"], "amount": ["amount", "renewal amount"], "renewal_date": ["date", "renewal date", "due date"], "notes": ["notes"]},
}


def _rows(raw: bytes):
    text = raw.decode("utf-8-sig", errors="replace")
    if len(text) > 8_000_000:
        raise HTTPException(413, "File too large (max 8 MB).")
    rd = csv.DictReader(io.StringIO(text))
    if not rd.fieldnames:
        raise HTTPException(422, "The file is empty.")
    return rd


@router.get("/import/{entity}/template")
def import_template(entity: str, ctx: Ctx = Depends(current)):
    if entity not in ALIASES:
        raise HTTPException(404, "Unknown import.")
    return PlainTextResponse(",".join(k for k in ALIASES[entity]) + "\n", media_type="text/csv")


@router.post("/import/{entity}")
async def import_csv(entity: str, dry_run: bool = False, on_duplicate: str = "repeat", file: UploadFile = File(...), ctx: Ctx = Depends(current)):
    """Imports leads (with duplicate handling), customers, products or renewals. dry_run validates without saving."""
    if entity not in ALIASES:
        raise HTTPException(404, "Unknown import.")
    if entity == "products" and not ctx.is_at_least("admin"):
        raise HTTPException(403, "Only admins can import products.")
    rd = _rows(await file.read())
    amap = {}
    for h in rd.fieldnames:
        for k, al in ALIASES[entity].items():
            if h.strip().lower() in al and k not in amap.values():
                amap[h] = k
    if not amap:
        raise HTTPException(422, "No column matched. Download the template to see the expected headers.")
    users = {u.email: u.id for u in ctx.db.scalars(select(User).where(User.tenant_id == ctx.tid))}
    res = {"created": 0, "repeat": 0, "skipped": 0, "updated": 0, "errors": [], "mapped": sorted(set(amap.values()))}
    for i, raw in enumerate(rd, start=2):
        if i > 5002:
            res["errors"].append("Stopped at 5,000 rows.")
            break
        row = {amap[h]: (v or "").strip() for h, v in raw.items() if h in amap}
        try:
            if entity == "leads":
                if row.get("owner_email"):
                    row["owner_id"] = users.get(row.pop("owner_email").lower())
                if row.get("temperature"):
                    row["temperature"] = row["temperature"].lower() if row["temperature"].lower() in ("hot", "warm", "cold") else "warm"
                if row.get("value"):
                    row["value"] = float(re.sub(r"[^\d.]", "", row["value"]) or 0)
                if dry_run:
                    if not svc.phone_key(row.get("phone", "")) and not row.get("email"):
                        raise HTTPException(422, "mobile or email required")
                    res["repeat" if svc.find_duplicate_lead(ctx.db, ctx.tid, row.get("phone", ""), row.get("email", "")) else "created"] += 1
                    continue
                with ctx.db.begin_nested():
                    _, st = svc.create_lead_core(ctx.db, ctx.tenant, ctx.user, row, "import", "repeat" if on_duplicate == "repeat" else "reject", ctx.user.name)
                res[st] += 1
            elif entity == "customers":
                if not row.get("name"):
                    raise HTTPException(422, "name required")
                pk = svc.phone_key(row.get("phone", ""))
                ex = ctx.db.scalar(select(Customer).where(Customer.tenant_id == ctx.tid, Customer.deleted_at.is_(None), or_(Customer.name.ilike(row["name"]), *( [Customer.phone_key == pk] if pk else []))))
                if ex:
                    res["skipped"] += 1
                    continue
                if not dry_run:
                    for k in ("credit_limit",):
                        row[k] = float(row[k]) if row.get(k) else 0
                    row["credit_days"] = int(row["credit_days"]) if row.get("credit_days") else 30
                    ctx.db.add(Customer(tenant_id=ctx.tid, owner_id=ctx.user.id, phone_key=pk, **row))
                res["created"] += 1
            elif entity == "products":
                if not row.get("name") or not row.get("code"):
                    raise HTTPException(422, "code and name required")
                for k in ("price", "gst_pct", "moq"):
                    if row.get(k):
                        row[k] = float(re.sub(r"[^\d.]", "", row[k]) or 0)
                    else:
                        row.pop(k, None)
                ex = ctx.db.scalar(select(Product).where(Product.tenant_id == ctx.tid, Product.code == row["code"], Product.deleted_at.is_(None)))
                if ex:
                    if not dry_run:
                        for k, v in row.items():
                            setattr(ex, k, v)
                    res["updated"] += 1
                else:
                    if not dry_run:
                        ctx.db.add(Product(tenant_id=ctx.tid, **row))
                    res["created"] += 1
            elif entity == "renewals":
                cust = ctx.db.scalar(select(Customer).where(Customer.tenant_id == ctx.tid, Customer.name.ilike(row.get("customer", "")), Customer.deleted_at.is_(None)))
                if not cust:
                    raise HTTPException(422, f"customer '{row.get('customer', '')}' not found")
                d = date.fromisoformat(row["renewal_date"][:10]) if row.get("renewal_date") else None
                if not d:
                    raise HTTPException(422, "renewal date (YYYY-MM-DD) required")
                if not dry_run:
                    ctx.db.add(Renewal(tenant_id=ctx.tid, customer_id=cust.id, title=row.get("title", ""), amount=float(row.get("amount") or 0), renewal_date=d, assignee_id=cust.owner_id or ctx.user.id, notes=row.get("notes", "")))
                res["created"] += 1
        except HTTPException as e:
            res["errors"].append(f"Row {i}: {e.detail}")
            res["skipped"] += 1
        except Exception as e:  # noqa
            res["errors"].append(f"Row {i}: {str(e)[:120]}")
            res["skipped"] += 1
    if not dry_run:
        ctx.db.commit()
    res["errors"] = res["errors"][:50]
    return res


# ================================================================== Product files
class FileIn(BaseModel):
    kind: str  # photo | brochure
    data: str


@router.post("/crm/products/{pid}/files")
def product_file(pid: int, body: FileIn, ctx: Ctx = Depends(require("admin"))):
    p = get_or_404(ctx.db, Product, pid, ctx.tid)
    if body.kind == "brochure":
        from ..common import decode_doc_url
        raw, ext = decode_doc_url(body.data)
        if ext != "pdf":
            raise HTTPException(422, "The brochure must be a PDF.")
        p.brochure = save_upload(ctx.tid, raw, "pdf")
    elif body.kind == "photo":
        if len(p.photos or []) >= 3:
            raise HTTPException(409, "A product can have up to 3 photos. Remove one first.")
        raw, ext = decode_data_url(body.data)
        p.photos = [*(p.photos or []), save_upload(ctx.tid, raw, ext)]
    else:
        raise HTTPException(422, "kind must be photo or brochure.")
    ctx.db.commit()
    return to_dict(p)


@router.delete("/crm/products/{pid}/photos/{idx}")
def del_photo(pid: int, idx: int, ctx: Ctx = Depends(require("admin"))):
    p = get_or_404(ctx.db, Product, pid, ctx.tid)
    ph = list(p.photos or [])
    if 0 <= idx < len(ph):
        ph.pop(idx)
        p.photos = ph
    ctx.db.commit()
    return to_dict(p)


# ================================================================== Events (trade fairs)
class EventIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    venue: str = ""
    stall_no: str = ""
    start_date: date
    end_date: date
    budget: float = 0
    owner_id: int | None = None
    notes: str = ""


@router.post("/events")
def add_event(body: EventIn, ctx: Ctx = Depends(require("manager"))):
    if body.end_date < body.start_date:
        raise HTTPException(422, "The end date is before the start date.")
    if body.owner_id:
        from ..refs import own
        own(ctx, User, body.owner_id, "Owner")
    e = Event(tenant_id=ctx.tid, **body.model_dump())
    ctx.db.add(e)
    ctx.db.commit()
    return to_dict(e)


@router.patch("/events/{eid}")
def edit_event(eid: int, body: dict, ctx: Ctx = Depends(require("manager"))):
    e = get_or_404(ctx.db, Event, eid, ctx.tid)
    for k in ("name", "venue", "stall_no", "budget", "owner_id", "notes"):
        if k in body:
            setattr(e, k, body[k])
    for k in ("start_date", "end_date"):
        if body.get(k):
            setattr(e, k, date.fromisoformat(body[k][:10]))
    ctx.db.commit()
    return to_dict(e)


@router.delete("/events/{eid}")
def del_event(eid: int, ctx: Ctx = Depends(require("manager"))):
    get_or_404(ctx.db, Event, eid, ctx.tid).deleted_at = utcnow()
    ctx.db.commit()
    return {"ok": True}


@router.get("/events")
def list_events(when: str = "all", ctx: Ctx = Depends(current)):
    today = local_today(ctx.tenant)
    q = select(Event).where(Event.tenant_id == ctx.tid, Event.deleted_at.is_(None))
    if when == "ongoing":
        q = q.where(Event.start_date <= today, Event.end_date >= today)
    elif when == "upcoming":
        q = q.where(Event.start_date > today)
    elif when == "past":
        q = q.where(Event.end_date < today)
    kinds = {s["key"]: s["kind"] for s in svc.stages_of(ctx.tenant)}
    out = []
    for e in ctx.db.scalars(q.order_by(Event.start_date.desc())):
        ls = ctx.db.scalars(select(Lead).where(Lead.tenant_id == ctx.tid, Lead.event_id == e.id, Lead.deleted_at.is_(None))).all()
        won = [l for l in ls if kinds.get(l.status) == "won"]
        rev = sum(l.value or 0 for l in won)
        out.append({**to_dict(e), "leads": len(ls), "won": len(won), "revenue": rev, "pipeline": sum(l.value or 0 for l in ls if kinds.get(l.status) == "open"), "cost_per_lead": round(e.budget / len(ls)) if e.budget and ls else None,
                    "roi": round((rev - e.budget) / e.budget * 100) if e.budget else None})
    return out


# ================================================================== Renewals / re-order reminders
class RenewalIn(BaseModel):
    customer_id: int
    product_id: int | None = None
    title: str = ""
    amount: float = 0
    renewal_date: date
    assignee_id: int | None = None
    notes: str = ""


@router.post("/renewals")
def add_renewal(body: RenewalIn, ctx: Ctx = Depends(current)):
    c = get_or_404(ctx.db, Customer, body.customer_id, ctx.tid)
    from ..refs import own
    if body.product_id:
        own(ctx, Product, body.product_id, "Product")
    if body.assignee_id:
        own(ctx, User, body.assignee_id, "Assignee")
    r = Renewal(tenant_id=ctx.tid, **body.model_dump())
    r.assignee_id = body.assignee_id or c.owner_id or ctx.user.id
    ctx.db.add(r)
    ctx.db.commit()
    return to_dict(r)


@router.get("/renewals")
def list_renewals(status: str | None = None, scope: str = "team", ctx: Ctx = Depends(current)):
    ids = [ctx.user.id] if scope == "mine" else ctx.team_ids()
    q = select(Renewal, Customer.name).join(Customer, Customer.id == Renewal.customer_id).where(Renewal.tenant_id == ctx.tid, Renewal.deleted_at.is_(None), (Renewal.assignee_id.in_(ids)) | (Renewal.assignee_id.is_(None)))
    if status:
        q = q.where(Renewal.status == status)
    today = local_today(ctx.tenant)
    return [{**to_dict(r), "customer_name": n, "days_left": (r.renewal_date - today).days} for r, n in ctx.db.execute(q.order_by(Renewal.renewal_date).limit(500))]


@router.post("/renewals/{rid}/status")
def renewal_status(rid: int, body: dict, ctx: Ctx = Depends(current)):
    r = get_or_404(ctx.db, Renewal, rid, ctx.tid)
    if body.get("status") not in ("due", "contacted", "renewed", "lost"):
        raise HTTPException(422, "Status must be due, contacted, renewed or lost.")
    r.status = body["status"]
    if body.get("notes"):
        r.notes = str(body["notes"])[:300]
    if r.status == "renewed" and body.get("next_date"):  # roll forward
        ctx.db.add(Renewal(tenant_id=ctx.tid, customer_id=r.customer_id, product_id=r.product_id, title=r.title, amount=r.amount, renewal_date=date.fromisoformat(body["next_date"][:10]), assignee_id=r.assignee_id))
    ctx.db.commit()
    return to_dict(r)


@router.post("/renewals/{rid}/to-lead")
def renewal_to_lead(rid: int, ctx: Ctx = Depends(current)):
    r = get_or_404(ctx.db, Renewal, rid, ctx.tid)
    if r.lead_id:
        raise HTTPException(409, "A lead already exists for this renewal.")
    c = ctx.db.get(Customer, r.customer_id)
    lead, _ = svc.create_lead_core(ctx.db, ctx.tenant, ctx.user, {"name": c.name, "company": c.name, "phone": c.phone, "email": c.email, "city": c.city, "value": r.amount, "owner_id": r.assignee_id or ctx.user.id,
                                                                  "product_id": r.product_id, "source": "Existing customer", "notes": f"Renewal: {r.title}", "temperature": "hot"}, "renewal", "create", ctx.user.name)
    r.lead_id, r.status = lead.id, "contacted"
    ctx.db.commit()
    return to_dict(lead)


def renewal_reminders(db):
    from ..models import Tenant
    for t in db.scalars(select(Tenant).where(Tenant.active.is_(True))).all():
        today = local_today(t)
        for r in db.scalars(select(Renewal).where(Renewal.tenant_id == t.id, Renewal.deleted_at.is_(None), Renewal.status == "due", Renewal.renewal_date <= today + timedelta(days=30))):
            left = (r.renewal_date - today).days
            if left in (30, 15, 7, 3, 1, 0) or (left < 0 and left % 7 == 0):
                if r.last_reminded != today and r.assignee_id:
                    c = db.get(Customer, r.customer_id)
                    notify(db, t.id, r.assignee_id, "Renewal due" if left >= 0 else "Renewal overdue", f"{c.name}: {r.title or 'renewal'} {'in ' + str(left) + ' day(s)' if left >= 0 else str(-left) + ' day(s) overdue'}", "renewal")
                    r.last_reminded = today
    db.commit()


# ================================================================== Notes with replies
class NoteIn(BaseModel):
    entity: str
    entity_id: int
    text: str = Field(min_length=1, max_length=4000)
    parent_id: int | None = None
    mentions: list[int] = []


@router.post("/notes")
def add_note(body: NoteIn, ctx: Ctx = Depends(current)):
    if body.entity not in ("lead", "customer"):
        raise HTTPException(422, "Notes can be added to leads and customers.")
    model = Lead if body.entity == "lead" else Customer
    rec = get_or_404(ctx.db, model, body.entity_id, ctx.tid)
    if body.parent_id:
        par = get_or_404(ctx.db, Comment, body.parent_id, ctx.tid)
        if par.entity != body.entity or par.entity_id != body.entity_id or par.parent_id:
            raise HTTPException(422, "Reply to a top-level note on the same record.")
    valid = {u for (u,) in ctx.db.execute(select(User.id).where(User.tenant_id == ctx.tid, User.id.in_(body.mentions or [0])))}
    c = Comment(tenant_id=ctx.tid, entity=body.entity, entity_id=body.entity_id, parent_id=body.parent_id, user_id=ctx.user.id, text=body.text.strip(), mentions=sorted(valid), read_by=[ctx.user.id])
    ctx.db.add(c)
    people = set(valid) | ({rec.owner_id} if getattr(rec, "owner_id", None) else set())
    if body.parent_id:
        people.add(ctx.db.get(Comment, body.parent_id).user_id)
    for uid in people - {ctx.user.id}:
        notify(ctx.db, ctx.tid, uid, "New note" if not body.parent_id else "New reply", f"{ctx.user.name} on {body.entity} '{getattr(rec, 'name', '')}': {body.text[:100]}", "note")
    ctx.db.commit()
    return to_dict(c)


def _thread(ctx: Ctx, rows: list[Comment]):
    users = {u.id: u.name for u in ctx.db.scalars(select(User).where(User.tenant_id == ctx.tid))}
    return [{**to_dict(c), "user_name": users.get(c.user_id), "unread": ctx.user.id not in (c.read_by or [])} for c in rows]


@router.get("/notes")
def list_notes(entity: str | None = None, entity_id: int | None = None, unread: bool = False, mine: bool = True, ctx: Ctx = Depends(current)):
    """Top-level notes with replies. Without a record: notes on records you own, notes you wrote, or where you're mentioned."""
    q = select(Comment).where(Comment.tenant_id == ctx.tid, Comment.deleted_at.is_(None))
    if entity and entity_id:
        q = q.where(Comment.entity == entity, Comment.entity_id == entity_id)
    allc = ctx.db.scalars(q.order_by(Comment.id.desc()).limit(1500)).all()
    if not (entity and entity_id) and mine and not ctx.is_at_least("admin"):  # notes you wrote, were mentioned in, or on records in your reporting line
        lead_ids = {i for (i,) in ctx.db.execute(select(Lead.id).where(Lead.tenant_id == ctx.tid, Lead.owner_id.in_(ctx.team_ids())))}
        cust_ids = {i for (i,) in ctx.db.execute(select(Customer.id).where(Customer.tenant_id == ctx.tid, Customer.owner_id.in_(ctx.team_ids())))}
        allc = [c for c in allc if c.user_id == ctx.user.id or ctx.user.id in (c.mentions or []) or (c.entity == "lead" and c.entity_id in lead_ids) or (c.entity == "customer" and c.entity_id in cust_ids)]
    tops = [c for c in allc if not c.parent_id]
    replies = {}
    for c in allc:
        if c.parent_id:
            replies.setdefault(c.parent_id, []).append(c)
    ln = {l.id: l.name for l in ctx.db.scalars(select(Lead).where(Lead.tenant_id == ctx.tid, Lead.id.in_({c.entity_id for c in tops if c.entity == "lead"} or {0}), Lead.deleted_at.is_(None)))}
    cn = {c.id: c.name for c in ctx.db.scalars(select(Customer).where(Customer.tenant_id == ctx.tid, Customer.id.in_({c.entity_id for c in tops if c.entity == "customer"} or {0})))}
    out = []
    for t in tops:
        rs = sorted(replies.get(t.id, []), key=lambda x: x.id)
        has_unread = ctx.user.id not in (t.read_by or []) or any(ctx.user.id not in (r.read_by or []) for r in rs)
        if unread and not has_unread:
            continue
        out.append({**_thread(ctx, [t])[0], "record_name": (ln if t.entity == "lead" else cn).get(t.entity_id), "has_unread": has_unread, "replies": _thread(ctx, rs)})
    return out


@router.post("/notes/{nid}/read")
def mark_note_read(nid: int, ctx: Ctx = Depends(current)):
    top = get_or_404(ctx.db, Comment, nid, ctx.tid)
    for c in [top, *ctx.db.scalars(select(Comment).where(Comment.tenant_id == ctx.tid, Comment.parent_id == nid))]:
        if ctx.user.id not in (c.read_by or []):
            c.read_by = [*(c.read_by or []), ctx.user.id]
    ctx.db.commit()
    return {"ok": True}


# ================================================================== Global search
FIELDS = {"mobile": "phone", "phone": "phone", "email": "email", "name": "name", "company": "company", "city": "city", "address": "address", "country": "country", "state": "region", "title": "notes", "description": "notes", "id": "id"}


@router.get("/search")
def search(q: str = Query(min_length=2, max_length=80), scope: str = "all", field: str = "any", ctx: Ctx = Depends(current)):
    like = f"%{q.strip()}%"
    digits = svc.phone_key(q)
    out = {}

    def match(model, cols):
        conds = []
        if field != "any" and field in FIELDS:
            f = FIELDS[field]
            if f == "id":
                return [model.id == int(q)] if q.isdigit() and len(q) <= 9 else [model.id == -1]
            if f == "phone" and digits:
                return [model.phone_key == digits] if hasattr(model, "phone_key") else [model.phone.ilike(like)]
            return [getattr(model, f).ilike(like)] if hasattr(model, f) else [model.id == -1]
        for c in cols:
            if hasattr(model, c):
                conds.append(getattr(model, c).ilike(like))
        if digits and hasattr(model, "phone_key"):
            conds.append(model.phone_key == digits)
        if q.isdigit() and len(q) <= 9:  # ids are 32-bit: a long number is a phone number, not an id
            conds.append(model.id == int(q))
        return [or_(*conds)]

    if scope in ("all", "leads"):
        rows = ctx.db.scalars(_visible(ctx, Lead, True).where(*match(Lead, ["name", "company", "email", "phone", "city", "notes", "region"])).order_by(Lead.id.desc()).limit(8)).all()
        out["leads"] = [{"id": r.id, "title": r.name, "sub": f"{r.company} · {r.phone}", "status": r.status} for r in rows]
    if scope in ("all", "customers"):
        rows = ctx.db.scalars(_visible(ctx, Customer, True).where(*match(Customer, ["name", "email", "phone", "city", "address", "gstin"])).order_by(Customer.id.desc()).limit(8)).all()
        out["customers"] = [{"id": r.id, "title": r.name, "sub": f"{r.city} · {r.phone}"} for r in rows]
    if scope in ("all", "contacts"):
        rows = ctx.db.scalars(_visible(ctx, Contact, True).where(*match(Contact, ["name", "email", "phone"])).order_by(Contact.id.desc()).limit(8)).all()
        out["contacts"] = [{"id": r.id, "title": r.name, "sub": f"{r.designation} · {r.phone}"} for r in rows]
    if scope in ("all", "quotations"):
        rows = ctx.db.scalars(select(Quotation).where(Quotation.tenant_id == ctx.tid, Quotation.deleted_at.is_(None), Quotation.created_by.in_(ctx.team_ids()), or_(Quotation.number.ilike(like), Quotation.erp_name.ilike(like))).limit(8)).all()
        out["quotations"] = [{"id": r.id, "title": r.number, "sub": r.erp_name or r.status, "lead_id": r.lead_id} for r in rows]
    if scope in ("all", "orders"):
        rows = ctx.db.scalars(select(SalesOrder).where(SalesOrder.tenant_id == ctx.tid, SalesOrder.deleted_at.is_(None), SalesOrder.created_by.in_(ctx.team_ids()), or_(SalesOrder.number.ilike(like), SalesOrder.po_number.ilike(like), SalesOrder.erp_name.ilike(like))).limit(8)).all()
        out["orders"] = [{"id": r.id, "title": r.number, "sub": r.po_number or r.erp_name or r.status} for r in rows]
    return out
