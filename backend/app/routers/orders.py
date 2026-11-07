"""Sales orders, invoices (outstanding) and payment collections."""
from datetime import date, datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from ..deps import Ctx, current, require
from ..models import SalesOrder, Invoice, Payment, Customer, Product, Quotation, Lead, User, utcnow
from ..common import decode_doc_url, to_dict, get_or_404, local_today, log_activity, decode_data_url, save_upload, watermark_text, csv_response_text
from ..geo import haversine_m
from ..notify import notify
from ..integrations import erpnext as erp
from .sales import _erp_client, _own
from fastapi.responses import PlainTextResponse

router = APIRouter(tags=["orders"])


def outstanding_of(ctx: Ctx, cid: int) -> float:
    return float(ctx.db.scalar(select(func.coalesce(func.sum(Invoice.outstanding), 0)).where(Invoice.tenant_id == ctx.tid, Invoice.customer_id == cid, Invoice.deleted_at.is_(None))) or 0)


def _visible_customer_ids(ctx: Ctx):
    if ctx.is_at_least("admin"):
        return None
    return [c for (c,) in ctx.db.execute(select(Customer.id).where(Customer.tenant_id == ctx.tid, (Customer.owner_id.in_(ctx.team_ids())) | (Customer.owner_id.is_(None))))]


# ================================================================= Orders
class OItem(BaseModel):
    product_id: int
    qty: float = Field(gt=0, le=1_000_000)
    rate: float | None = Field(None, ge=0)
    discount_pct: float = Field(0, ge=0, le=100)


class OrderIn(BaseModel):
    customer_id: int | None = None
    quotation_id: int | None = None
    items: list[OItem] = []
    delivery_date: date
    po_number: str = ""
    payment_terms: str = ""
    po_file: str = Field(min_length=20)  # scanned customer PO (JPG/PNG/PDF as data URL): mandatory
    notes: str = ""
    tax_pct: float = Field(18, ge=0, le=100)


def _try_push_order(ctx: Ctx, o: SalesOrder):
    client = _erp_client(ctx)
    if client:
        try:
            erp.push_sales_order(ctx.db, ctx.tenant, client, o)
        except Exception as e:
            o.erp_error = str(e)[:500]


@router.post("/orders")
def create_order(body: OrderIn, ctx: Ctx = Depends(current)):
    raw_po, po_ext = decode_doc_url(body.po_file)
    if body.delivery_date < date.today():
        raise HTTPException(422, "Delivery date can't be in the past.")
    q = None
    items, tax = [], body.tax_pct
    cid = body.customer_id
    if body.quotation_id:
        q = get_or_404(ctx.db, Quotation, body.quotation_id, ctx.tid)
        if q.status in ("pending_approval", "rejected"):
            raise HTTPException(422, "This quotation isn't approved yet.")
        if ctx.db.scalar(select(SalesOrder.id).where(SalesOrder.tenant_id == ctx.tid, SalesOrder.quotation_id == q.id, SalesOrder.status != "cancelled", SalesOrder.deleted_at.is_(None))):
            raise HTTPException(409, "An order already exists for this quotation.")
        cid = q.customer_id or (ctx.db.get(Lead, q.lead_id).converted_customer_id if q.lead_id else None)
        if not cid:
            raise HTTPException(422, "Convert the lead to a customer (mark it Won) before booking the order.")
        items = [{**i} for i in q.items]
        tax = q.tax_pct
    else:
        for it in body.items:
            p = ctx.db.get(Product, it.product_id)
            if not p or p.tenant_id != ctx.tid or p.deleted_at:
                raise HTTPException(422, "One of the products is not in the product master.")
            rate = it.rate if it.rate is not None else p.price
            items.append({"product_id": p.id, "code": p.code, "name": p.name, "uom": p.uom, "qty": it.qty, "rate": rate, "discount_pct": it.discount_pct, "amount": round(it.qty * rate * (1 - it.discount_pct / 100), 2)})
    if not cid:
        raise HTTPException(422, "Choose a customer.")
    cust = get_or_404(ctx.db, Customer, cid, ctx.tid)
    if not items:
        raise HTTPException(422, "Add at least one product.")
    sub = round(sum(i["amount"] for i in items), 2)
    total = round(sub * (1 + tax / 100), 2)
    n = (ctx.db.scalar(select(func.count(SalesOrder.id)).where(SalesOrder.tenant_id == ctx.tid)) or 0) + 1
    out = outstanding_of(ctx, cid)
    over = bool(cust.credit_limit and out + total > cust.credit_limit)
    note = f"Outstanding {out:,.0f} + order {total:,.0f} exceeds credit limit {cust.credit_limit:,.0f}." if over else ""
    needs = over and not ctx.is_at_least("manager")
    prior = ctx.db.scalar(select(func.count(SalesOrder.id)).where(SalesOrder.tenant_id == ctx.tid, SalesOrder.customer_id == cid, SalesOrder.status != "cancelled", SalesOrder.deleted_at.is_(None))) or 0
    o = SalesOrder(tenant_id=ctx.tid, number=f"SO-{n:04d}", payment_terms=body.payment_terms[:200], sales_type="repeat" if prior else "new", customer_id=cid, quotation_id=q.id if q else None, created_by=ctx.user.id, items=items, subtotal=sub, tax_pct=tax, total=total,
                   delivery_date=body.delivery_date, po_number=body.po_number[:60], po_file=save_upload(ctx.tid, raw_po, po_ext), notes=body.notes, status="pending_approval" if needs else "confirmed", credit_note=note)
    ctx.db.add(o)
    ctx.db.flush()
    if needs:
        if ctx.user.manager_id:
            notify(ctx.db, ctx.tid, ctx.user.manager_id, "Order needs credit approval", f"{ctx.user.name}: {o.number} for {cust.name}. {note}", "approval")
    else:
        _try_push_order(ctx, o)
    if q:
        q.status = "accepted"
    log_activity(ctx.db, ctx.tid, "customer", cid, ctx.user.id, f"{ctx.user.name} booked order {o.number} for {total:,.0f}" + (f" (ERPNext {o.erp_name})" if o.erp_name else "") + ".")
    ctx.db.commit()
    return to_dict(o)


@router.get("/orders")
def list_orders(scope: str = "mine", status: str | None = None, customer_id: int | None = None, ctx: Ctx = Depends(current)):
    ids = [ctx.user.id] if scope == "mine" else ctx.team_ids()
    q = select(SalesOrder, Customer.name).join(Customer, Customer.id == SalesOrder.customer_id).where(SalesOrder.tenant_id == ctx.tid, SalesOrder.deleted_at.is_(None), SalesOrder.created_by.in_(ids))
    if status:
        q = q.where(SalesOrder.status == status)
    if customer_id:
        q = q.where(SalesOrder.customer_id == customer_id)
    rows = ctx.db.execute(q.order_by(SalesOrder.id.desc()).limit(300)).all()
    from ..models import Invoice
    inv = {}
    for oid, tot, out in ctx.db.execute(select(Invoice.order_id, func.coalesce(func.sum(Invoice.total), 0), func.coalesce(func.sum(Invoice.outstanding), 0)).where(
            Invoice.tenant_id == ctx.tid, Invoice.kind == "tax", Invoice.deleted_at.is_(None), Invoice.order_id.in_([o.id for o, _ in rows] or [0])).group_by(Invoice.order_id)):
        inv[oid] = (tot, out)
    return [{**to_dict(o), "customer_name": n, "invoiced": inv.get(o.id, (0, 0))[0], "payment_received": round(inv.get(o.id, (0, 0))[0] - inv.get(o.id, (0, 0))[1], 2), "balance_payment": inv.get(o.id, (0, o.total))[1] if o.id in inv else o.total,
             "invoice_count": 1 if o.id in inv else 0} for o, n in rows]


def _o(ctx, oid):
    o = get_or_404(ctx.db, SalesOrder, oid, ctx.tid)
    _own(ctx, o.created_by)
    return o


@router.post("/orders/{oid}/approve")
def approve_order(oid: int, ctx: Ctx = Depends(require("manager"))):
    o = _o(ctx, oid)
    if o.status != "pending_approval":
        raise HTTPException(409, "This order isn't waiting for approval.")
    if o.created_by == ctx.user.id:
        raise HTTPException(403, "You can't approve your own credit exception.")
    o.status, o.approver_id = "confirmed", ctx.user.id
    _try_push_order(ctx, o)
    notify(ctx.db, ctx.tid, o.created_by, "Order approved", f"{o.number} was approved.", "approval")
    ctx.db.commit()
    return to_dict(o)


@router.post("/orders/{oid}/push")
def push_order(oid: int, ctx: Ctx = Depends(current)):
    o = _o(ctx, oid)
    if o.status != "confirmed" or (o.erp_name and o.po_attached):
        raise HTTPException(409, "Only confirmed orders not yet fully in ERPNext can be pushed.")
    client = _erp_client(ctx)
    if not client:
        raise HTTPException(422, "ERPNext integration is not enabled.")
    try:
        if o.erp_name:
            erp.attach_po(ctx.db, ctx.tenant, client, o)  # retry only the attachment
        else:
            erp.push_sales_order(ctx.db, ctx.tenant, client, o)
    except Exception as e:
        o.erp_error = str(e)[:500]
        ctx.db.commit()
        raise HTTPException(502, f"ERPNext said: {e}")
    ctx.db.commit()
    return to_dict(o)


class OStatus(BaseModel):
    status: str


@router.post("/orders/{oid}/status")
def order_status(oid: int, body: OStatus, ctx: Ctx = Depends(current)):
    o = _o(ctx, oid)
    if body.status not in ("delivered", "cancelled") or o.status not in ("confirmed", "pending_approval"):
        raise HTTPException(422, "Only open orders can be marked delivered or cancelled.")
    if body.status == "cancelled" and o.erp_name:
        raise HTTPException(409, f"This order is in ERPNext as {o.erp_name}. Cancel it there first.")
    o.status = body.status
    ctx.db.commit()
    return to_dict(o)


@router.get("/products/{pid}/stock")
def stock(pid: int, ctx: Ctx = Depends(current)):
    p = get_or_404(ctx.db, Product, pid, ctx.tid)
    client = _erp_client(ctx)
    if not client:
        raise HTTPException(422, "Live stock needs the ERPNext integration. Ask an admin to enable it.")
    try:
        rows = client.list("Bin", ["warehouse", "actual_qty", "reserved_qty", "projected_qty"], [["item_code", "=", p.erp_name or p.code]])
    except Exception as e:
        raise HTTPException(502, f"ERPNext said: {e}")
    return {"item": p.code, "total": sum(r["actual_qty"] for r in rows), "available": sum(r["actual_qty"] - (r.get("reserved_qty") or 0) for r in rows), "warehouses": rows}


# ================================================================= Invoices & outstanding
class InvoiceIn(BaseModel):
    customer_id: int
    number: str = Field(min_length=1, max_length=60)
    invoice_date: date
    due_date: date | None = None
    total: float = Field(gt=0)


@router.post("/invoices")
def add_invoice(body: InvoiceIn, ctx: Ctx = Depends(require("admin"))):
    """Manual entry for workspaces without ERPNext; with ERPNext, invoices are pulled automatically."""
    c = get_or_404(ctx.db, Customer, body.customer_id, ctx.tid)
    if ctx.db.scalar(select(Invoice.id).where(Invoice.tenant_id == ctx.tid, Invoice.number == body.number, Invoice.deleted_at.is_(None))):
        raise HTTPException(409, "An invoice with this number already exists.")
    inv = Invoice(tenant_id=ctx.tid, number=body.number, customer_id=c.id, invoice_date=body.invoice_date, due_date=body.due_date or body.invoice_date + timedelta(days=c.credit_days or 30), total=body.total, outstanding=body.total)
    ctx.db.add(inv)
    ctx.db.commit()
    return to_dict(inv)


def _bucket(days_over: int) -> str:
    return "current" if days_over <= 0 else "d1_30" if days_over <= 30 else "d31_60" if days_over <= 60 else "d61_90" if days_over <= 90 else "d90p"


@router.get("/collections/outstanding")
def outstanding(customer_id: int | None = None, ctx: Ctx = Depends(current)):
    today = local_today(ctx.tenant)
    vis = _visible_customer_ids(ctx)
    q = select(Invoice, Customer).join(Customer, Customer.id == Invoice.customer_id).where(Invoice.tenant_id == ctx.tid, Invoice.deleted_at.is_(None), Invoice.outstanding > 0)
    if vis is not None:
        q = q.where(Invoice.customer_id.in_(vis or [0]))
    if customer_id:
        q = q.where(Invoice.customer_id == customer_id)
    custs: dict = {}
    for inv, c in ctx.db.execute(q.order_by(Invoice.due_date)):
        days = (today - inv.due_date).days
        e = custs.setdefault(c.id, {"customer_id": c.id, "name": c.name, "phone": c.phone, "credit_limit": c.credit_limit, "outstanding": 0.0, "overdue": 0.0, "aging": {"current": 0, "d1_30": 0, "d31_60": 0, "d61_90": 0, "d90p": 0}, "invoices": []})
        e["outstanding"] += inv.outstanding
        e["aging"][_bucket(days)] += inv.outstanding
        if days > 0:
            e["overdue"] += inv.outstanding
        e["invoices"].append({**to_dict(inv), "days_overdue": max(days, 0)})
    rows = sorted(custs.values(), key=lambda r: -r["overdue"])
    pend = ctx.db.scalars(select(Payment).where(Payment.tenant_id == ctx.tid, Payment.status == "pending", Payment.deleted_at.is_(None), Payment.user_id.in_(ctx.team_ids())))
    return {"total_outstanding": round(sum(r["outstanding"] for r in rows)), "total_overdue": round(sum(r["overdue"] for r in rows)), "pending_verification": sum(p.amount for p in pend), "customers": rows}


# ================================================================= Collections
class PayIn(BaseModel):
    customer_id: int
    invoice_id: int | None = None
    amount: float = Field(gt=0, le=100_000_000)
    tds_amount: float = Field(0, ge=0, le=100_000_000)
    mode: str
    reference: str = ""
    cheque_date: date | None = None
    note: str = ""
    photo: str | None = None
    lat: float | None = None
    lng: float | None = None


@router.post("/collections")
def collect(body: PayIn, ctx: Ctx = Depends(current)):
    if body.mode not in ("cash", "cheque", "upi", "bank"):
        raise HTTPException(422, "Payment mode must be cash, cheque, UPI or bank transfer.")
    if body.mode != "cash" and not body.reference.strip():
        raise HTTPException(422, {"cheque": "Enter the cheque number.", "upi": "Enter the UPI transaction ID.", "bank": "Enter the bank reference / UTR."}[body.mode])
    if body.mode == "cheque" and not body.cheque_date:
        raise HTTPException(422, "Enter the cheque date.")
    cust = get_or_404(ctx.db, Customer, body.customer_id, ctx.tid)
    inv = None
    if body.invoice_id:
        inv = get_or_404(ctx.db, Invoice, body.invoice_id, ctx.tid)
        if inv.customer_id != cust.id:
            raise HTTPException(422, "That invoice belongs to a different customer.")
        pending = ctx.db.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.tenant_id == ctx.tid, Payment.invoice_id == inv.id, Payment.status == "pending", Payment.deleted_at.is_(None))) or 0
        if body.amount + body.tds_amount + pending > inv.outstanding + 0.005:
            raise HTTPException(422, f"Only {inv.outstanding - pending:,.2f} is still due on {inv.number} (after payments awaiting verification).")
    else:
        if body.amount > outstanding_of(ctx, cust.id) + 0.005:
            raise HTTPException(422, "The amount is more than this customer's total outstanding. Record it against an invoice or reduce the amount.")
    if body.reference.strip() and ctx.db.scalar(select(Payment.id).where(Payment.tenant_id == ctx.tid, Payment.mode == body.mode, Payment.reference == body.reference.strip(), Payment.status != "rejected", Payment.deleted_at.is_(None))):
        raise HTTPException(409, "A payment with this reference is already recorded.")
    photo = None
    if body.photo:
        raw, ext = decode_data_url(body.photo)
        photo = save_upload(ctx.tid, raw, ext, watermark_text(ctx.user.name, body.lat, body.lng, ctx.tenant))
    n = (ctx.db.scalar(select(func.count(Payment.id)).where(Payment.tenant_id == ctx.tid)) or 0) + 1
    p = Payment(tenant_id=ctx.tid, receipt_no=f"RCT-{n:05d}", customer_id=cust.id, invoice_id=body.invoice_id, user_id=ctx.user.id, day=local_today(ctx.tenant), amount=round(body.amount, 2), mode=body.mode,
                reference=body.reference.strip(), cheque_date=body.cheque_date, tds_amount=round(body.tds_amount, 2), note=body.note[:300], photo=photo, lat=body.lat, lng=body.lng)
    ctx.db.add(p)
    if ctx.user.manager_id:
        notify(ctx.db, ctx.tid, ctx.user.manager_id, "Collection to verify", f"{ctx.user.name} collected {p.amount:,.0f} ({p.mode}) from {cust.name}.", "approval")
    log_activity(ctx.db, ctx.tid, "customer", cust.id, ctx.user.id, f"{ctx.user.name} collected {p.amount:,.0f} by {p.mode} (receipt {p.receipt_no}).")
    ctx.db.commit()
    return {**to_dict(p), "customer_name": cust.name}


@router.get("/collections")
def list_collections(scope: str = "mine", status: str | None = None, customer_id: int | None = None, fmt: str = "json", ctx: Ctx = Depends(current)):
    ids = [ctx.user.id] if scope == "mine" else ctx.team_ids()
    q = select(Payment, Customer.name, User.name, Invoice.number).join(Customer, Customer.id == Payment.customer_id).join(User, User.id == Payment.user_id).outerjoin(Invoice, Invoice.id == Payment.invoice_id).where(
        Payment.tenant_id == ctx.tid, Payment.deleted_at.is_(None), Payment.user_id.in_(ids))
    if status:
        q = q.where(Payment.status == status)
    if customer_id:
        q = q.where(Payment.customer_id == customer_id)
    rows = [{**to_dict(p), "customer_name": cn, "user_name": un, "invoice_number": inn} for p, cn, un, inn in ctx.db.execute(q.order_by(Payment.id.desc()).limit(500))]
    if fmt == "csv":
        return PlainTextResponse(csv_response_text([{k: r[k] for k in ("receipt_no", "day", "customer_name", "user_name", "invoice_number", "amount", "mode", "reference", "status", "erp_ref")} for r in rows]), media_type="text/csv")
    return rows


class VerifyIn(BaseModel):
    decision: str
    note: str = ""


@router.post("/collections/{pid}/verify")
def verify(pid: int, body: VerifyIn, ctx: Ctx = Depends(require("manager"))):
    """Manager/finance confirms the money was received (e.g. cash deposited, cheque cleared). Verification reduces the invoice and posts a Payment Entry to ERPNext."""
    if body.decision not in ("verified", "rejected"):
        raise HTTPException(422, "Decision must be verified or rejected.")
    p = get_or_404(ctx.db, Payment, pid, ctx.tid)
    _own(ctx, p.user_id)
    if p.user_id == ctx.user.id:
        raise HTTPException(403, "You can't verify your own collection.")
    if p.status != "pending":
        raise HTTPException(409, f"This payment is already {p.status}.")
    if body.decision == "rejected" and not body.note.strip():
        raise HTTPException(422, "Tell the collector why it was rejected.")
    p.status, p.verified_by, p.decision_note = body.decision, ctx.user.id, body.note[:300]
    if body.decision == "verified":
        left = p.amount + (p.tds_amount or 0)  # TDS deducted by the customer also settles the invoice
        invs = [ctx.db.get(Invoice, p.invoice_id)] if p.invoice_id else list(ctx.db.scalars(select(Invoice).where(Invoice.tenant_id == ctx.tid, Invoice.customer_id == p.customer_id, Invoice.outstanding > 0, Invoice.deleted_at.is_(None)).order_by(Invoice.due_date)))
        for inv in invs:  # unallocated receipts settle the oldest invoices first
            if left <= 0:
                break
            take = min(left, inv.outstanding)
            inv.outstanding = round(inv.outstanding - take, 2)
            inv.status = "paid" if inv.outstanding <= 0.005 else "partly_paid"
            left -= take
        client = _erp_client(ctx)
        if client:
            try:
                erp.push_payment(ctx.db, ctx.tenant, client, p)
            except Exception as e:
                p.erp_error = str(e)[:500]
    notify(ctx.db, ctx.tid, p.user_id, f"Collection {body.decision}", f"{p.receipt_no}: {p.amount:,.0f}. {body.note}", "approval")
    ctx.db.commit()
    return to_dict(p)


@router.post("/collections/{pid}/push")
def push_payment(pid: int, ctx: Ctx = Depends(require("manager"))):
    p = get_or_404(ctx.db, Payment, pid, ctx.tid)
    if p.status != "verified" or p.erp_ref:
        raise HTTPException(409, "Only verified payments not yet in ERPNext can be pushed.")
    client = _erp_client(ctx)
    if not client:
        raise HTTPException(422, "ERPNext integration is not enabled.")
    try:
        erp.push_payment(ctx.db, ctx.tenant, client, p)
    except Exception as e:
        p.erp_error = str(e)[:500]
        ctx.db.commit()
        raise HTTPException(502, f"ERPNext said: {e}")
    ctx.db.commit()
    return to_dict(p)


@router.get("/collections/{pid}/receipt")
def receipt(pid: int, ctx: Ctx = Depends(current)):
    p = get_or_404(ctx.db, Payment, pid, ctx.tid)
    _own(ctx, p.user_id)
    c, u = ctx.db.get(Customer, p.customer_id), ctx.db.get(User, p.user_id)
    inv = ctx.db.get(Invoice, p.invoice_id) if p.invoice_id else None
    return {**to_dict(p), "customer_name": c.name, "collector": u.name, "invoice_number": inv.number if inv else None, "company": ctx.tenant.name, "currency": ctx.setting("currency", "INR")}
