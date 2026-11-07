"""Vendors, vendor purchase orders, dealers and helpdesk tickets."""
from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from ..deps import Ctx, current, require
from ..models import Vendor, PurchaseOrder, Dealer, Ticket, TicketComment, User, utcnow
from ..common import to_dict, get_or_404
from ..notify import notify

router = APIRouter(tags=["purchase"])


# ------------------------------------------------------------------ vendors
VENDOR_KEYS = ("name", "contact_person", "phone", "email", "gstin", "city", "country", "status")


@router.get("/vendors")
def vendors(q: str = "", status: str = "", ctx: Ctx = Depends(current)):
    qq = select(Vendor).where(Vendor.tenant_id == ctx.tid, Vendor.deleted_at.is_(None))
    if q:
        qq = qq.where(Vendor.name.ilike(f"%{q}%"))
    if status:
        qq = qq.where(Vendor.status == status)
    pc = dict(ctx.db.execute(select(PurchaseOrder.vendor_id, func.count()).where(PurchaseOrder.tenant_id == ctx.tid, PurchaseOrder.status != "cancelled").group_by(PurchaseOrder.vendor_id)).all())
    return [{**to_dict(v), "po_count": pc.get(v.id, 0)} for v in ctx.db.scalars(qq.order_by(Vendor.name))]


@router.post("/vendors")
def add_vendor(body: dict, ctx: Ctx = Depends(require("manager"))):
    if not str(body.get("name", "")).strip():
        raise HTTPException(422, "Vendor name is required.")
    v = Vendor(tenant_id=ctx.tid, **{k: body[k] for k in VENDOR_KEYS if k in body})
    ctx.db.add(v)
    ctx.db.commit()
    return to_dict(v)


@router.patch("/vendors/{vid}")
def edit_vendor(vid: int, body: dict, ctx: Ctx = Depends(require("manager"))):
    v = get_or_404(ctx.db, Vendor, vid, ctx.tid)
    for k in VENDOR_KEYS:
        if k in body:
            setattr(v, k, body[k])
    ctx.db.commit()
    return to_dict(v)


@router.delete("/vendors/{vid}")
def del_vendor(vid: int, ctx: Ctx = Depends(require("manager"))):
    get_or_404(ctx.db, Vendor, vid, ctx.tid).deleted_at = utcnow()
    ctx.db.commit()
    return {"ok": True}


class POItem(BaseModel):
    description: str = Field(min_length=1, max_length=300)
    product_id: int | None = None
    qty: float = Field(gt=0)
    rate: float = Field(ge=0)


class POIn(BaseModel):
    vendor_id: int
    items: list[POItem] = Field(min_length=1, max_length=60)
    expected_date: date | None = None
    notes: str = ""


@router.post("/purchase-orders")
def add_po(body: POIn, ctx: Ctx = Depends(require("manager"))):
    get_or_404(ctx.db, Vendor, body.vendor_id, ctx.tid)
    n = (ctx.db.scalar(select(func.count(PurchaseOrder.id)).where(PurchaseOrder.tenant_id == ctx.tid)) or 0) + 1
    items = [{**i.model_dump(), "amount": round(i.qty * i.rate, 2)} for i in body.items]
    po = PurchaseOrder(tenant_id=ctx.tid, number=f"PO-{n:04d}", vendor_id=body.vendor_id, created_by=ctx.user.id, items=items, total=round(sum(i["amount"] for i in items), 2), expected_date=body.expected_date, notes=body.notes)
    ctx.db.add(po)
    ctx.db.commit()
    return to_dict(po)


@router.get("/purchase-orders")
def list_pos(vendor_id: int | None = None, status: str | None = None, ctx: Ctx = Depends(require("manager"))):
    q = select(PurchaseOrder, Vendor.name).join(Vendor, Vendor.id == PurchaseOrder.vendor_id).where(PurchaseOrder.tenant_id == ctx.tid, PurchaseOrder.deleted_at.is_(None))
    if vendor_id:
        q = q.where(PurchaseOrder.vendor_id == vendor_id)
    if status:
        q = q.where(PurchaseOrder.status == status)
    return [{**to_dict(p), "vendor_name": n} for p, n in ctx.db.execute(q.order_by(PurchaseOrder.id.desc()).limit(300))]


@router.post("/purchase-orders/{pid}/status")
def po_status(pid: int, body: dict, ctx: Ctx = Depends(require("manager"))):
    po = get_or_404(ctx.db, PurchaseOrder, pid, ctx.tid)
    allowed = {"draft": ["issued", "cancelled"], "issued": ["received", "cancelled"]}
    if body.get("status") not in allowed.get(po.status, []):
        raise HTTPException(409, f"A {po.status} order can't move to {body.get('status')}.")
    po.status = body["status"]
    ctx.db.commit()
    return to_dict(po)


# ------------------------------------------------------------------ dealers
DEALER_KEYS = ("name", "ceo_name", "contact_person", "phone", "email", "territory", "services")


@router.get("/dealers")
def dealers(ctx: Ctx = Depends(current)):
    return [to_dict(d) for d in ctx.db.scalars(select(Dealer).where(Dealer.tenant_id == ctx.tid, Dealer.deleted_at.is_(None)).order_by(Dealer.name))]


@router.post("/dealers")
def add_dealer(body: dict, ctx: Ctx = Depends(require("manager"))):
    if not str(body.get("name", "")).strip():
        raise HTTPException(422, "Dealer name is required.")
    d = Dealer(tenant_id=ctx.tid, **{k: body[k] for k in DEALER_KEYS if k in body})
    ctx.db.add(d)
    ctx.db.commit()
    return to_dict(d)


@router.patch("/dealers/{did}")
def edit_dealer(did: int, body: dict, ctx: Ctx = Depends(require("manager"))):
    d = get_or_404(ctx.db, Dealer, did, ctx.tid)
    for k in DEALER_KEYS:
        if k in body:
            setattr(d, k, body[k])
    ctx.db.commit()
    return to_dict(d)


@router.delete("/dealers/{did}")
def del_dealer(did: int, ctx: Ctx = Depends(require("manager"))):
    get_or_404(ctx.db, Dealer, did, ctx.tid).deleted_at = utcnow()
    ctx.db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ helpdesk
class TicketIn(BaseModel):
    kind: str = "General"
    subject: str = Field(min_length=3, max_length=200)
    description: str = ""
    priority: str = "medium"


@router.post("/tickets")
def raise_ticket(body: TicketIn, ctx: Ctx = Depends(current)):
    n = (ctx.db.scalar(select(func.count(Ticket.id)).where(Ticket.tenant_id == ctx.tid)) or 0) + 1
    t = Ticket(tenant_id=ctx.tid, number=f"TKT-{n:04d}", kind=body.kind[:60], subject=body.subject, description=body.description, raised_by=ctx.user.id, priority=body.priority if body.priority in ("low", "medium", "high") else "medium")
    ctx.db.add(t)
    for (aid,) in ctx.db.execute(select(User.id).where(User.tenant_id == ctx.tid, User.role.in_(["owner", "admin"]), User.active.is_(True))):
        if aid != ctx.user.id:
            notify(ctx.db, ctx.tid, aid, "New helpdesk ticket", f"{t.number}: {t.subject} ({ctx.user.name})", "ticket")
    ctx.db.commit()
    return to_dict(t)


@router.get("/tickets")
def tickets(ctx: Ctx = Depends(current)):
    q = select(Ticket).where(Ticket.tenant_id == ctx.tid, Ticket.deleted_at.is_(None))
    if not ctx.is_at_least("admin"):
        q = q.where(Ticket.raised_by == ctx.user.id)
    users = {u.id: u.name for u in ctx.db.scalars(select(User).where(User.tenant_id == ctx.tid))}
    return [{**to_dict(t), "raised_by_name": users.get(t.raised_by), "assignee": users.get(t.assigned_to)} for t in ctx.db.scalars(q.order_by(Ticket.id.desc()).limit(300))]


def _tk(ctx: Ctx, tid: int) -> Ticket:
    t = get_or_404(ctx.db, Ticket, tid, ctx.tid)
    if t.raised_by != ctx.user.id and not ctx.is_at_least("admin"):
        raise HTTPException(404, "Ticket not found.")
    return t


@router.get("/tickets/{tid}")
def ticket(tid: int, ctx: Ctx = Depends(current)):
    t = _tk(ctx, tid)
    users = {u.id: u.name for u in ctx.db.scalars(select(User).where(User.tenant_id == ctx.tid))}
    return {**to_dict(t), "comments": [{**to_dict(c), "user_name": users.get(c.user_id)} for c in ctx.db.scalars(select(TicketComment).where(TicketComment.ticket_id == t.id).order_by(TicketComment.id))]}


@router.post("/tickets/{tid}/comment")
def comment(tid: int, body: dict, ctx: Ctx = Depends(current)):
    t = _tk(ctx, tid)
    text = str(body.get("text", "")).strip()
    if not text:
        raise HTTPException(422, "Write a comment.")
    ctx.db.add(TicketComment(tenant_id=ctx.tid, ticket_id=t.id, user_id=ctx.user.id, text=text[:3000]))
    other = t.raised_by if ctx.user.id != t.raised_by else t.assigned_to
    if other:
        notify(ctx.db, ctx.tid, other, f"Reply on {t.number}", text[:120], "ticket")
    ctx.db.commit()
    return {"ok": True}


@router.post("/tickets/{tid}/update")
def update_ticket(tid: int, body: dict, ctx: Ctx = Depends(require("admin"))):
    t = get_or_404(ctx.db, Ticket, tid, ctx.tid)
    if body.get("status") in ("open", "in_progress", "closed"):
        t.status = body["status"]
    if "assigned_to" in body:
        t.assigned_to = body["assigned_to"] or None
    if t.status == "closed":
        notify(ctx.db, ctx.tid, t.raised_by, f"{t.number} closed", t.subject, "ticket")
    ctx.db.commit()
    return to_dict(t)
