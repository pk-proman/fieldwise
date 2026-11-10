"""AI Lead Qualifier: settings, results and re-runs."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.orm.attributes import flag_modified
from ..deps import Ctx, current, require
from ..models import Lead, utcnow
from ..common import get_or_404
from .. import qualifier
from datetime import timedelta

router = APIRouter(prefix="/ai/qualifier", tags=["ai-qualifier"])


@router.get("")
def get_settings(ctx: Ctx = Depends(current)):
    cfg = qualifier.settings_of(ctx.tenant)
    since = utcnow() - timedelta(days=30)
    rows = ctx.db.execute(select(Lead.ai_qualification).where(Lead.tenant_id == ctx.tid, Lead.deleted_at.is_(None), Lead.created_at >= since)).all()
    done = [r[0] for r in rows if r[0]]
    q = [d for d in done if d.get("verdict") == "qualified"]
    return {"settings": cfg, "engine": qualifier.ENGINE, "last_30_days": {"leads": len(rows), "scored": len(done), "qualified": len(q), "rejected": len(done) - len(q),
            "avg_score": round(sum(d.get("score", 0) for d in done) / len(done), 1) if done else 0},
            "how": "Seven-area 100-point scorecard plus hard-reject checks for junk, throw-away emails and non-buyers. The reasoning is written into each lead's timeline."}


@router.put("")
def put_settings(body: dict, ctx: Ctx = Depends(require("admin"))):
    s = dict(ctx.tenant.settings or {})
    c = dict(s.get("ai_qualifier") or {})
    if "enabled" in body:
        c["enabled"] = bool(body["enabled"])
    if "auto_regret" in body:
        c["auto_regret"] = bool(body["auto_regret"])
    if "threshold" in body:
        c["threshold"] = max(10, min(90, int(body["threshold"])))
    for k in ("countries", "blocked_domains", "junk_words", "extra_keywords"):
        if k in body:
            c[k] = [str(x).strip()[:60] for x in (body[k] or []) if str(x).strip()][:60]
    s["ai_qualifier"] = c
    ctx.tenant.settings = s
    flag_modified(ctx.tenant, "settings")
    ctx.db.commit()
    return get_settings(ctx)


@router.post("/run")
def run_one(body: dict, ctx: Ctx = Depends(current)):
    """Re-score one lead (after it was edited) and add a new remark."""
    lead = get_or_404(ctx.db, Lead, int(body.get("lead_id", 0)), ctx.tid)
    r = qualifier.evaluate(ctx.db, ctx.tenant, lead)
    qualifier.apply(ctx.db, ctx.tenant, lead, r, first_time=False, auto_regret=False)
    ctx.db.commit()
    return r


@router.post("/backfill")
def backfill(ctx: Ctx = Depends(require("admin"))):
    """Scores every existing lead that has no verdict yet (quietly: no auto-regret, no temperature change)."""
    leads = ctx.db.scalars(select(Lead).where(Lead.tenant_id == ctx.tid, Lead.deleted_at.is_(None)).order_by(Lead.id)).all()
    n = q = 0
    for l in leads:
        if l.ai_qualification:
            continue
        r = qualifier.evaluate(ctx.db, ctx.tenant, l)
        qualifier.apply(ctx.db, ctx.tenant, l, r, first_time=False, auto_regret=False)
        n += 1
        q += r["verdict"] == "qualified"
        if n >= 2000:
            break
    ctx.db.commit()
    return {"scored": n, "qualified": q, "rejected": n - q}
