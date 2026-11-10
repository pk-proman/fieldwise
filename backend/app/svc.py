"""Shared services: stage configuration, phone keys, lead assignment, duplicate detection, tenant secrets, and outbound channels."""
import base64
import hashlib
import json
import logging
import re
import smtplib
from email.message import EmailMessage
import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified
from . import config
from .models import Lead, Customer, Product, User, Activity, Followup, Master, utcnow

log = logging.getLogger("fieldwise.svc")

DEFAULT_STAGES = [
    {"key": "new", "label": "New", "kind": "open", "gate": "none", "prob": 5},
    {"key": "not_picked", "label": "Not picked", "kind": "open", "gate": "none", "prob": 3},
    {"key": "contacted", "label": "Contacted", "kind": "open", "gate": "none", "prob": 10},
    {"key": "visited", "label": "Visited", "kind": "open", "gate": "none", "prob": 20},
    {"key": "qualified", "label": "Qualified", "kind": "open", "gate": "qualified", "prob": 30},
    {"key": "proposal", "label": "Quoted", "kind": "open", "gate": "quoted", "prob": 50},
    {"key": "negotiation", "label": "Negotiation", "kind": "open", "gate": "quoted", "prob": 70},
    {"key": "won", "label": "Deal won", "kind": "won", "gate": "quoted", "prob": 100},
    {"key": "lost", "label": "Deal lost", "kind": "lost", "gate": "none", "prob": 0},
    {"key": "regretted", "label": "Regretted", "kind": "lost", "gate": "none", "prob": 0},
]
DEFAULT_MASTERS = {
    "lead_source": ["Website", "Referral", "Exhibition", "Cold call", "Direct visit", "IndiaMART", "JustDial", "TradeIndia", "Facebook", "Existing customer", "Other"],
    "regret_reason": ["Price too high", "Chose a competitor", "No budget", "No response", "Requirement changed", "Not a fit", "Other"],
    "region": [], "product_group": [], "product_category": [], "unit": ["Nos", "Set", "Kg", "Tonne", "Meter", "Each"],
    "business_type": ["Manufacturer", "Contractor", "Trader", "Dealer", "End user", "Government"], "doc_type": ["GST certificate", "PAN", "Trade licence", "Bank details"],
    "ticket_type": ["Login related issues", "Data correction", "Feature request", "Integration help", "Other"],
}


def stages_of(tenant) -> list[dict]:
    s = (tenant.settings or {}).get("stages")
    return s if s else DEFAULT_STAGES


def stage_map(tenant) -> dict:
    return {s["key"]: s for s in stages_of(tenant)}


def entry_stage(tenant) -> str:
    return next((s["key"] for s in stages_of(tenant) if s["kind"] == "open"), "new")


def masters(db, tenant_id, kind) -> list[str]:
    rows = [m.name for m in db.scalars(select(Master).where(Master.tenant_id == tenant_id, Master.kind == kind, Master.deleted_at.is_(None)).order_by(Master.sort, Master.id))]
    return rows or DEFAULT_MASTERS.get(kind, [])


def phone_key(s: str | None) -> str:
    d = re.sub(r"\D", "", s or "")
    return d[-10:] if len(d) >= 7 else ""


# ------------------------------------------------------------------ secrets
def _fernet() -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("fw-secrets|" + config.SECRET_KEY).encode()).digest()))


def set_secret(tenant, name: str, value: str):
    s = dict(tenant.settings or {})
    sec = dict(s.get("secrets") or {})
    if value:
        sec[name] = _fernet().encrypt(value.encode()).decode()
    s["secrets"] = sec
    tenant.settings = s
    flag_modified(tenant, "settings")


def get_secret(tenant, name: str) -> str:
    tok = ((tenant.settings or {}).get("secrets") or {}).get(name)
    if not tok:
        return ""
    try:
        return _fernet().decrypt(tok.encode()).decode()
    except InvalidToken:
        return ""


def has_secret(tenant, name: str) -> bool:
    return bool(((tenant.settings or {}).get("secrets") or {}).get(name))


# ------------------------------------------------------------------ assignment & duplicates
def pick_owner(db, tenant, data: dict):
    """Returns (owner_id | None, in_pool). Rules match on source/region/product/country; round-robin across the rule's users."""
    cfg = (tenant.settings or {}).get("assignment") or {}
    mode = cfg.get("mode", "none")
    if mode == "none":
        return None, False
    def norm(v):
        return str(v or "").strip().lower()
    users_for, key = None, "default"
    for i, rule in enumerate(cfg.get("rules") or []):
        m = rule.get("match") or {}
        if all(norm(data.get(k)) == norm(v) for k, v in m.items() if v not in (None, "")):
            users_for, key = rule.get("user_ids") or [], f"r{i}"
            break
    if users_for is None:
        users_for = cfg.get("default_user_ids") or []
    ok = {u for (u,) in db.execute(select(User.id).where(User.tenant_id == tenant.id, User.active.is_(True), User.deleted_at.is_(None), User.id.in_(users_for or [0])))}
    users_for = [u for u in users_for if u in ok]
    if not users_for:
        return None, mode == "pool"
    s = dict(tenant.settings or {})
    a = dict(s.get("assignment") or {})
    rr = dict(a.get("rr") or {})
    n = rr.get(key, 0)
    rr[key] = n + 1
    a["rr"] = rr
    s["assignment"] = a
    tenant.settings = s
    flag_modified(tenant, "settings")
    return users_for[n % len(users_for)], False


def sp_for_user(db, tenant_id: int, user_id):
    """The sales-team entry tied to a login (or None)."""
    from .models import SalesPerson
    if not user_id:
        return None
    return db.scalar(select(SalesPerson).where(SalesPerson.tenant_id == tenant_id, SalesPerson.user_id == user_id, SalesPerson.deleted_at.is_(None)).order_by(SalesPerson.enabled.desc()).limit(1))


def sync_owner_fields(db, tenant_id: int, data: dict):
    """Keeps a lead's two owner fields consistent. Picking a sales-team member sets the owner to that person's login (none yet = unassigned
    to a login, visible to managers); picking a user sets the matching sales-team member."""
    from .models import SalesPerson
    if "sales_person_id" in data:
        sp = db.get(SalesPerson, int(data["sales_person_id"])) if data["sales_person_id"] else None
        if sp and (sp.tenant_id != tenant_id or sp.deleted_at):
            raise HTTPException(422, "Sales person not found.")
        data["sales_person_id"] = sp.id if sp else None
        data["owner_id"] = sp.user_id if sp else None
    elif data.get("owner_id"):
        sp = sp_for_user(db, tenant_id, data["owner_id"])
        data["sales_person_id"] = sp.id if sp else None
    elif "owner_id" in data:
        data["sales_person_id"] = None
    return data


def find_duplicate_lead(db, tenant_id: int, phone: str, email: str):
    pk, em = phone_key(phone), (email or "").strip().lower()
    q = select(Lead).where(Lead.tenant_id == tenant_id, Lead.deleted_at.is_(None))
    conds = []
    if pk:
        conds.append(Lead.phone_key == pk)
    if em:
        conds.append(Lead.email.ilike(em))
    if not conds:
        return None
    from sqlalchemy import or_
    return db.scalar(q.where(or_(*conds)).order_by(Lead.id))


def unspecified_product(db, tenant_id: int) -> Product:
    p = db.scalar(select(Product).where(Product.tenant_id == tenant_id, Product.code == "UNSPEC", Product.deleted_at.is_(None)))
    if not p:
        p = Product(tenant_id=tenant_id, code="UNSPEC", name="Unspecified (to be classified)", price=0)
        db.add(p)
        db.flush()
    return p


def match_product(db, tenant_id: int, text: str):
    """Best-effort map of a portal's product text to the product master (exact code/name, then substring)."""
    t = (text or "").strip().lower()
    if not t:
        return None
    prods = db.scalars(select(Product).where(Product.tenant_id == tenant_id, Product.deleted_at.is_(None), Product.disabled.is_(False))).all()
    for p in prods:
        if t in (p.code.lower(), p.name.lower()):
            return p
    for p in prods:
        if p.code.lower() != "unspec" and (p.name.lower() in t or t in p.name.lower()):
            return p
    return None


def create_lead_core(db, tenant, creator: User | None, data: dict, source: str = "manual", dedupe: str = "repeat", actor: str = "System"):
    """Creates a lead from a trusted dict (intake, import, dialer, call log). Returns (lead, 'created' | 'repeat')."""
    from .common import log_activity
    from .notify import notify
    phone, email = data.get("phone", ""), data.get("email", "")
    if not phone_key(phone) and not (email or "").strip():
        raise HTTPException(422, "A mobile number or an email is required.")
    dup = find_duplicate_lead(db, tenant.id, phone, email)
    if dup and dedupe != "create":
        if dedupe == "reject":
            raise HTTPException(409, f"Duplicate: lead #{dup.id} ({dup.name}) already has this mobile or email.")
        dup.repeat_count = (dup.repeat_count or 1) + 1
        msg = f"Repeat enquiry #{dup.repeat_count} via {source}" + (f": {data.get('notes', '')[:200]}" if data.get("notes") else "")
        log_activity(db, tenant.id, "lead", dup.id, creator.id if creator else None, msg)
        if dup.owner_id:
            notify(db, tenant.id, dup.owner_id, "Repeat enquiry", f"{dup.name} enquired again ({source}).", "lead")
        if dup.status in [s["key"] for s in stages_of(tenant) if s["kind"] == "lost"]:
            dup.status, dup.stage_changed_at = entry_stage(tenant), utcnow()  # a regretted lead that comes back is reopened
        return dup, "repeat"
    pr = None
    if data.get("product_id"):
        pr = db.get(Product, int(data["product_id"]))
    if not pr or pr.tenant_id != tenant.id:
        pr = match_product(db, tenant.id, data.get("product_text", "")) or unspecified_product(db, tenant.id)
    sp_id = None
    if data.get("sales_person_id"):
        o = sync_owner_fields(db, tenant.id, {"sales_person_id": data["sales_person_id"]})
        data = {**data, "owner_id": o["owner_id"]}
        sp_id = o["sales_person_id"]
        owner, in_pool = o["owner_id"], False
    else:
        owner, in_pool = (data.get("owner_id"), False) if data.get("owner_id") else pick_owner(db, tenant, {**data, "product_id": pr.id})
        if not owner and not in_pool and creator:
            owner = creator.id
        sp = sp_for_user(db, tenant.id, owner)
        sp_id = sp.id if sp else None
    lead = Lead(tenant_id=tenant.id, name=(data.get("name") or data.get("company") or phone or email)[:200], company=(data.get("company") or "")[:200], phone=phone[:40], email=(email or "")[:200],
                source=(data.get("source") or source)[:60], status=entry_stage(tenant), value=float(data.get("value") or 0), owner_id=owner, city=(data.get("city") or "")[:100], country=(data.get("country") or "India")[:50],
                notes=(data.get("notes") or "")[:2000], product_id=pr.id, region=(data.get("region") or data.get("state") or "")[:60], tags=data.get("tags") or ([] if pr.code != "UNSPEC" else ["needs-product"]),
                in_pool=in_pool, sales_person_id=sp_id, source_ref=str(data.get("source_ref") or "")[:100], phone_key=phone_key(phone), stage_changed_at=utcnow(), event_id=data.get("event_id"),
                temperature=data.get("temperature") if data.get("temperature") in ("hot", "warm", "cold") else "warm", custom=data.get("custom") or {})
    db.add(lead)
    db.flush()
    log_activity(db, tenant.id, "lead", lead.id, creator.id if creator else None, f"Lead created via {source} by {actor}." + (" Placed in the common lead pool." if in_pool else ""))
    from . import qualifier
    qualifier.qualify_new_lead(db, tenant, lead, data.get("product_text", ""))  # automation: every new lead is scored and the reasoning is added as a remark
    days = int((tenant.settings or {}).get("auto_followup_days", 1) or 0)
    if days > 0 and owner:
        due = (utcnow() + __import__("datetime").timedelta(days=days)).replace(hour=5, minute=0, second=0, microsecond=0)
        db.add(Followup(tenant_id=tenant.id, lead_id=lead.id, owner_id=owner, kind="call", due_at=due, notes="Introductory follow-up (auto-scheduled)"))
    if owner and (not creator or owner != creator.id):
        notify(db, tenant.id, owner, "New lead assigned", f"{lead.name} ({lead.source}) was assigned to you.", "lead")
    return lead, "created"


# ------------------------------------------------------------------ templates & channels
def render(text: str, vars: dict) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: str(vars.get(m.group(1), m.group(0))), text or "")


def send_email_t(tenant, to: str, subject: str, body: str, attachments: list[tuple[str, bytes, str]] | None = None) -> tuple[bool, str]:
    """Sends through the workspace's own SMTP account (Settings), falling back to the server-wide one."""
    cfg = (tenant.settings or {}).get("smtp") or {}
    host = cfg.get("host") or config.SMTP_HOST
    if not host:
        return False, "No SMTP server is configured. Add one in Settings → Notifications."
    if not to:
        return False, "No recipient email."
    try:
        msg = EmailMessage()
        msg["From"] = cfg.get("from") or cfg.get("user") or config.SMTP_FROM
        msg["To"], msg["Subject"] = to, subject
        msg.set_content(body)
        for fn, data, mime in attachments or []:
            mt, st = (mime.split("/", 1) + ["octet-stream"])[:2]
            msg.add_attachment(data, maintype=mt, subtype=st, filename=fn)
        port = int(cfg.get("port") or config.SMTP_PORT)
        pw = get_secret(tenant, "smtp_password") or config.SMTP_PASSWORD
        user = cfg.get("user") or config.SMTP_USER
        cls = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
        with cls(host, port, timeout=20) as s:
            if port != 465 and cfg.get("starttls", True):
                s.starttls()
            if user:
                s.login(user, pw)
            s.send_message(msg)
        return True, "sent"
    except Exception as e:  # noqa
        log.warning("email failed: %s", e)
        return False, f"Email failed: {str(e)[:200]}"


def gateway_call(tenant, channel: str, vars: dict, client: httpx.Client | None = None) -> tuple[bool, str]:
    """Generic HTTP gateway for sms / whatsapp / call. Config: settings.gateways[channel] = {enabled,url,method,headers,body}.
    Placeholders: {{to}} {{message}} {{from}} {{secret}} (the encrypted gateway token)."""
    cfg = ((tenant.settings or {}).get("gateways") or {}).get(channel) or {}
    if not cfg.get("enabled") or not cfg.get("url"):
        return False, f"The {channel} gateway is not configured. Add it in Settings → Notifications."
    secret = get_secret(tenant, f"gw_{channel}")
    if client is None:
        from .netguard import check_url
        try:
            check_url(re.sub(r"\{\{\w+\}\}", "x", cfg["url"]))
        except ValueError as e:
            return False, f"{channel} gateway blocked: {e}"
    def sub_json(tpl: str) -> str:
        return re.sub(r"\{\{(\w+)\}\}", lambda m: json.dumps(secret if m.group(1) == "secret" else str(vars.get(m.group(1), "")))[1:-1], tpl)
    try:
        headers = {k: re.sub(r"\{\{secret\}\}", secret, str(v)) for k, v in (cfg.get("headers") or {}).items()}
        url = re.sub(r"\{\{(\w+)\}\}", lambda m: __import__("urllib.parse").parse.quote(secret if m.group(1) == "secret" else str(vars.get(m.group(1), ""))), cfg["url"])
        body = sub_json(cfg.get("body") or '{"to":"{{to}}","message":"{{message}}"}')
        c = client or httpx.Client(timeout=15)
        method = (cfg.get("method") or "POST").upper()
        r = c.request(method, url, headers={"Content-Type": "application/json", **headers}, content=None if method == "GET" else body)
        return (r.is_success, f"HTTP {r.status_code}")
    except Exception as e:  # noqa
        return False, f"{channel} gateway error: {str(e)[:200]}"
