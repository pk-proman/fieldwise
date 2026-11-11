"""Company profile, numbering & terms, quotation PDF / sending, proforma & tax invoices, message templates."""
import hashlib
import hmac
import time
import urllib.parse
from datetime import date, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.orm.attributes import flag_modified
from ..db import SessionLocal
from ..deps import Ctx, current, require, _auth
from ..models import (Quotation, SalesOrder, Invoice, Customer, Lead, MessageTemplate, Product, User, Tenant, Payment, utcnow)
from ..common import to_dict, get_or_404, local_today, decode_data_url, decode_doc_url, save_upload, log_activity
from .. import svc, config, pdfdoc
from ..integrations import erpnext as erp
from .sales import _erp_client, _own

router = APIRouter(tags=["documents"])

DEFAULT_TERMS = ("1. Prices are ex-works unless stated otherwise.\n2. Payment: 50% advance with order, balance before dispatch.\n3. Delivery: 4-6 weeks from receipt of advance and approved drawings.\n"
                 "4. Warranty: 12 months from commissioning against manufacturing defects.\n5. This quotation is valid for the period stated above.")
COMPANY_KEYS = ("name", "address", "city", "state", "country", "pin", "gstin", "pan", "cin", "tan", "phone", "email", "website", "about", "bank", "show_banner")


# ------------------------------------------------------------------ settings helpers
def company_of(tenant) -> dict:
    c = dict((tenant.settings or {}).get("company") or {})
    c.setdefault("name", tenant.name)
    return c


def next_number(tenant, kind: str, db=None) -> str:
    """Per-workspace number series (quote / proforma / invoice). Increments atomically with the surrounding transaction."""
    defaults = {"quote": ("QTN-", 4), "proforma": ("PI-", 4), "invoice": ("INV-", 4)}
    s = dict(tenant.settings or {})
    cfg = dict((s.get("numbering") or {}).get(kind) or {})
    prefix, pad = cfg.get("prefix", defaults[kind][0]), int(cfg.get("pad", defaults[kind][1]))
    n = int(cfg.get("next", 0))
    if not n and db is not None:
        model = {"quote": Quotation, "proforma": Invoice, "invoice": Invoice}[kind]
        q = select(func.count(model.id)).where(model.tenant_id == tenant.id)
        if kind != "quote":
            q = q.where(model.kind == ("proforma" if kind == "proforma" else "tax"))
        n = (db.scalar(q) or 0) + 1
    n = n or 1
    cfg.update(prefix=prefix, pad=pad, next=n + 1)
    num = s.setdefault("numbering", {})
    num[kind] = cfg
    s["numbering"] = num
    tenant.settings = s
    flag_modified(tenant, "settings")
    return f"{prefix}{n:0{pad}d}"


@router.get("/company")
def get_company(ctx: Ctx = Depends(current)):
    return company_of(ctx.tenant)


@router.put("/company")
def put_company(body: dict, ctx: Ctx = Depends(require("admin"))):
    s = dict(ctx.tenant.settings or {})
    c = dict(s.get("company") or {})
    for k in COMPANY_KEYS:
        if k in body:
            c[k] = body[k]
    if c.get("gstin") and not (len(str(c["gstin"])) == 15):
        raise HTTPException(422, "GSTIN should be 15 characters.")
    s["company"] = c
    ctx.tenant.settings = s
    flag_modified(ctx.tenant, "settings")
    ctx.db.commit()
    return company_of(ctx.tenant)


class CFile(BaseModel):
    kind: str
    data: str


@router.post("/company/files")
def company_file(body: CFile, ctx: Ctx = Depends(require("admin"))):
    if body.kind not in ("logo", "logo_dark", "banner"):
        raise HTTPException(422, "kind must be logo, logo_dark or banner.")
    raw, ext = decode_data_url(body.data)
    path = save_upload(ctx.tid, raw, ext)
    s = dict(ctx.tenant.settings or {})
    c = dict(s.get("company") or {})
    c[f"{body.kind}_path"] = path
    s["company"] = c
    ctx.tenant.settings = s
    flag_modified(ctx.tenant, "settings")
    ctx.db.commit()
    return company_of(ctx.tenant)


@router.get("/quote-settings")
def get_quote_settings(ctx: Ctx = Depends(current)):
    q = dict((ctx.tenant.settings or {}).get("quote") or {})
    n = (ctx.tenant.settings or {}).get("numbering") or {}
    return {"terms": q.get("terms", DEFAULT_TERMS), "validity_days": q.get("validity_days", 15), "charges": q.get("charges", [{"name": "Freight", "type": "fixed", "value": 0}, {"name": "Installation", "type": "percent", "value": 0}]),
            "tax_pct": q.get("tax_pct", 18), "numbering": {k: {"prefix": (n.get(k) or {}).get("prefix", p), "next": (n.get(k) or {}).get("next", 0) or 1} for k, p in (("quote", "QTN-"), ("proforma", "PI-"), ("invoice", "INV-"))}}


@router.put("/quote-settings")
def put_quote_settings(body: dict, ctx: Ctx = Depends(require("admin"))):
    s = dict(ctx.tenant.settings or {})
    q = dict(s.get("quote") or {})
    if "terms" in body:
        q["terms"] = str(body["terms"])[:5000]
    if "validity_days" in body:
        q["validity_days"] = max(1, min(365, int(body["validity_days"])))
    if "tax_pct" in body:
        q["tax_pct"] = max(0, min(100, float(body["tax_pct"])))
    if "charges" in body:
        q["charges"] = [{"name": str(c["name"])[:60], "type": c.get("type") if c.get("type") in ("fixed", "percent") else "fixed", "value": float(c.get("value") or 0)} for c in body["charges"][:12] if c.get("name")]
    s["quote"] = q
    num = dict(s.get("numbering") or {})
    for k, v in (body.get("numbering") or {}).items():
        if k in ("quote", "proforma", "invoice"):
            cur = dict(num.get(k) or {})
            if v.get("prefix") is not None:
                cur["prefix"] = str(v["prefix"])[:12]
            if v.get("next"):
                cur["next"] = max(1, int(v["next"]))
            num[k] = cur
    s["numbering"] = num
    ctx.tenant.settings = s
    flag_modified(ctx.tenant, "settings")
    ctx.db.commit()
    return get_quote_settings(ctx)


# ------------------------------------------------------------------ templates
DEFAULT_TEMPLATES = [
    ("quotation_send", "Quotation - email", "email", "Quotation {quote_no} from {company}", "Dear {customer_name},\n\nThank you for your enquiry. Please find our quotation {quote_no} for {amount}, valid until {valid_till}.\n\nYou can also view it here: {link}\n\nRegards,\n{sender}\n{company}"),
    ("quotation_send", "Quotation - WhatsApp", "whatsapp", "", "Hello {customer_name}, thank you for your enquiry. Our quotation {quote_no} for {amount} (valid till {valid_till}): {link}\n- {sender}, {company}"),
    ("payment_reminder", "Payment reminder - WhatsApp", "whatsapp", "", "Hello {customer_name}, a gentle reminder that {amount} is pending with {company}. Kindly share the payment details. Thank you."),
    ("lead_welcome", "Lead welcome - email", "email", "Thanks for contacting {company}", "Dear {customer_name},\n\nThank you for your enquiry. {sender} from {company} will get in touch shortly.\n\nRegards,\n{company}"),
]


def ensure_templates(db, tenant_id: int):
    if db.scalar(select(func.count(MessageTemplate.id)).where(MessageTemplate.tenant_id == tenant_id, MessageTemplate.deleted_at.is_(None))):
        return
    for key, name, ch, subj, body in DEFAULT_TEMPLATES:
        db.add(MessageTemplate(tenant_id=tenant_id, key=key, name=name, channel=ch, subject=subj, body=body))
    db.flush()


@router.get("/templates")
def list_templates(ctx: Ctx = Depends(current)):
    ensure_templates(ctx.db, ctx.tid)
    ctx.db.commit()
    return [to_dict(t) for t in ctx.db.scalars(select(MessageTemplate).where(MessageTemplate.tenant_id == ctx.tid, MessageTemplate.deleted_at.is_(None)).order_by(MessageTemplate.id))]


@router.post("/templates")
def save_template(body: dict, ctx: Ctx = Depends(require("admin"))):
    if body.get("channel") not in ("email", "whatsapp", "sms") or not str(body.get("name", "")).strip() or not str(body.get("body", "")).strip():
        raise HTTPException(422, "A template needs a name, a channel and a message.")
    t = ctx.db.get(MessageTemplate, body["id"]) if body.get("id") else None
    if t and t.tenant_id != ctx.tid:
        raise HTTPException(404, "Template not found.")
    t = t or MessageTemplate(tenant_id=ctx.tid, key=str(body.get("key") or "custom")[:40])
    t.name, t.channel, t.subject, t.body = body["name"][:120], body["channel"], str(body.get("subject", ""))[:200], body["body"][:4000]
    ctx.db.add(t)
    ctx.db.commit()
    return to_dict(t)


@router.delete("/templates/{tid}")
def del_template(tid: int, ctx: Ctx = Depends(require("admin"))):
    get_or_404(ctx.db, MessageTemplate, tid, ctx.tid).deleted_at = utcnow()
    ctx.db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ signed public links (for WhatsApp / email links)
def _sig(tid: int, qid: int, exp: int) -> str:
    return hmac.new(config.SECRET_KEY.encode(), f"q|{tid}|{qid}|{exp}".encode(), hashlib.sha256).hexdigest()[:32]


def public_link(base: str, q: Quotation, days: int = 30) -> str:
    exp = int(time.time()) + days * 86400
    return f"{base.rstrip('/')}/api/public/quotation/{q.tenant_id}/{q.id}/{exp}/{_sig(q.tenant_id, q.id, exp)}.pdf"


@router.get("/public/quotation/{tid}/{qid}/{exp}/{sig}.pdf")
def public_pdf(tid: int, qid: int, exp: int, sig: str):
    if exp < time.time() or not hmac.compare_digest(sig, _sig(tid, qid, exp)):
        raise HTTPException(404, "This link has expired.")
    db = SessionLocal()
    try:
        q = db.get(Quotation, qid)
        if not q or q.tenant_id != tid or q.deleted_at:
            raise HTTPException(404, "Not found.")
        return Response(_quote_pdf(db, db.get(Tenant, tid), q), media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{q.number}.pdf"'})
    finally:
        db.close()


# ------------------------------------------------------------------ quotation PDF
def _party(db, tid: int, q) -> dict:
    if q.customer_id:
        c = db.get(Customer, q.customer_id)
        return {"name": c.name, "address": ", ".join(x for x in [c.address, c.city] if x), "phone": c.phone, "email": c.email, "gstin": c.gstin, "contact": ""}
    l = db.get(Lead, q.lead_id)
    return {"name": l.company or l.name, "contact": l.name if l.company else "", "address": l.city, "phone": l.phone, "email": l.email, "gstin": ""}


def _quote_pdf(db, tenant, q: Quotation) -> bytes:
    if q.custom_pdf and (config.UPLOAD_DIR / q.custom_pdf).exists():
        return (config.UPLOAD_DIR / q.custom_pdf).read_bytes()
    items = []
    for it in q.items:
        pr = db.get(Product, it.get("product_id")) if it.get("product_id") else None
        items.append({**it, "hsn": pr.hsn if pr else ""})
    return pdfdoc.build_pdf(title="Quotation", number=q.number, date_=q.created_at.date(), valid_till=q.valid_till, company=company_of(tenant), party=_party(db, tenant.id, q), items=items, charges=q.charges or [],
                            subtotal=q.subtotal, discount=q.discount_total, tax_pct=q.tax_pct, total=q.total, currency=(tenant.settings or {}).get("currency", "INR"), terms=q.terms, notes=q.notes, reference=q.erp_name)


def _auth_q(token_or_ctx, db, qid: int, t: str) -> tuple[Ctx, Quotation]:
    ctx = _auth(db, t)
    q = get_or_404(db, Quotation, qid, ctx.tid)
    _own(ctx, q.created_by)
    return ctx, q


@router.get("/quotations/{qid}/pdf")
def quotation_pdf(qid: int, t: str = Query(""), ctx: Ctx = Depends(current)):
    q = get_or_404(ctx.db, Quotation, qid, ctx.tid)
    _own(ctx, q.created_by)
    return Response(_quote_pdf(ctx.db, ctx.tenant, q), media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{q.number}.pdf"'})


@router.get("/quotations/{qid}/pdf-view")
def quotation_pdf_view(qid: int, t: str = Query("")):
    """Same PDF, authenticated with ?t=<token> so it opens in a new browser tab."""
    db = SessionLocal()
    try:
        ctx, q = _auth_q(None, db, qid, t)
        return Response(_quote_pdf(db, ctx.tenant, q), media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{q.number}.pdf"'})
    finally:
        db.close()


class CustomPdf(BaseModel):
    data: str


@router.post("/quotations/{qid}/custom-pdf")
def custom_pdf(qid: int, body: CustomPdf, ctx: Ctx = Depends(current)):
    q = get_or_404(ctx.db, Quotation, qid, ctx.tid)
    _own(ctx, q.created_by)
    raw, ext = decode_doc_url(body.data)
    if ext != "pdf":
        raise HTTPException(422, "Upload a PDF file.")
    q.custom_pdf = save_upload(ctx.tid, raw, "pdf")
    ctx.db.commit()
    return to_dict(q)


class SendIn(BaseModel):
    channel: str  # email | whatsapp
    to: str = ""
    template_id: int | None = None
    subject: str = ""
    message: str = ""
    base_url: str = ""
    mark_sent: bool = False


@router.post("/quotations/{qid}/send")
def send_quotation(qid: int, body: SendIn, ctx: Ctx = Depends(current)):
    q = get_or_404(ctx.db, Quotation, qid, ctx.tid)
    _own(ctx, q.created_by)
    if q.status == "pending_approval":
        raise HTTPException(409, "Waiting for discount approval. It can be sent once approved.")
    if body.channel not in ("email", "whatsapp"):
        raise HTTPException(422, "Channel must be email or whatsapp.")
    party = _party(ctx.db, ctx.tid, q)
    cur = (ctx.tenant.settings or {}).get("currency", "INR")
    link = public_link(body.base_url or "", q) if body.base_url else ""
    vars = {"customer_name": party["contact"] or party["name"], "quote_no": q.number, "amount": pdfdoc.money(q.total, cur), "valid_till": q.valid_till.strftime("%d %b %Y") if q.valid_till else "-",
            "company": company_of(ctx.tenant)["name"], "sender": ctx.user.name, "link": link}
    ensure_templates(ctx.db, ctx.tid)
    tpl = ctx.db.get(MessageTemplate, body.template_id) if body.template_id else ctx.db.scalar(select(MessageTemplate).where(
        MessageTemplate.tenant_id == ctx.tid, MessageTemplate.key == "quotation_send", MessageTemplate.channel == body.channel, MessageTemplate.deleted_at.is_(None)))
    message = svc.render(body.message or (tpl.body if tpl else "Quotation {quote_no}: {link}"), vars)
    subject = svc.render(body.subject or (tpl.subject if tpl else "Quotation {quote_no}"), vars)
    result = {"sent": False, "detail": ""}
    if body.channel == "email":
        to = body.to or party["email"]
        pdf = _quote_pdf(ctx.db, ctx.tenant, q)
        ok, detail = svc.send_email_t(ctx.tenant, to, subject, message, [(f"{q.number}.pdf", pdf, "application/pdf")])
        result.update(sent=ok, detail=detail if not ok else f"Emailed to {to}.")
        if ok:
            q.sent_to = to
    else:
        to = body.to or party["phone"]
        digits = "".join(ch for ch in to if ch.isdigit())
        if len(digits) == 10:
            digits = "91" + digits  # bare Indian mobile numbers get the country code
        ok, detail = svc.gateway_call(ctx.tenant, "whatsapp", {"to": digits, "message": message})
        result.update(sent=ok, detail=detail if ok else "No WhatsApp gateway is connected, so use the link to send it from your own WhatsApp.", wa_link=f"https://wa.me/{digits}?text={urllib.parse.quote(message)}", message=message)
        if ok:
            q.sent_to = to
    if result["sent"] or body.mark_sent:
        q.sent_at = utcnow()
        if q.status == "draft":
            q.status = "sent"
        log_activity(ctx.db, ctx.tid, "lead" if q.lead_id else "customer", q.lead_id or q.customer_id, ctx.user.id, f"{q.number} sent via {body.channel} to {to or '-'}.")
    ctx.db.commit()
    return {**result, "quotation": to_dict(q)}


# ------------------------------------------------------------------ proforma & tax invoices
class InvIn(BaseModel):
    payment_terms: str = ""
    tds_pct: float = Field(0, ge=0, le=100)
    invoice_date: date | None = None


def _order(ctx: Ctx, oid: int) -> SalesOrder:
    o = get_or_404(ctx.db, SalesOrder, oid, ctx.tid)
    _own(ctx, o.created_by)
    if o.status == "cancelled":
        raise HTTPException(409, "This order is cancelled.")
    return o


def _terms_days(text: str, default: int) -> int:
    import re
    m = re.search(r"(\d{1,3})\s*days?", text or "", re.I)
    return int(m.group(1)) if m else default


@router.post("/orders/{oid}/proforma")
def make_proforma(oid: int, body: InvIn, ctx: Ctx = Depends(current)):
    o = _order(ctx, oid)
    inv = Invoice(tenant_id=ctx.tid, number=next_number(ctx.tenant, "proforma", ctx.db), customer_id=o.customer_id, order_id=o.id, kind="proforma", invoice_date=body.invoice_date or local_today(ctx.tenant),
                  due_date=(body.invoice_date or local_today(ctx.tenant)) + timedelta(days=7), total=o.total, outstanding=0, status="open", payment_terms=body.payment_terms or o.payment_terms)
    ctx.db.add(inv)
    ctx.db.commit()
    return to_dict(inv)


@router.post("/orders/{oid}/invoice")
def make_tax_invoice(oid: int, body: InvIn, ctx: Ctx = Depends(require("manager"))):
    """Tax invoice from a confirmed order. With ERPNext on, a draft Sales Invoice is created there and its number stored."""
    o = _order(ctx, oid)
    if o.status == "pending_approval":
        raise HTTPException(409, "The order is waiting for credit approval.")
    if ctx.db.scalar(select(Invoice.id).where(Invoice.tenant_id == ctx.tid, Invoice.order_id == o.id, Invoice.kind == "tax", Invoice.deleted_at.is_(None))):
        raise HTTPException(409, "A tax invoice already exists for this order.")
    c = ctx.db.get(Customer, o.customer_id)
    d = body.invoice_date or local_today(ctx.tenant)
    terms = body.payment_terms or o.payment_terms
    inv = Invoice(tenant_id=ctx.tid, number=next_number(ctx.tenant, "invoice", ctx.db), customer_id=o.customer_id, order_id=o.id, kind="tax", invoice_date=d, due_date=d + timedelta(days=_terms_days(terms, c.credit_days or 30)),
                  total=o.total, outstanding=o.total, status="open", payment_terms=terms, tds_pct=body.tds_pct)
    ctx.db.add(inv)
    ctx.db.flush()
    client = _erp_client(ctx)
    if client:
        try:
            erp.push_sales_invoice(ctx.db, ctx.tenant, client, inv, o)
        except Exception as e:  # noqa
            inv.payment_terms = (inv.payment_terms + " ").strip()
            ctx.db.commit()
            raise HTTPException(502, f"The invoice was saved as {inv.number} but ERPNext said: {e}")
    ctx.db.commit()
    return to_dict(inv)


@router.get("/invoices")
def list_invoices(kind: str | None = None, customer_id: int | None = None, ctx: Ctx = Depends(current)):
    q = select(Invoice, Customer.name).join(Customer, Customer.id == Invoice.customer_id).where(Invoice.tenant_id == ctx.tid, Invoice.deleted_at.is_(None))
    if not ctx.is_at_least("admin"):
        q = q.where((Customer.owner_id.in_(ctx.team_ids())) | (Customer.owner_id.is_(None)))
    if kind:
        q = q.where(Invoice.kind == kind)
    if customer_id:
        q = q.where(Invoice.customer_id == customer_id)
    today = local_today(ctx.tenant)
    return [{**to_dict(i), "customer_name": n, "paid": round(i.total - i.outstanding, 2) if i.kind == "tax" else 0, "days_overdue": max((today - i.due_date).days, 0) if i.kind == "tax" and i.outstanding > 0 else 0} for i, n in ctx.db.execute(q.order_by(Invoice.id.desc()).limit(500))]


def _inv_pdf(ctx_db, tenant, inv: Invoice) -> bytes:
    o = ctx_db.get(SalesOrder, inv.order_id) if inv.order_id else None
    c = ctx_db.get(Customer, inv.customer_id)
    cur = (tenant.settings or {}).get("currency", "INR")
    items = []
    for it in (o.items if o else []):
        pr = ctx_db.get(Product, it.get("product_id")) if it.get("product_id") else None
        items.append({**it, "hsn": pr.hsn if pr else ""})
    sub = sum(i["qty"] * i["rate"] for i in items) if items else inv.total / (1 + (o.tax_pct if o else 0) / 100)
    disc = sub - sum(i["amount"] for i in items) if items else 0
    extra = []
    if inv.kind == "tax" and inv.tds_pct:
        tds = round(inv.total * inv.tds_pct / 100, 2)
        extra = [(f"TDS @ {inv.tds_pct:g}% (deducted by customer)", "- " + pdfdoc.money(tds, cur)), ("Net receivable", pdfdoc.money(inv.total - tds, cur))]
    return pdfdoc.build_pdf(title="Proforma Invoice" if inv.kind == "proforma" else "Tax Invoice", number=inv.number, date_=inv.invoice_date, valid_till=inv.due_date if inv.kind == "tax" else None, company=company_of(tenant),
                            party={"name": c.name, "address": ", ".join(x for x in [c.address, c.city] if x), "phone": c.phone, "email": c.email, "gstin": c.gstin, "contact": ""}, items=items, charges=[], subtotal=sub, discount=disc,
                            tax_pct=o.tax_pct if o else 0, total=inv.total, currency=cur, notes=(o.notes if o else ""), reference=(f"SO {o.number}" + (f" · PO {o.po_number}" if o.po_number else "")) if o else "", payment_terms=inv.payment_terms,
                            terms=(((tenant.settings or {}).get("quote") or {}).get("terms") or ""), extra_rows=extra)


@router.get("/invoices/{iid}/pdf-view")
def invoice_pdf(iid: int, t: str = Query("")):
    db = SessionLocal()
    try:
        ctx = _auth(db, t)
        inv = get_or_404(db, Invoice, iid, ctx.tid)
        return Response(_inv_pdf(db, ctx.tenant, inv), media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{inv.number}.pdf"'})
    finally:
        db.close()
