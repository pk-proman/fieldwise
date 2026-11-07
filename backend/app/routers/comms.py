"""Channels (SMTP / SMS / WhatsApp / click-to-call gateways), call logs and the auto-dialer."""
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.orm.attributes import flag_modified
from ..db import SessionLocal
from ..deps import Ctx, current, require
from ..models import CallLog, Campaign, CampaignContact, Lead, Customer, User, Followup, Tenant, utcnow
from ..common import to_dict, get_or_404, log_activity
from .. import svc
from .intake import _tenant_for
from ..notify import notify

router = APIRouter(tags=["comms"])


# ================================================================== channel settings
def _gw_public(tenant, ch):
    g = dict(((tenant.settings or {}).get("gateways") or {}).get(ch) or {})
    g["has_secret"] = svc.has_secret(tenant, f"gw_{ch}")
    return g


@router.get("/integrations/channels")
def get_channels(ctx: Ctx = Depends(require("admin"))):
    smtp = dict((ctx.tenant.settings or {}).get("smtp") or {})
    smtp["has_password"] = svc.has_secret(ctx.tenant, "smtp_password")
    return {"smtp": smtp, "gateways": {ch: _gw_public(ctx.tenant, ch) for ch in ("sms", "whatsapp", "call")}}


@router.put("/integrations/channels")
def put_channels(body: dict, ctx: Ctx = Depends(require("admin"))):
    s = dict(ctx.tenant.settings or {})
    if "smtp" in body:
        sm = body["smtp"]
        s["smtp"] = {"host": str(sm.get("host", "")).strip(), "port": int(sm.get("port") or 587), "user": str(sm.get("user", "")).strip(), "from": str(sm.get("from", "")).strip(), "starttls": bool(sm.get("starttls", True))}
        ctx.tenant.settings = s
        if sm.get("password"):
            svc.set_secret(ctx.tenant, "smtp_password", sm["password"])
        s = dict(ctx.tenant.settings)
    gws = dict(s.get("gateways") or {})
    for ch, g in (body.get("gateways") or {}).items():
        if ch not in ("sms", "whatsapp", "call"):
            continue
        url = str(g.get("url", "")).strip()
        if url:
            from ..netguard import check_url
            try:
                check_url(url.replace("{{to}}", "x").replace("{{from}}", "x").replace("{{message}}", "x").replace("{{secret}}", "x"))
            except ValueError as e:
                raise HTTPException(422, f"The {ch} gateway address: {e}")
        gws[ch] = {"enabled": bool(g.get("enabled")), "url": url, "method": (g.get("method") or "POST").upper() if (g.get("method") or "POST").upper() in ("POST", "GET") else "POST",
                   "headers": {str(k)[:60]: str(v)[:300] for k, v in (g.get("headers") or {}).items()}, "body": str(g.get("body", ""))[:2000]}
        ctx.tenant.settings = {**s, "gateways": gws}
        if g.get("secret"):
            svc.set_secret(ctx.tenant, f"gw_{ch}", g["secret"])
        s = dict(ctx.tenant.settings)
    s["gateways"] = gws
    ctx.tenant.settings = s
    flag_modified(ctx.tenant, "settings")
    from ..audit import audit
    audit(ctx, "integration.channels", "tenant", ctx.tid, "email / gateway settings changed")
    ctx.db.commit()
    return get_channels(ctx)


@router.post("/integrations/channels/test")
def test_channel(body: dict, ctx: Ctx = Depends(require("admin"))):
    ch, to = body.get("channel"), str(body.get("to", "")).strip()
    if ch == "email":
        ok, d = svc.send_email_t(ctx.tenant, to, "Fieldwise test message", "This is a test message from your Fieldwise workspace.")
    elif ch in ("sms", "whatsapp"):
        ok, d = svc.gateway_call(ctx.tenant, ch, {"to": "".join(c for c in to if c.isdigit()), "message": "Test message from Fieldwise."})
    else:
        raise HTTPException(422, "Choose email, sms or whatsapp.")
    if not ok:
        raise HTTPException(502, d)
    return {"ok": True, "detail": d}


# ================================================================== call logs
class CallIn(BaseModel):
    number: str = Field(min_length=5, max_length=30)
    name: str = ""
    direction: str = "outgoing"
    started_at: datetime | None = None
    duration_sec: int = Field(0, ge=0, le=86400)
    disposition: str = ""
    notes: str = ""
    lead_id: int | None = None


def _match(db, tenant_id: int, number: str):
    pk = svc.phone_key(number)
    if not pk:
        return None, None
    lead = db.scalar(select(Lead).where(Lead.tenant_id == tenant_id, Lead.phone_key == pk, Lead.deleted_at.is_(None)).order_by(Lead.id.desc()))
    cust = db.scalar(select(Customer).where(Customer.tenant_id == tenant_id, Customer.phone_key == pk, Customer.deleted_at.is_(None)))
    return lead, cust


def record_call(db, tenant_id: int, user_id, number, direction, started, dur, name="", notes="", disposition="", lead_id=None, source="manual") -> CallLog:
    lead, cust = _match(db, tenant_id, number)
    c = CallLog(tenant_id=tenant_id, user_id=user_id, number=number[:30], phone_key=svc.phone_key(number), name=name[:200] or (lead.name if lead else cust.name if cust else ""), direction=direction, started_at=started or utcnow(),
                duration_sec=dur, lead_id=lead_id or (lead.id if lead else None), customer_id=cust.id if cust else None, notes=notes[:500], disposition=disposition, source=source)
    db.add(c)
    if c.lead_id:
        log_activity(db, tenant_id, "lead", c.lead_id, user_id, f"{direction.title()} call {dur // 60}m{dur % 60:02d}s." + (f" {notes[:150]}" if notes else ""))
    return c


@router.post("/calls")
def log_call(body: CallIn, ctx: Ctx = Depends(current)):
    if body.direction not in ("incoming", "outgoing", "missed"):
        raise HTTPException(422, "Direction must be incoming, outgoing or missed.")
    if body.lead_id:
        get_or_404(ctx.db, Lead, body.lead_id, ctx.tid)
    c = record_call(ctx.db, ctx.tid, ctx.user.id, body.number, body.direction, body.started_at.replace(tzinfo=None) if body.started_at else None, body.duration_sec, body.name, body.notes, body.disposition, body.lead_id, "app")
    ctx.db.commit()
    return to_dict(c)


@router.get("/calls")
def list_calls(scope: str = "mine", unmatched: bool = False, ctx: Ctx = Depends(current)):
    ids = [ctx.user.id] if scope == "mine" else ctx.team_ids()
    q = select(CallLog, User.name).outerjoin(User, User.id == CallLog.user_id).where(CallLog.tenant_id == ctx.tid, CallLog.deleted_at.is_(None), (CallLog.user_id.in_(ids)) | (CallLog.user_id.is_(None)))
    if unmatched:
        q = q.where(CallLog.lead_id.is_(None), CallLog.customer_id.is_(None))
    lnames = {l.id: l.name for l in ctx.db.scalars(select(Lead).where(Lead.tenant_id == ctx.tid))}
    return [{**to_dict(c), "user_name": n, "lead_name": lnames.get(c.lead_id)} for c, n in ctx.db.execute(q.order_by(CallLog.started_at.desc()).limit(300))]


@router.post("/calls/{cid}/to-lead")
def call_to_lead(cid: int, body: dict, ctx: Ctx = Depends(current)):
    c = get_or_404(ctx.db, CallLog, cid, ctx.tid)
    if c.lead_id:
        raise HTTPException(409, "This call is already linked to a lead.")
    lead, st = svc.create_lead_core(ctx.db, ctx.tenant, ctx.user, {"name": body.get("name") or c.name or c.number, "phone": c.number, "company": body.get("company", ""), "product_id": body.get("product_id"), "notes": c.notes}, "call log", "repeat", ctx.user.name)
    c.lead_id = lead.id
    ctx.db.commit()
    return {**to_dict(lead), "_repeat": st == "repeat"}


@router.delete("/calls/{cid}")
def del_call(cid: int, ctx: Ctx = Depends(current)):
    c = get_or_404(ctx.db, CallLog, cid, ctx.tid)
    if c.user_id != ctx.user.id and not ctx.is_at_least("manager"):
        raise HTTPException(403, "You can only delete your own call records.")
    c.deleted_at = utcnow()
    ctx.db.commit()
    return {"ok": True}


class DialIn(BaseModel):
    to: str
    lead_id: int | None = None


@router.post("/calls/dial")
def dial(body: DialIn, ctx: Ctx = Depends(current)):
    """Click-to-call through the telephony gateway (agent's phone rings first, then the customer). Without a gateway the app falls back to a tap-to-dial link."""
    me = "".join(c for c in (ctx.user.phone or "") if c.isdigit())
    to = "".join(c for c in body.to if c.isdigit())
    ok, detail = svc.gateway_call(ctx.tenant, "call", {"to": to, "from": me, "agent": me})
    if ok:
        record_call(ctx.db, ctx.tid, ctx.user.id, body.to, "outgoing", None, 0, lead_id=body.lead_id, source="app", notes="Click-to-call")
        ctx.db.commit()
    return {"connected": ok, "detail": detail, "tel": f"tel:{body.to}"}


@router.post("/intake/{slug}/calllog")
async def calllog_webhook(slug: str, request: Request, key: str = Query("")):
    """Telephony provider pushes finished calls here: number (or from/to), direction, start, duration, agent email or phone."""
    try:
        p = await request.json()
    except Exception:
        form = await request.form()
        p = dict(form)
    db = SessionLocal()
    try:
        t = _tenant_for(db, slug, key)
        direction = str(p.get("direction") or p.get("type") or "incoming").lower()
        direction = "outgoing" if direction.startswith(("out", "dial")) else "missed" if direction.startswith(("miss", "no")) else "incoming"
        number = str(p.get("number") or (p.get("to") if direction == "outgoing" else p.get("from")) or "").strip()
        if not svc.phone_key(number):
            raise HTTPException(422, "No customer number in the call record.")
        agent = None
        em, ph = str(p.get("agent_email") or "").lower(), svc.phone_key(str(p.get("agent_number") or p.get("agent") or ""))
        if em:
            agent = db.scalar(select(User).where(User.tenant_id == t.id, User.email == em))
        if not agent and ph:
            agent = next((u for u in db.scalars(select(User).where(User.tenant_id == t.id)) if svc.phone_key(u.phone) == ph), None)
        try:
            started = datetime.fromisoformat(str(p.get("start") or p.get("start_time") or "").replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            started = utcnow()
        c = record_call(db, t.id, agent.id if agent else None, number, direction, started, int(float(p.get("duration") or 0)), str(p.get("name") or ""), source="webhook")
        if direction == "missed" and not c.lead_id and not c.customer_id:
            lead, _ = svc.create_lead_core(db, t, None, {"name": c.name or number, "phone": number, "source": "Call enquiry", "notes": "Missed call"}, "missed call", "repeat", "telephony")
            c.lead_id = lead.id
        db.commit()
        return {"ok": True, "id": c.id, "lead_id": c.lead_id}
    finally:
        db.close()


# ================================================================== auto-dialer campaigns
class CampaignIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    mode: str = "auto"
    user_ids: list[int] = []
    contacts: list[dict] = []     # [{number, name, company}]
    from_leads: dict | None = None  # {status, source}: dial existing leads (e.g. all Not-picked)


@router.post("/campaigns")
def create_campaign(body: CampaignIn, ctx: Ctx = Depends(require("manager"))):
    if body.mode not in ("auto", "manual"):
        raise HTTPException(422, "Mode must be auto or manual.")
    team = set(ctx.team_ids())
    users = [u for u in body.user_ids if u in team]
    if body.mode == "auto" and not users:
        raise HTTPException(422, "Choose at least one caller from your team for auto-assign.")
    contacts = [{k: v for k, v in c.items() if k != 'lead_id'} for c in body.contacts]  # lead links come only from our own query below
    if body.from_leads:
        q = select(Lead).where(Lead.tenant_id == ctx.tid, Lead.deleted_at.is_(None), Lead.in_pool.is_(False))
        if body.from_leads.get("status"):
            q = q.where(Lead.status == body.from_leads["status"])
        if body.from_leads.get("source"):
            q = q.where(Lead.source == body.from_leads["source"])
        contacts += [{"number": l.phone, "name": l.name, "company": l.company, "lead_id": l.id} for l in ctx.db.scalars(q.limit(2000)) if svc.phone_key(l.phone)]
    seen, clean = set(), []
    for c in contacts:
        pk = svc.phone_key(str(c.get("number", "")))
        if pk and pk not in seen:
            seen.add(pk)
            clean.append(c)
    if not clean:
        raise HTTPException(422, "No valid phone numbers to dial.")
    if len(clean) > 5000:
        raise HTTPException(413, "A campaign can hold up to 5,000 numbers.")
    cp = Campaign(tenant_id=ctx.tid, name=body.name.strip(), mode=body.mode, created_by=ctx.user.id)
    ctx.db.add(cp)
    ctx.db.flush()
    for i, c in enumerate(clean):
        ctx.db.add(CampaignContact(tenant_id=ctx.tid, campaign_id=cp.id, number=str(c["number"])[:30], name=str(c.get("name", ""))[:200], company=str(c.get("company", ""))[:200], lead_id=c.get("lead_id"),
                                   assigned_to=users[i % len(users)] if body.mode == "auto" else None))
    for u in users:
        notify(ctx.db, ctx.tid, u, "Dialer campaign assigned", f"'{cp.name}': open Auto dialer to start calling.", "dialer")
    ctx.db.commit()
    return {**to_dict(cp), "contacts": len(clean)}


@router.get("/campaigns")
def list_campaigns(ctx: Ctx = Depends(current)):
    out = []
    mine = not ctx.is_at_least("manager")
    for cp in ctx.db.scalars(select(Campaign).where(Campaign.tenant_id == ctx.tid, Campaign.deleted_at.is_(None)).order_by(Campaign.id.desc()).limit(100)):
        q = select(CampaignContact.status, CampaignContact.disposition, CampaignContact.assigned_to, func.count()).where(CampaignContact.campaign_id == cp.id).group_by(CampaignContact.status, CampaignContact.disposition, CampaignContact.assigned_to)
        rows = ctx.db.execute(q).all()
        if mine:
            rows = [r for r in rows if r[2] == ctx.user.id]
            if not rows:
                continue
        out.append({**to_dict(cp), "total": sum(r[3] for r in rows), "pending": sum(r[3] for r in rows if r[0] == "pending"), "disposed": sum(r[3] for r in rows if r[0] == "disposed"),
                    "unassigned": sum(r[3] for r in rows if r[2] is None), "created_leads": sum(r[3] for r in rows if r[1] == "lead_created")})
    return out


@router.get("/campaigns/{cid}/contacts")
def campaign_contacts(cid: int, status: str | None = None, ctx: Ctx = Depends(current)):
    cp = get_or_404(ctx.db, Campaign, cid, ctx.tid)
    q = select(CampaignContact).where(CampaignContact.campaign_id == cp.id)
    if not ctx.is_at_least("manager"):
        q = q.where(CampaignContact.assigned_to == ctx.user.id)
    if status:
        q = q.where(CampaignContact.status == status)
    users = {u.id: u.name for u in ctx.db.scalars(select(User).where(User.tenant_id == ctx.tid))}
    return [{**to_dict(c), "assignee": users.get(c.assigned_to)} for c in ctx.db.scalars(q.order_by(CampaignContact.id).limit(1000))]


@router.post("/campaigns/{cid}/assign")
def campaign_assign(cid: int, body: dict, ctx: Ctx = Depends(require("manager"))):
    cp = get_or_404(ctx.db, Campaign, cid, ctx.tid)
    uid = body.get("user_id")
    if uid not in ctx.team_ids():
        raise HTTPException(403, "Choose someone from your team.")
    n = 0
    for c in ctx.db.scalars(select(CampaignContact).where(CampaignContact.campaign_id == cp.id, CampaignContact.id.in_(body.get("contact_ids") or [0]), CampaignContact.status == "pending")):
        c.assigned_to = uid
        n += 1
    ctx.db.commit()
    return {"assigned": n}


@router.delete("/campaigns/{cid}")
def del_campaign(cid: int, ctx: Ctx = Depends(require("manager"))):
    get_or_404(ctx.db, Campaign, cid, ctx.tid).deleted_at = utcnow()
    ctx.db.commit()
    return {"ok": True}


@router.get("/campaigns-next")
def next_contact(ctx: Ctx = Depends(current)):
    """The caller's next number: due call-backs first, then fresh numbers, then retries."""
    now = utcnow()
    live = [c for (c,) in ctx.db.execute(select(Campaign.id).where(Campaign.tenant_id == ctx.tid, Campaign.deleted_at.is_(None), Campaign.status == "active"))]
    base = select(CampaignContact).where(CampaignContact.tenant_id == ctx.tid, CampaignContact.campaign_id.in_(live or [0]), CampaignContact.assigned_to == ctx.user.id, CampaignContact.status == "pending")
    c = ctx.db.scalar(base.where(CampaignContact.callback_at.is_not(None), CampaignContact.callback_at <= now).order_by(CampaignContact.callback_at).limit(1)) \
        or ctx.db.scalar(base.where(CampaignContact.callback_at.is_(None)).order_by(CampaignContact.attempts, CampaignContact.id).limit(1))
    left = ctx.db.scalar(select(func.count(CampaignContact.id)).where(CampaignContact.tenant_id == ctx.tid, CampaignContact.campaign_id.in_(live or [0]), CampaignContact.assigned_to == ctx.user.id, CampaignContact.status == "pending")) or 0
    if not c:
        return {"contact": None, "remaining": left}
    cp = ctx.db.get(Campaign, c.campaign_id)
    lead, cust = _match(ctx.db, ctx.tid, c.number)
    return {"contact": {**to_dict(c), "campaign": cp.name, "known_as": (lead.name if lead else cust.name if cust else None), "lead_id": c.lead_id or (lead.id if lead else None)}, "remaining": left}


class DisposeIn(BaseModel):
    disposition: str
    notes: str = ""
    callback_at: datetime | None = None
    duration_sec: int = 0
    lead: dict | None = None  # when disposition == lead_created: {name, company, product_id, value}


@router.post("/campaigns/contacts/{ccid}/dispose")
def dispose(ccid: int, body: DisposeIn, ctx: Ctx = Depends(current)):
    c = get_or_404(ctx.db, CampaignContact, ccid, ctx.tid)
    if c.assigned_to != ctx.user.id:
        raise HTTPException(403, "This number is assigned to someone else.")
    if body.disposition not in ("connected", "not_picked", "call_back", "not_interested", "wrong_number", "lead_created"):
        raise HTTPException(422, "Choose how the call went.")
    c.attempts += 1
    c.disposition, c.notes = body.disposition, body.notes[:300]
    if body.disposition == "not_picked":
        c.status, c.callback_at = ("pending", utcnow() + timedelta(hours=2)) if c.attempts < 3 else ("disposed", None)
    elif body.disposition == "call_back":
        if not body.callback_at:
            raise HTTPException(422, "Choose when to call back.")
        c.status, c.callback_at = "pending", body.callback_at.replace(tzinfo=None)
    else:
        c.status, c.callback_at = "disposed", None
    lead_id = c.lead_id
    if body.disposition == "lead_created":
        L = body.lead or {}
        lead, st = svc.create_lead_core(ctx.db, ctx.tenant, ctx.user, {"name": L.get("name") or c.name or c.number, "company": L.get("company") or c.company, "phone": c.number, "product_id": L.get("product_id"),
                                                                       "value": L.get("value") or 0, "notes": body.notes, "source": "Cold calling"}, "auto dialer", "repeat", ctx.user.name)
        c.lead_id = lead_id = lead.id
    record_call(ctx.db, ctx.tid, ctx.user.id, c.number, "outgoing", None, body.duration_sec, c.name, body.notes, body.disposition, lead_id, "dialer")
    if body.disposition == "call_back" and lead_id:
        ctx.db.add(Followup(tenant_id=ctx.tid, lead_id=lead_id, owner_id=ctx.user.id, kind="call", due_at=c.callback_at, notes="Call back (from auto dialer)"))
    ctx.db.commit()
    return to_dict(c)
