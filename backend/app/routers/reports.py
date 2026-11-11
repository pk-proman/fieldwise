from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from ..deps import Ctx, current
from ..models import Attendance, LeaveRequest, ExpenseClaim, Lead, Task, User, Visit, LocationPing, utcnow
from ..common import local_today, local_day_bounds_utc

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/dashboard")
def dashboard(ctx: Ctx = Depends(current)):
    ids, today = ctx.team_ids(), local_today(ctx.tenant)
    db, t = ctx.db, ctx.tid
    att = db.execute(select(Attendance.status, func.count()).where(Attendance.tenant_id == t, Attendance.user_id.in_(ids), Attendance.day == today,
                                                                    Attendance.deleted_at.is_(None)).group_by(Attendance.status)).all()
    st = {k: v for k, v in att}
    headcount = db.scalar(select(func.count(User.id)).where(User.tenant_id == t, User.id.in_(ids), User.active.is_(True), User.deleted_at.is_(None)))
    checked_in = db.scalar(select(func.count()).where(Attendance.tenant_id == t, Attendance.user_id.in_(ids), Attendance.day == today,
                                                      Attendance.check_in_at.is_not(None), Attendance.check_out_at.is_(None)))
    km_today = db.scalar(select(func.coalesce(func.sum(Attendance.total_distance_km), 0)).where(Attendance.tenant_id == t, Attendance.user_id.in_(ids), Attendance.day == today))
    stages = db.execute(select(Lead.status, func.count(), func.coalesce(func.sum(Lead.value), 0)).where(
        Lead.tenant_id == t, Lead.deleted_at.is_(None), (Lead.owner_id.in_(ids)) | (Lead.owner_id.is_(None))).group_by(Lead.status)).all()
    s, e = local_day_bounds_utc(ctx.tenant, today)
    pend_leaves = db.scalar(select(func.count()).where(LeaveRequest.tenant_id == t, LeaveRequest.user_id.in_(ids), LeaveRequest.status == "pending", LeaveRequest.deleted_at.is_(None)))
    pend_exp = db.execute(select(func.count(), func.coalesce(func.sum(ExpenseClaim.amount), 0)).where(
        ExpenseClaim.tenant_id == t, ExpenseClaim.user_id.in_(ids), ExpenseClaim.status == "pending", ExpenseClaim.deleted_at.is_(None))).one()
    overdue = db.scalar(select(func.count()).where(Task.tenant_id == t, Task.assignee_id.in_(ids), Task.status != "done", Task.due_at < utcnow(), Task.deleted_at.is_(None)))
    open_tasks = db.scalar(select(func.count()).where(Task.tenant_id == t, Task.assignee_id.in_(ids), Task.status != "done", Task.deleted_at.is_(None)))
    visits_today = db.scalar(select(func.count()).where(Visit.tenant_id == t, Visit.user_id.in_(ids), Visit.started_at >= s, Visit.started_at < e))
    # 7-day attendance trend
    trend = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        n = db.scalar(select(func.count()).where(Attendance.tenant_id == t, Attendance.user_id.in_(ids), Attendance.day == d, Attendance.check_in_at.is_not(None)))
        trend.append({"day": d.isoformat(), "present": n})
    return {"headcount": headcount, "present": st.get("present", 0) + st.get("late", 0) + st.get("half_day", 0), "late": st.get("late", 0),
            "on_leave": st.get("on_leave", 0), "checked_in_now": checked_in, "km_today": round(km_today, 1), "visits_today": visits_today,
            "pending_leaves": pend_leaves, "pending_expenses": pend_exp[0], "pending_expense_amount": float(pend_exp[1]),
            "overdue_tasks": overdue, "open_tasks": open_tasks, "pipeline": [{"status": a, "count": b, "value": float(c)} for a, b, c in stages],
            "trend": trend, "currency": ctx.setting("currency", "INR")}


# ============================================================ Sales analytics, DCR, targets
from datetime import date, datetime
from fastapi import HTTPException
from sqlalchemy import and_
from ..deps import require
from ..models import Quotation, Followup, Meeting, Target, Activity, Customer, Tenant
from ..common import to_dict, parse_day, tz_of
from .. import svc


def _lead_scope(ctx):
    ids = ctx.team_ids()
    return [Lead.tenant_id == ctx.tid, Lead.deleted_at.is_(None), (Lead.owner_id.in_(ids)) | (Lead.owner_id.is_(None))]


@router.get("/forecast")
def forecast(ctx: Ctx = Depends(current)):
    probs = {x["key"]: x["prob"] for x in svc.stages_of(ctx.tenant)}
    leads = ctx.db.scalars(select(Lead).where(*_lead_scope(ctx))).all()
    users = {u.id: u.name for u in ctx.db.scalars(select(User).where(User.tenant_id == ctx.tid))}
    now = datetime.utcnow()
    kinds = {x["key"]: x["kind"] for x in svc.stages_of(ctx.tenant)}
    open_ = [l for l in leads if kinds.get(l.status, "open") == "open"]
    by_month, by_stage, by_owner = {}, {}, {}
    for l in open_:
        w = (l.value or 0) * probs.get(l.status, 0) / 100
        m = l.expected_close.strftime("%Y-%m") if l.expected_close else "unscheduled"
        for bucket, key in ((by_month, m), (by_stage, l.status), (by_owner, users.get(l.owner_id, "Unassigned"))):
            b = bucket.setdefault(key, {"count": 0, "value": 0.0, "weighted": 0.0})
            b["count"] += 1; b["value"] += l.value or 0; b["weighted"] += w
    won_m = sum(l.value or 0 for l in leads if kinds.get(l.status) == "won" and l.stage_changed_at and l.stage_changed_at.strftime("%Y-%m") == now.strftime("%Y-%m"))
    fmt = lambda d: [{"key": k, **{kk: round(vv) if kk != "count" else vv for kk, vv in v.items()}} for k, v in sorted(d.items())]
    return {"open_count": len(open_), "open_value": round(sum(l.value or 0 for l in open_)), "weighted": round(sum((l.value or 0) * probs.get(l.status, 0) / 100 for l in open_)),
            "commit": round(sum(l.value or 0 for l in open_ if l.status == "negotiation")), "won_this_month": round(won_m),
            "by_month": fmt(by_month), "by_stage": [x for x in fmt(by_stage)], "by_owner": sorted(fmt(by_owner), key=lambda x: -x["weighted"]), "probabilities": probs,
            "currency": ctx.setting("currency", "INR")}


@router.get("/funnel")
def funnel(days: int = 90, ctx: Ctx = Depends(current)):
    since = datetime.utcnow() - timedelta(days=min(max(days, 7), 730))
    leads = ctx.db.scalars(select(Lead).where(*_lead_scope(ctx), Lead.created_at >= since)).all()
    order = ["new", "contacted", "qualified", "proposal", "negotiation", "won"]
    rank = {s: i for i, s in enumerate(order)}
    # reached-stage approximation: a lead that is lost after qualification is counted as qualified
    reached = lambda l: rank.get(l.status, 2 if l.qualified_at else 0)
    stages = [{"stage": s, "count": sum(1 for l in leads if reached(l) >= i)} for i, s in enumerate(order)]
    won = [l for l in leads if l.status == "won"]; lost = [l for l in leads if l.status == "lost"]
    reasons = {}
    for l in lost:
        reasons[l.lost_reason or "—"] = reasons.get(l.lost_reason or "—", 0) + 1
    stale = [l for l in leads if l.status not in ("won", "lost") and (datetime.utcnow() - (l.stage_changed_at or l.created_at)).days > 14]
    by_source = {}
    for l in leads:
        b = by_source.setdefault(l.source or "Unknown", {"leads": 0, "won": 0}); b["leads"] += 1; b["won"] += l.status == "won"
    return {"total": len(leads), "stages": stages, "win_rate": round(len(won) / (len(won) + len(lost)) * 100) if (won or lost) else None,
            "avg_days_to_win": round(sum(((l.stage_changed_at or l.created_at) - l.created_at).days for l in won) / len(won), 1) if won else None,
            "lost_reasons": [{"reason": k, "count": v} for k, v in sorted(reasons.items(), key=lambda x: -x[1])], "stale": len(stale),
            "by_source": [{"source": k, **v} for k, v in sorted(by_source.items(), key=lambda x: -x[1]["leads"])]}


def dcr_data(db, tenant: Tenant, uid: int, day: date) -> dict:
    s, e = local_day_bounds_utc(tenant, day)
    t = tenant.id
    att = db.scalar(select(Attendance).where(Attendance.tenant_id == t, Attendance.user_id == uid, Attendance.day == day))
    fus = db.scalars(select(Followup).where(Followup.tenant_id == t, Followup.owner_id == uid, Followup.status == "done", Followup.completed_at >= s, Followup.completed_at < e)).all()
    productive = [f for f in fus if f.outcome in ("order_placed", "quotation_requested", "negotiation", "follow_up_required")]
    ms = db.scalars(select(Meeting).where(Meeting.tenant_id == t, Meeting.owner_id == uid, Meeting.status == "completed", Meeting.ended_at >= s, Meeting.ended_at < e)).all()
    visits = db.scalar(select(func.count()).where(Visit.tenant_id == t, Visit.user_id == uid, Visit.started_at >= s, Visit.started_at < e)) or 0
    leads_new = db.scalar(select(func.count()).where(Lead.tenant_id == t, Lead.owner_id == uid, Lead.created_at >= s, Lead.created_at < e)) or 0
    qs = db.execute(select(func.count(), func.coalesce(func.sum(Quotation.total), 0)).where(Quotation.tenant_id == t, Quotation.created_by == uid, Quotation.created_at >= s, Quotation.created_at < e)).one()
    exp = db.scalar(select(func.coalesce(func.sum(ExpenseClaim.amount), 0)).where(ExpenseClaim.tenant_id == t, ExpenseClaim.user_id == uid, ExpenseClaim.day == day)) or 0
    tasks_done = db.scalar(select(func.count()).where(Task.tenant_id == t, Task.assignee_id == uid, Task.completed_at >= s, Task.completed_at < e)) or 0
    overdue = db.scalar(select(func.count()).where(Followup.tenant_id == t, Followup.owner_id == uid, Followup.status == "pending", Followup.due_at < datetime.utcnow(), Followup.deleted_at.is_(None))) or 0
    from ..models import SalesOrder, Payment
    ob = db.execute(select(func.count(), func.coalesce(func.sum(SalesOrder.total), 0)).where(SalesOrder.tenant_id == t, SalesOrder.created_by == uid, SalesOrder.status != "cancelled", SalesOrder.created_at >= s, SalesOrder.created_at < e)).one()
    coll = db.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.tenant_id == t, Payment.user_id == uid, Payment.day == day, Payment.status != "rejected")) or 0
    u = db.get(User, uid)
    return {"orders_booked": ob[0], "order_value": float(ob[1]), "collected": float(coll), "user_id": uid, "name": u.name, "day": day.isoformat(), "status": att.status if att else "absent", "check_in": att.check_in_at.isoformat() + "Z" if att and att.check_in_at else None,
            "check_out": att.check_out_at.isoformat() + "Z" if att and att.check_out_at else None, "km": att.total_distance_km if att else 0, "visits": visits, "meetings": len(ms),
            "calls_done": len(fus), "productive_calls": len(productive), "orders": sum(1 for f in fus if f.outcome == "order_placed") + sum(1 for m in ms if m.outcome == "order_placed"),
            "new_leads": leads_new, "quotations": qs[0], "quoted_value": float(qs[1]), "expenses": float(exp), "tasks_done": tasks_done, "overdue_followups": overdue}


@router.get("/dcr")
def dcr(user_id: int | None = None, day: str | None = None, ctx: Ctx = Depends(current)):
    d = parse_day(day, local_today(ctx.tenant))
    ids = ctx.team_ids()
    if user_id:
        if user_id not in ids:
            raise HTTPException(403, "You can only view your own team.")
        ids = [user_id]
    elif not ctx.is_at_least("manager"):
        ids = [ctx.user.id]
    users = ctx.db.scalars(select(User).where(User.tenant_id == ctx.tid, User.id.in_(ids), User.active.is_(True), User.deleted_at.is_(None), User.role != "owner")).all()
    return [dcr_data(ctx.db, ctx.tenant, u.id, d) for u in users]


class TargetIn(BaseModel):
    user_id: int
    month: str
    revenue: float = 0
    visits: int = 0
    new_leads: int = 0


@router.put("/targets")
def set_target(body: TargetIn, ctx: Ctx = Depends(require("manager"))):
    if body.user_id not in ctx.team_ids():
        raise HTTPException(403, "You can only set targets for your team.")
    try:
        datetime.strptime(body.month, "%Y-%m")
    except ValueError:
        raise HTTPException(422, "Month must be YYYY-MM.")
    t = ctx.db.scalar(select(Target).where(Target.tenant_id == ctx.tid, Target.user_id == body.user_id, Target.month == body.month))
    if not t:
        t = Target(tenant_id=ctx.tid, user_id=body.user_id, month=body.month)
        ctx.db.add(t)
    t.revenue, t.visits, t.new_leads = body.revenue, body.visits, body.new_leads
    ctx.db.commit()
    return to_dict(t)


@router.get("/targets")
def targets(month: str | None = None, ctx: Ctx = Depends(current)):
    month = month or local_today(ctx.tenant).strftime("%Y-%m")
    y, m = map(int, month.split("-"))
    first = date(y, m, 1); nxt = date(y + (m == 12), m % 12 + 1, 1)
    s, _ = local_day_bounds_utc(ctx.tenant, first); e, _ = local_day_bounds_utc(ctx.tenant, nxt)
    ids = ctx.team_ids() if ctx.is_at_least("manager") else [ctx.user.id]
    out = []
    for u in ctx.db.scalars(select(User).where(User.tenant_id == ctx.tid, User.id.in_(ids), User.active.is_(True), User.deleted_at.is_(None), User.role != "owner")):
        tg = ctx.db.scalar(select(Target).where(Target.tenant_id == ctx.tid, Target.user_id == u.id, Target.month == month))
        rev = ctx.db.scalar(select(func.coalesce(func.sum(Lead.value), 0)).where(Lead.tenant_id == ctx.tid, Lead.owner_id == u.id, Lead.status == "won", Lead.stage_changed_at >= s, Lead.stage_changed_at < e)) or 0
        vis = ctx.db.scalar(select(func.count()).where(Visit.tenant_id == ctx.tid, Visit.user_id == u.id, Visit.started_at >= s, Visit.started_at < e)) or 0
        ms = ctx.db.scalar(select(func.count()).where(Meeting.tenant_id == ctx.tid, Meeting.owner_id == u.id, Meeting.status == "completed", Meeting.ended_at >= s, Meeting.ended_at < e)) or 0
        nl = ctx.db.scalar(select(func.count()).where(Lead.tenant_id == ctx.tid, Lead.owner_id == u.id, Lead.created_at >= s, Lead.created_at < e)) or 0
        out.append({"user_id": u.id, "name": u.name, "target_revenue": tg.revenue if tg else 0, "revenue": float(rev), "target_visits": tg.visits if tg else 0, "visits": vis + ms,
                    "target_leads": tg.new_leads if tg else 0, "new_leads": nl})
    out.sort(key=lambda r: -r["revenue"])
    return {"month": month, "rows": out, "currency": ctx.setting("currency", "INR")}


# ============================================================ overview (month-on-month, by user / manager)
def _subtree(ctx, root: int) -> set[int]:
    return ctx.subtree_ids(root)


@router.get("/overview")
def overview(user_ids: str = "", manager_id: int | None = None, months: int = 6, ctx: Ctx = Depends(current)):
    from ..models import SalesOrder, Customer, Payment
    team = set(ctx.team_ids())
    ids = set(team)
    if manager_id:
        if manager_id not in team:
            raise HTTPException(403, "You can only view your own team.")
        ids = _subtree(ctx, manager_id) & team
    if user_ids:
        want = {int(x) for x in user_ids.split(",") if x.strip().isdigit()}
        ids = ids & want if want else ids
    ids = ids or {ctx.user.id}
    t, db = ctx.tid, ctx.db
    kinds = {x["key"]: x["kind"] for x in svc.stages_of(ctx.tenant)}
    won_keys = [k for k, v in kinds.items() if v == "won"]
    lost_keys = [k for k, v in kinds.items() if v == "lost"]
    today = local_today(ctx.tenant)
    series = []
    for i in range(min(max(months, 2), 12) - 1, -1, -1):
        y, m = today.year, today.month - i
        while m <= 0:
            y, m = y - 1, m + 12
        first = date(y, m, 1)
        nxt = date(y + (m == 12), m % 12 + 1, 1)
        s, _ = local_day_bounds_utc(ctx.tenant, first)
        e, _ = local_day_bounds_utc(ctx.tenant, nxt)
        newl = db.scalar(select(func.count()).where(Lead.tenant_id == t, Lead.owner_id.in_(ids), Lead.created_at >= s, Lead.created_at < e, Lead.deleted_at.is_(None))) or 0
        orders = db.execute(select(func.count(), func.coalesce(func.sum(SalesOrder.total), 0)).where(SalesOrder.tenant_id == t, SalesOrder.created_by.in_(ids), SalesOrder.status != "cancelled", SalesOrder.created_at >= s, SalesOrder.created_at < e)).one()
        won = db.execute(select(func.count(), func.coalesce(func.sum(Lead.value), 0)).where(Lead.tenant_id == t, Lead.owner_id.in_(ids), Lead.status.in_(won_keys or ["won"]), Lead.stage_changed_at >= s, Lead.stage_changed_at < e)).one()
        newc = db.scalar(select(func.count()).where(Customer.tenant_id == t, Customer.owner_id.in_(ids), Customer.created_at >= s, Customer.created_at < e, Customer.deleted_at.is_(None))) or 0
        coll = db.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.tenant_id == t, Payment.user_id.in_(ids), Payment.status == "verified", Payment.created_at >= s, Payment.created_at < e)) or 0
        series.append({"month": first.strftime("%Y-%m"), "label": first.strftime("%b %y"), "new_leads": newl, "orders": orders[0], "order_value": float(orders[1]), "won_deals": won[0], "won_value": float(won[1]), "new_customers": newc, "collections": float(coll)})
    cur, prev = series[-1], series[-2]
    pct = lambda a, b: None if not b else round((a - b) / b * 100)
    open_v = db.scalar(select(func.coalesce(func.sum(Lead.value), 0)).where(Lead.tenant_id == t, Lead.owner_id.in_(ids), Lead.deleted_at.is_(None), Lead.status.notin_(won_keys + lost_keys))) or 0
    lost_v = db.scalar(select(func.coalesce(func.sum(Lead.value), 0)).where(Lead.tenant_id == t, Lead.owner_id.in_(ids), Lead.deleted_at.is_(None), Lead.status.in_(lost_keys or ["lost"]))) or 0
    won_v = db.scalar(select(func.coalesce(func.sum(Lead.value), 0)).where(Lead.tenant_id == t, Lead.owner_id.in_(ids), Lead.deleted_at.is_(None), Lead.status.in_(won_keys or ["won"]))) or 0
    stages = db.execute(select(Lead.status, func.count(), func.coalesce(func.sum(Lead.value), 0)).where(Lead.tenant_id == t, Lead.owner_id.in_(ids), Lead.deleted_at.is_(None)).group_by(Lead.status)).all()
    names = {u.id: u.name for u in db.scalars(select(User).where(User.tenant_id == t, User.id.in_(team)))}
    return {"series": series, "compare": {k: {"value": cur[k], "prev": prev[k], "change_pct": pct(cur[k], prev[k])} for k in ("new_leads", "order_value", "won_value", "new_customers", "collections", "orders")},
            "opportunity": {"open": float(open_v), "won": float(won_v), "lost": float(lost_v)}, "pipeline": [{"status": a, "label": (next((x["label"] for x in svc.stages_of(ctx.tenant) if x["key"] == a), a)), "count": b, "value": float(c)} for a, b, c in stages],
            "users": [{"id": u.id, "name": u.name, "manager_id": u.manager_id} for u in db.scalars(select(User).where(User.tenant_id == t, User.id.in_(team)))], "selected": sorted(ids), "currency": ctx.setting("currency", "INR")}
