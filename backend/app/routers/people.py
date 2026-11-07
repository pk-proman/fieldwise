"""Departments, designations, incentive plans and the incentive report."""
from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from ..deps import Ctx, current, require
from ..models import Department, Designation, IncentivePlan, User, SalesOrder, Payment, Visit, Meeting, Lead, utcnow
from ..common import to_dict, get_or_404, local_today, local_day_bounds_utc, csv_response_text
from .. import svc

router = APIRouter(tags=["people"])


# ------------------------------------------------------------------ departments & designations
@router.get("/departments")
def departments(ctx: Ctx = Depends(current)):
    counts = dict(ctx.db.execute(select(User.department_id, func.count()).where(User.tenant_id == ctx.tid, User.deleted_at.is_(None)).group_by(User.department_id)).all())
    return [{**to_dict(d), "users": counts.get(d.id, 0)} for d in ctx.db.scalars(select(Department).where(Department.tenant_id == ctx.tid, Department.deleted_at.is_(None)).order_by(Department.name))]


@router.post("/departments")
def add_department(body: dict, ctx: Ctx = Depends(require("admin"))):
    name = str(body.get("name", "")).strip()
    if not name:
        raise HTTPException(422, "Enter a department name.")
    if body.get("parent_id"):
        get_or_404(ctx.db, Department, int(body["parent_id"]), ctx.tid)
    d = Department(tenant_id=ctx.tid, name=name[:100], parent_id=body.get("parent_id") or None)
    ctx.db.add(d)
    ctx.db.commit()
    return to_dict(d)


@router.delete("/departments/{did}")
def del_department(did: int, ctx: Ctx = Depends(require("admin"))):
    d = get_or_404(ctx.db, Department, did, ctx.tid)
    if ctx.db.scalar(select(func.count()).where(User.tenant_id == ctx.tid, User.department_id == did, User.deleted_at.is_(None))):
        raise HTTPException(409, "People are still assigned to this department.")
    if ctx.db.scalar(select(func.count()).where(Department.tenant_id == ctx.tid, Department.parent_id == did, Department.deleted_at.is_(None))):
        raise HTTPException(409, "Remove its sub-departments first.")
    d.deleted_at = utcnow()
    ctx.db.commit()
    return {"ok": True}


@router.get("/designations")
def designations(ctx: Ctx = Depends(current)):
    counts = dict(ctx.db.execute(select(User.designation_id, func.count()).where(User.tenant_id == ctx.tid, User.deleted_at.is_(None)).group_by(User.designation_id)).all())
    return [{**to_dict(d), "users": counts.get(d.id, 0)} for d in ctx.db.scalars(select(Designation).where(Designation.tenant_id == ctx.tid, Designation.deleted_at.is_(None)).order_by(Designation.name))]


@router.post("/designations")
def add_designation(body: dict, ctx: Ctx = Depends(require("admin"))):
    name = str(body.get("name", "")).strip()
    if not name:
        raise HTTPException(422, "Enter a designation.")
    d = Designation(tenant_id=ctx.tid, name=name[:100])
    ctx.db.add(d)
    ctx.db.commit()
    return to_dict(d)


@router.delete("/designations/{did}")
def del_designation(did: int, ctx: Ctx = Depends(require("admin"))):
    d = get_or_404(ctx.db, Designation, did, ctx.tid)
    if ctx.db.scalar(select(func.count()).where(User.tenant_id == ctx.tid, User.designation_id == did, User.deleted_at.is_(None))):
        raise HTTPException(409, "People still hold this designation.")
    d.deleted_at = utcnow()
    ctx.db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ incentives
class Slab(BaseModel):
    frm: float = Field(ge=0, alias="from")
    to: float | None = None
    pct: float = Field(0, ge=0, le=100)
    flat: float = Field(0, ge=0)


class PlanIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    metric: str
    slabs: list[Slab] = Field(min_length=1, max_length=12)
    user_ids: list[int] = []
    active: bool = True


@router.post("/incentives/plans")
def save_plan(body: PlanIn, id: int | None = None, ctx: Ctx = Depends(require("manager"))):
    if body.metric not in ("revenue", "orders", "collections", "visits"):
        raise HTTPException(422, "Metric must be revenue, orders, collections or visits.")
    sl = sorted(({"from": s.frm, "to": s.to, "pct": s.pct, "flat": s.flat} for s in body.slabs), key=lambda x: x["from"])
    for a, b in zip(sl, sl[1:]):
        if a["to"] is None or a["to"] > b["from"]:
            raise HTTPException(422, "Slabs overlap. Each slab must end where the next one starts.")
    team = set(ctx.team_ids())
    p = ctx.db.get(IncentivePlan, id) if id else None
    if p and p.tenant_id != ctx.tid:
        raise HTTPException(404, "Plan not found.")
    p = p or IncentivePlan(tenant_id=ctx.tid)
    p.name, p.metric, p.slabs, p.user_ids, p.active = body.name, body.metric, sl, [u for u in body.user_ids if u in team], body.active
    ctx.db.add(p)
    ctx.db.commit()
    return to_dict(p)


@router.get("/incentives/plans")
def plans(ctx: Ctx = Depends(require("manager"))):
    return [to_dict(p) for p in ctx.db.scalars(select(IncentivePlan).where(IncentivePlan.tenant_id == ctx.tid, IncentivePlan.deleted_at.is_(None)).order_by(IncentivePlan.id))]


@router.delete("/incentives/plans/{pid}")
def del_plan(pid: int, ctx: Ctx = Depends(require("manager"))):
    get_or_404(ctx.db, IncentivePlan, pid, ctx.tid).deleted_at = utcnow()
    ctx.db.commit()
    return {"ok": True}


def _metric(ctx: Ctx, metric: str, uid: int, s, e) -> float:
    t = ctx.tid
    if metric == "revenue":  # value of deals won in the month
        kinds = [x["key"] for x in svc.stages_of(ctx.tenant) if x["kind"] == "won"]
        return float(ctx.db.scalar(select(func.coalesce(func.sum(Lead.value), 0)).where(Lead.tenant_id == t, Lead.owner_id == uid, Lead.status.in_(kinds), Lead.stage_changed_at >= s, Lead.stage_changed_at < e)) or 0)
    if metric == "orders":
        return float(ctx.db.scalar(select(func.coalesce(func.sum(SalesOrder.total), 0)).where(SalesOrder.tenant_id == t, SalesOrder.created_by == uid, SalesOrder.status != "cancelled", SalesOrder.created_at >= s, SalesOrder.created_at < e)) or 0)
    if metric == "collections":
        return float(ctx.db.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.tenant_id == t, Payment.user_id == uid, Payment.status == "verified", Payment.created_at >= s, Payment.created_at < e)) or 0)
    v = ctx.db.scalar(select(func.count()).where(Visit.tenant_id == t, Visit.user_id == uid, Visit.started_at >= s, Visit.started_at < e)) or 0
    m = ctx.db.scalar(select(func.count()).where(Meeting.tenant_id == t, Meeting.owner_id == uid, Meeting.status == "completed", Meeting.ended_at >= s, Meeting.ended_at < e)) or 0
    return float(v + m)


def payout(slabs: list[dict], value: float) -> tuple[float, dict | None]:
    for sl in slabs:
        if value >= sl["from"] and (sl["to"] is None or value < sl["to"]):
            return round(value * sl["pct"] / 100 + sl["flat"], 2), sl
    return 0.0, None


@router.get("/incentives/report")
def report(month: str | None = None, fmt: str = "json", ctx: Ctx = Depends(current)):
    month = month or local_today(ctx.tenant).strftime("%Y-%m")
    try:
        y, m = map(int, month.split("-"))
    except ValueError:
        raise HTTPException(422, "Month must be YYYY-MM.")
    s, _ = local_day_bounds_utc(ctx.tenant, date(y, m, 1))
    e, _ = local_day_bounds_utc(ctx.tenant, date(y + (m == 12), m % 12 + 1, 1))
    ids = set(ctx.team_ids() if ctx.is_at_least("manager") else [ctx.user.id])
    names = {u.id: u.name for u in ctx.db.scalars(select(User).where(User.tenant_id == ctx.tid))}
    rows = []
    for p in ctx.db.scalars(select(IncentivePlan).where(IncentivePlan.tenant_id == ctx.tid, IncentivePlan.deleted_at.is_(None), IncentivePlan.active.is_(True))):
        for uid in p.user_ids:
            if uid in ids:
                val = _metric(ctx, p.metric, uid, s, e)
                amt, sl = payout(p.slabs, val)
                rows.append({"user_id": uid, "name": names.get(uid), "plan": p.name, "metric": p.metric, "achieved": val, "slab": f"{sl['from']:g}-{'∞' if sl['to'] is None else format(sl['to'], 'g')} @ {sl['pct']:g}%" + (f" + {sl['flat']:g}" if sl["flat"] else "") if sl else "below first slab", "incentive": amt})
    rows.sort(key=lambda r: -r["incentive"])
    if fmt == "csv":
        return PlainTextResponse(csv_response_text([{k: r[k] for k in ("name", "plan", "metric", "achieved", "slab", "incentive")} for r in rows]), media_type="text/csv")
    return {"month": month, "rows": rows, "total": round(sum(r["incentive"] for r in rows), 2), "currency": ctx.setting("currency", "INR")}
