"""Optional AI assistance: workspace brings its own provider key. Falls back to transparent rules when off."""
import json
import re
import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified
from ..deps import Ctx, current, require
from ..models import Lead, Activity, Followup, Quotation, Meeting, Product, utcnow
from ..common import get_or_404
from .. import svc
from .crm import lead_insight

router = APIRouter(tags=["ai"])
DEFAULT_PROMPTS = {
    "lead_analyze": "You are a sales coach for an industrial equipment business. Analyse this lead and answer ONLY with JSON having keys: summary (2 sentences), risks (list of up to 3 short strings), next_steps (list of up to 4 short, concrete actions), suggested_followup_days (integer), draft_message (a short, polite WhatsApp message to the customer).",
    "draft_email": "Write a concise, professional follow-up email (subject on the first line) for this lead. No placeholders.",
}
PROVIDERS = {"anthropic": "claude-haiku-5-5", "openai": "gpt-4o-mini"}


def _cfg(tenant) -> dict:
    return dict((tenant.settings or {}).get("ai") or {})


@router.get("/integrations/ai")
def get_ai(ctx: Ctx = Depends(require("admin"))):
    c = _cfg(ctx.tenant)
    return {"enabled": c.get("enabled", False), "provider": c.get("provider", "anthropic"), "model": c.get("model", PROVIDERS["anthropic"]), "has_key": svc.has_secret(ctx.tenant, "ai_key"),
            "prompts": {**DEFAULT_PROMPTS, **(c.get("prompts") or {})}, "providers": PROVIDERS,
            "privacy": "When enabled, lead details (name, company, notes, recent activity) are sent to the provider you choose."}


@router.put("/integrations/ai")
def put_ai(body: dict, ctx: Ctx = Depends(require("admin"))):
    s = dict(ctx.tenant.settings or {})
    c = dict(s.get("ai") or {})
    if body.get("provider") in PROVIDERS:
        c["provider"] = body["provider"]
    if "model" in body:
        c["model"] = str(body["model"])[:60]
    if "enabled" in body:
        c["enabled"] = bool(body["enabled"])
    if "prompts" in body:
        c["prompts"] = {k: str(v)[:2000] for k, v in body["prompts"].items() if k in DEFAULT_PROMPTS}
    s["ai"] = c
    ctx.tenant.settings = s
    flag_modified(ctx.tenant, "settings")
    if body.get("key"):
        svc.set_secret(ctx.tenant, "ai_key", body["key"])
    ctx.db.commit()
    return get_ai(ctx)


def _context(ctx: Ctx, lead: Lead) -> str:
    pr = ctx.db.get(Product, lead.product_id) if lead.product_id else None
    acts = ctx.db.scalars(select(Activity).where(Activity.tenant_id == ctx.tid, Activity.entity == "lead", Activity.entity_id == lead.id).order_by(Activity.id.desc()).limit(10)).all()
    fus = ctx.db.scalars(select(Followup).where(Followup.tenant_id == ctx.tid, Followup.lead_id == lead.id).order_by(Followup.due_at.desc()).limit(5)).all()
    qs = ctx.db.scalars(select(Quotation).where(Quotation.tenant_id == ctx.tid, Quotation.lead_id == lead.id)).all()
    return json.dumps({"lead": {"name": lead.name, "company": lead.company, "city": lead.city, "stage": lead.status, "temperature": lead.temperature, "value": lead.value, "source": lead.source, "product": pr.name if pr else None,
                                "qualification": lead.qualification, "repeat_enquiries": lead.repeat_count, "age_days": (utcnow() - lead.created_at).days, "notes": lead.notes[:500]},
                       "recent_activity": [a.text[:200] for a in acts], "followups": [{"due": f.due_at.isoformat(), "status": f.status, "outcome": f.outcome} for f in fus], "quotations": [{"no": q.number, "total": q.total, "status": q.status} for q in qs]})


def _call_provider(tenant, system: str, user: str, client: httpx.Client | None = None) -> str:
    c = _cfg(tenant)
    key = svc.get_secret(tenant, "ai_key")
    prov = c.get("provider", "anthropic")
    model = c.get("model") or PROVIDERS[prov]
    http = client or httpx.Client(timeout=40)
    if prov == "anthropic":
        r = http.post("https://api.anthropic.com/v1/messages", headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                      json={"model": model, "max_tokens": 700, "system": system, "messages": [{"role": "user", "content": user}]})
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json().get("content", []))
    r = http.post("https://api.openai.com/v1/chat/completions", headers={"Authorization": f"Bearer {key}"}, json={"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}], "max_tokens": 700})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


_client_factory = None  # tests inject an httpx.Client with a mock transport


def rules_analysis(ctx: Ctx, lead: Lead) -> dict:
    ins = lead_insight(lead, None, 0, True, bool(ctx.db.scalar(select(Quotation.id).where(Quotation.tenant_id == ctx.tid, Quotation.lead_id == lead.id))))
    steps = {"Qualify this lead (budget, authority, need, timeline)": 2, "Send a quotation": 1}.get(ins["next_action"])
    return {"source": "rules", "summary": f"{lead.name} ({lead.company or 'no company'}) is at stage '{lead.status}' with a score of {ins['score']}/100.", "risks": (["Lead has gone quiet"] if (utcnow() - lead.created_at).days > 30 else []) + (["No quotation yet"] if lead.status in ("qualified",) else []),
            "next_steps": [ins["next_action"]], "suggested_followup_days": steps or 3, "draft_message": f"Hello {lead.name}, thank you for your interest. Could we speak briefly about your requirement?"}


@router.post("/ai/lead-analyze")
def analyze(body: dict, ctx: Ctx = Depends(current)):
    lead = get_or_404(ctx.db, Lead, int(body.get("lead_id", 0)), ctx.tid)
    c = _cfg(ctx.tenant)
    if not (c.get("enabled") and svc.has_secret(ctx.tenant, "ai_key")):
        return rules_analysis(ctx, lead)
    prompt = {**DEFAULT_PROMPTS, **(c.get("prompts") or {})}["lead_analyze"]
    try:
        raw = _call_provider(ctx.tenant, prompt, _context(ctx, lead), _client_factory() if _client_factory else None)
        m = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(m.group(0)) if m else {}
        return {"source": c.get("provider", "ai"), "summary": str(data.get("summary", ""))[:600], "risks": [str(x)[:160] for x in (data.get("risks") or [])][:3], "next_steps": [str(x)[:200] for x in (data.get("next_steps") or [])][:4],
                "suggested_followup_days": int(data.get("suggested_followup_days") or 3), "draft_message": str(data.get("draft_message", ""))[:800]}
    except Exception as e:  # noqa
        out = rules_analysis(ctx, lead)
        out["note"] = f"The AI provider could not be reached ({str(e)[:80]}); showing rule-based advice."
        return out
