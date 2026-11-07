"""Inbound lead capture: portal webhooks (IndiaMART, JustDial, TradeIndia, website forms, Facebook Lead Ads, generic) and call-log webhooks."""
import hmac
import re
import secrets
import urllib.parse
from datetime import datetime
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Query, Header
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified
from ..db import SessionLocal
from ..deps import Ctx, require
from ..models import Tenant, Lead, IntakeLog, Product, utcnow
from ..common import to_dict
from .. import svc

router = APIRouter(tags=["intake"])

SOURCES = {
    "indiamart": ("IndiaMART", "In IndiaMART Lead Manager → Import/Push API, paste this URL as the push URL."),
    "justdial": ("JustDial", "Give this URL to your JustDial account manager as the lead push endpoint."),
    "tradeindia": ("TradeIndia", "Set as the lead-forwarding URL in TradeIndia's integration settings."),
    "sulekha": ("Sulekha", "Lead forwarding URL."), "exportersindia": ("Exporters India", "Lead forwarding URL."), "pharmahopers": ("Pharmahopers", "Lead forwarding URL."),
    "magicbricks": ("Magicbricks", "Lead forwarding URL."), "shopify": ("Shopify", "Create a webhook for 'Customer creation' with this URL."),
    "facebook": ("Facebook Lead Ads", "Webhook callback URL for the 'leadgen' field. Verify token = your intake key. Save a Page access token under Integrations to fetch the lead details."),
    "website": ("Website form", "POST your enquiry form (JSON or form fields: name, email, phone, company, message, product) to this URL."),
    "generic": ("Generic webhook", "Any JSON with name / email / phone / company / message."),
}
ALIAS = {
    "name": ["sender_name", "name", "full_name", "contact_name", "buyer_name", "customer_name", "first_name", "lead_name", "contact person"],
    "phone": ["sender_mobile", "mobile", "phone", "phone_number", "contact_number", "mobile_number", "sender_phone", "buyer_mobile", "tel"],
    "email": ["sender_email", "email", "email_id", "buyer_email", "e-mail"],
    "company": ["sender_company", "company", "company_name", "organization", "organisation", "firm"],
    "city": ["sender_city", "city", "location"], "region": ["sender_state", "state", "region"], "country": ["sender_country", "country", "sender_country_iso"],
    "product_text": ["query_product_name", "product", "product_name", "category", "item", "subject", "requirement"],
    "notes": ["query_message", "message", "notes", "comments", "enquiry", "description", "details", "requirement_details"],
    "source_ref": ["query_id", "leadid", "lead_id", "rfi_id", "enquiry_id", "id", "unique_query_id", "leadgen_id"],
}


def _flat(d, out=None):
    out = {} if out is None else out
    for k, v in (d or {}).items():
        if isinstance(v, dict):
            _flat(v, out)
        elif not isinstance(v, (list, tuple)):
            out[str(k).lower()] = v
    return out


def normalize(source: str, payload: dict) -> dict:
    pl = payload.get("RESPONSE") if isinstance(payload.get("RESPONSE"), dict) else payload  # IndiaMART wraps the lead in RESPONSE
    flat = _flat(pl)
    if isinstance(pl.get("field_data"), list):  # Facebook lead form fields
        for f in pl["field_data"]:
            vals = f.get("values") or [""]
            flat[str(f.get("name", "")).lower()] = vals[0]
        flat.setdefault("name", flat.get("full_name"))
    out = {}
    for k, al in ALIAS.items():
        for a in al:
            if flat.get(a) not in (None, ""):
                out[k] = str(flat[a]).strip()
                break
    if not out.get("name") and flat.get("last_name"):
        out["name"] = (str(flat.get("first_name", "")) + " " + str(flat["last_name"])).strip()
    if source == "shopify" and not out.get("phone"):
        out["phone"] = str(flat.get("phone") or "")
    if out.get("phone"):
        out["phone"] = re.sub(r"[^\d+]", "", out["phone"].replace("-", "")) or out["phone"]
    return out


def _conf(tenant) -> dict:
    return dict((tenant.settings or {}).get("intake") or {})


def ensure_key(tenant) -> str:
    s = dict(tenant.settings or {})
    c = dict(s.get("intake") or {})
    if not c.get("key"):
        c["key"] = secrets.token_urlsafe(24)
        s["intake"] = c
        tenant.settings = s
        flag_modified(tenant, "settings")
    return c["key"]


def _tenant_for(db, slug: str, key: str) -> Tenant:
    t = db.scalar(select(Tenant).where(Tenant.slug == slug))
    k = _conf(t).get("key", "") if t else ""
    if not t or not k or not t.active or not hmac.compare_digest(k, key or ""):
        raise HTTPException(401, "Invalid intake key.")
    return t


def process(db, tenant: Tenant, source: str, payload: dict, client: httpx.Client | None = None) -> tuple[str, str, int | None]:
    data = normalize(source, payload)
    # Facebook sends only a lead id: fetch the details with the saved Page token
    if source == "facebook":
        ents = payload.get("entry") or []
        lid = next((c.get("value", {}).get("leadgen_id") for e in ents for c in e.get("changes", []) if c.get("value", {}).get("leadgen_id")), None) or data.get("source_ref")
        if lid and not data.get("phone") and not data.get("email"):
            tok = svc.get_secret(tenant, "fb_page_token")
            if not tok:
                return "error", "Facebook lead received but no Page access token is saved (Settings → Integrations).", None
            try:
                r = (client or httpx.Client(timeout=15)).get(f"https://graph.facebook.com/v19.0/{lid}", params={"access_token": tok})
                r.raise_for_status()
                data = {**normalize("facebook", r.json()), "source_ref": str(lid)}
            except Exception as e:  # noqa
                return "error", f"Could not fetch Facebook lead {lid}: {str(e)[:150]}", None
    if not data.get("phone") and not data.get("email"):
        return "rejected", "No mobile number or email in the payload.", None
    ref = data.get("source_ref")
    if ref and db.scalar(select(Lead.id).where(Lead.tenant_id == tenant.id, Lead.source_ref == ref, Lead.deleted_at.is_(None))):
        return "duplicate", f"Lead {ref} was already received.", None
    conf = _conf(tenant)
    if conf.get("default_product_id") and not data.get("product_text"):
        data["product_id"] = conf["default_product_id"]
    data["source"] = SOURCES.get(source, (source,))[0]
    data["temperature"] = "warm"
    lead, st = svc.create_lead_core(db, tenant, None, data, SOURCES.get(source, (source,))[0], "repeat", "intake")
    return st, f"{lead.name}", lead.id


async def _payload(request: Request) -> dict:
    ct = request.headers.get("content-type", "")
    try:
        if "json" in ct:
            return await request.json()
        form = await request.form()
        return {k: v for k, v in form.items() if isinstance(v, str)}
    except Exception:
        return dict(request.query_params)


@router.get("/intake/{slug}/facebook")
def fb_verify(slug: str, request: Request):
    q = request.query_params
    db = SessionLocal()
    try:
        _tenant_for(db, slug, q.get("hub.verify_token", ""))
        return PlainTextResponse(q.get("hub.challenge", ""))
    finally:
        db.close()


@router.post("/intake/{slug}/{source}")
async def intake(slug: str, source: str, request: Request, key: str = Query(""), x_intake_key: str = Header(default="")):
    if source not in SOURCES:
        raise HTTPException(404, "Unknown source.")
    payload = await _payload(request)
    db = SessionLocal()
    try:
        t = _tenant_for(db, slug, key or x_intake_key)
        try:
            status, msg, lid = process(db, t, source, payload)
        except HTTPException as e:
            status, msg, lid = "rejected", str(e.detail), None
        except Exception as e:  # noqa
            db.rollback()
            status, msg, lid = "error", str(e)[:200], None
        db.add(IntakeLog(tenant_id=t.id, source=source, status=status, lead_id=lid, message=msg[:300], payload={k: str(v)[:300] for k, v in list(_flat(payload).items())[:30]}))
        db.commit()
        # IndiaMART expects this shape; harmless for the others
        return {"CODE": 200 if status in ("created", "repeat", "duplicate") else 400, "STATUS": "SUCCESS" if status in ("created", "repeat", "duplicate") else "FAILED", "result": status, "message": msg}
    finally:
        db.close()


# ------------------------------------------------------------------ admin
@router.get("/integrations/intake")
def get_intake(request: Request, ctx: Ctx = Depends(require("admin"))):
    key = ensure_key(ctx.tenant)
    ctx.db.commit()
    base = str(request.base_url).rstrip("/")
    c = _conf(ctx.tenant)
    return {"key": key, "default_product_id": c.get("default_product_id"), "has_fb_token": svc.has_secret(ctx.tenant, "fb_page_token"),
            "sources": [{"id": k, "name": v[0], "help": v[1], "url": f"{base}/api/intake/{ctx.tenant.slug}/{k}?key={key}"} for k, v in SOURCES.items()],
            "calllog_url": f"{base}/api/intake/{ctx.tenant.slug}/calllog?key={key}"}


@router.put("/integrations/intake")
def put_intake(body: dict, ctx: Ctx = Depends(require("admin"))):
    ensure_key(ctx.tenant)
    s = dict(ctx.tenant.settings or {})
    c = dict(s.get("intake") or {})
    if "default_product_id" in body:
        pid = body["default_product_id"]
        if pid and not ctx.db.scalar(select(Product.id).where(Product.tenant_id == ctx.tid, Product.id == int(pid))):
            raise HTTPException(422, "Unknown product.")
        c["default_product_id"] = int(pid) if pid else None
    s["intake"] = c
    ctx.tenant.settings = s
    flag_modified(ctx.tenant, "settings")
    if body.get("fb_page_token"):
        svc.set_secret(ctx.tenant, "fb_page_token", body["fb_page_token"])
    ctx.db.commit()
    return {"ok": True}


@router.post("/integrations/intake/rotate")
def rotate(ctx: Ctx = Depends(require("admin"))):
    s = dict(ctx.tenant.settings or {})
    c = dict(s.get("intake") or {})
    c["key"] = secrets.token_urlsafe(24)
    s["intake"] = c
    ctx.tenant.settings = s
    flag_modified(ctx.tenant, "settings")
    from ..audit import audit
    audit(ctx, "integration.intake_key_rotated", "tenant", ctx.tid)
    ctx.db.commit()
    return {"key": c["key"]}


@router.post("/integrations/intake/test")
def test_mapping(body: dict, ctx: Ctx = Depends(require("admin"))):
    """Shows how a sample payload from a portal would be mapped, without creating anything."""
    src = body.get("source", "generic")
    if src not in SOURCES:
        raise HTTPException(404, "Unknown source.")
    d = normalize(src, body.get("payload") or {})
    dup = svc.find_duplicate_lead(ctx.db, ctx.tid, d.get("phone", ""), d.get("email", ""))
    return {"mapped": d, "would": "repeat enquiry on lead #%d" % dup.id if dup else ("rejected: no mobile or email" if not d.get("phone") and not d.get("email") else "create a new lead"),
            "product": (svc.match_product(ctx.db, ctx.tid, d.get("product_text", "")) or None) and svc.match_product(ctx.db, ctx.tid, d.get("product_text", "")).name}


@router.get("/integrations/intake/logs")
def logs(ctx: Ctx = Depends(require("admin"))):
    return [to_dict(l) for l in ctx.db.scalars(select(IntakeLog).where(IntakeLog.tenant_id == ctx.tid).order_by(IntakeLog.id.desc()).limit(100))]
