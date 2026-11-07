from datetime import date, timedelta
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from ..deps import Ctx, current, require
from ..models import LeaveType, LeaveRequest, Holiday, User, Attendance, utcnow
from ..common import to_dict, get_or_404, local_today
from ..notify import notify

router = APIRouter(prefix="/leaves", tags=["leaves"])


def working_days(ctx: Ctx, d1: date, d2: date) -> float:
    off = set(ctx.setting("weekly_off", [6]))
    hol = {h for (h,) in ctx.db.execute(select(Holiday.day).where(Holiday.tenant_id == ctx.tid, Holiday.deleted_at.is_(None),
                                                                  Holiday.day >= d1, Holiday.day <= d2))}
    n, d = 0, d1
    while d <= d2:
        if d.weekday() not in off and d not in hol:
            n += 1
        d += timedelta(days=1)
    return float(n)


@router.get("/types")
def types(ctx: Ctx = Depends(current)):
    return [to_dict(t) for t in ctx.db.scalars(select(LeaveType).where(LeaveType.tenant_id == ctx.tid, LeaveType.deleted_at.is_(None)))]


@router.post("/types")
def add_type(body: dict, ctx: Ctx = Depends(require("admin"))):
    t = LeaveType(tenant_id=ctx.tid, name=str(body.get("name", "")).strip(), days_per_year=float(body.get("days_per_year", 12)), paid=bool(body.get("paid", True)))
    if not t.name:
        raise HTTPException(422, "Leave type name is required.")
    ctx.db.add(t)
    ctx.db.commit()
    return to_dict(t)


@router.delete("/types/{tid}")
def del_type(tid: int, ctx: Ctx = Depends(require("admin"))):
    get_or_404(ctx.db, LeaveType, tid, ctx.tid).deleted_at = utcnow()
    ctx.db.commit()
    return {"ok": True}


@router.get("/holidays")
def holidays(ctx: Ctx = Depends(current)):
    return [to_dict(h) for h in ctx.db.scalars(select(Holiday).where(Holiday.tenant_id == ctx.tid, Holiday.deleted_at.is_(None)).order_by(Holiday.day))]


@router.post("/holidays")
def add_holiday(body: dict, ctx: Ctx = Depends(require("admin"))):
    try:
        h = Holiday(tenant_id=ctx.tid, day=date.fromisoformat(body["day"]), name=str(body["name"]).strip(), country=body.get("country", "All"))
    except (KeyError, ValueError):
        raise HTTPException(422, "Holiday needs a name and a date (YYYY-MM-DD).")
    ctx.db.add(h)
    ctx.db.commit()
    return to_dict(h)


@router.delete("/holidays/{hid}")
def del_holiday(hid: int, ctx: Ctx = Depends(require("admin"))):
    get_or_404(ctx.db, Holiday, hid, ctx.tid).deleted_at = utcnow()
    ctx.db.commit()
    return {"ok": True}


def _used(ctx: Ctx, uid: int, type_id: int, year: int) -> float:
    return float(ctx.db.scalar(select(func.coalesce(func.sum(LeaveRequest.days), 0)).where(
        LeaveRequest.tenant_id == ctx.tid, LeaveRequest.user_id == uid, LeaveRequest.leave_type_id == type_id,
        LeaveRequest.status.in_(["approved", "pending"]), LeaveRequest.deleted_at.is_(None),
        LeaveRequest.from_day >= date(year, 1, 1), LeaveRequest.from_day <= date(year, 12, 31))) or 0)


@router.get("/balance")
def balance(user_id: int | None = None, ctx: Ctx = Depends(current)):
    uid = user_id or ctx.user.id
    if uid not in ctx.team_ids():
        raise HTTPException(403, "You can only view your own team.")
    y = local_today(ctx.tenant).year
    return [{"leave_type_id": t.id, "name": t.name, "allocated": t.days_per_year, "used": _used(ctx, uid, t.id, y),
             "balance": t.days_per_year - _used(ctx, uid, t.id, y)}
            for t in ctx.db.scalars(select(LeaveType).where(LeaveType.tenant_id == ctx.tid, LeaveType.deleted_at.is_(None)))]


class ApplyIn(BaseModel):
    leave_type_id: int
    from_day: date
    to_day: date
    reason: str = ""


@router.post("")
def apply(body: ApplyIn, ctx: Ctx = Depends(current)):
    if body.to_day < body.from_day:
        raise HTTPException(422, "The end date is before the start date.")
    lt = get_or_404(ctx.db, LeaveType, body.leave_type_id, ctx.tid)
    days = working_days(ctx, body.from_day, body.to_day)
    if days <= 0:
        raise HTTPException(422, "Those dates are all holidays or weekly offs.")
    clash = ctx.db.scalar(select(LeaveRequest.id).where(
        LeaveRequest.tenant_id == ctx.tid, LeaveRequest.user_id == ctx.user.id, LeaveRequest.status.in_(["pending", "approved"]),
        LeaveRequest.deleted_at.is_(None), LeaveRequest.from_day <= body.to_day, LeaveRequest.to_day >= body.from_day))
    if clash:
        raise HTTPException(409, "You already have leave overlapping these dates.")
    left = lt.days_per_year - _used(ctx, ctx.user.id, lt.id, body.from_day.year)
    if lt.paid and days > left:
        raise HTTPException(422, f"Only {left:g} day(s) of {lt.name} left; you asked for {days:g}.")
    r = LeaveRequest(tenant_id=ctx.tid, user_id=ctx.user.id, leave_type_id=lt.id, from_day=body.from_day, to_day=body.to_day, days=days, reason=body.reason)
    ctx.db.add(r)
    if ctx.user.manager_id:
        notify(ctx.db, ctx.tid, ctx.user.manager_id, "Leave request", f"{ctx.user.name}: {lt.name} {body.from_day} to {body.to_day} ({days:g} d)", "approval")
    ctx.db.commit()
    return to_dict(r)


@router.get("")
def list_leaves(status: str | None = None, scope: str = "team", ctx: Ctx = Depends(current)):
    ids = [ctx.user.id] if scope == "mine" else ctx.team_ids()
    q = select(LeaveRequest, User.name, LeaveType.name).join(User, User.id == LeaveRequest.user_id).join(LeaveType, LeaveType.id == LeaveRequest.leave_type_id).where(
        LeaveRequest.tenant_id == ctx.tid, LeaveRequest.user_id.in_(ids), LeaveRequest.deleted_at.is_(None))
    if status:
        q = q.where(LeaveRequest.status == status)
    return [{**to_dict(r), "user_name": un, "type_name": tn} for r, un, tn in ctx.db.execute(q.order_by(LeaveRequest.id.desc()).limit(300))]


class DecideIn(BaseModel):
    decision: str  # approved|rejected
    note: str = ""


@router.post("/{lid}/decide")
def decide(lid: int, body: DecideIn, ctx: Ctx = Depends(require("manager"))):
    if body.decision not in ("approved", "rejected"):
        raise HTTPException(422, "Decision must be approved or rejected.")
    r = get_or_404(ctx.db, LeaveRequest, lid, ctx.tid)
    if r.user_id == ctx.user.id:
        raise HTTPException(403, "You cannot approve your own leave.")
    if r.user_id not in ctx.team_ids():
        raise HTTPException(403, "This person is not in your reporting line.")
    if r.status != "pending":
        raise HTTPException(409, f"This request is already {r.status}.")
    r.status, r.approver_id, r.decision_note = body.decision, ctx.user.id, body.note[:300]
    if body.decision == "approved":
        d = r.from_day
        while d <= r.to_day:
            if d.weekday() not in set(ctx.setting("weekly_off", [6])):
                ex = ctx.db.scalar(select(Attendance).where(Attendance.tenant_id == ctx.tid, Attendance.user_id == r.user_id, Attendance.day == d))
                if not ex:
                    ctx.db.add(Attendance(tenant_id=ctx.tid, user_id=r.user_id, day=d, status="on_leave"))
            d += timedelta(days=1)
    notify(ctx.db, ctx.tid, r.user_id, f"Leave {body.decision}", f"{r.from_day} to {r.to_day}. {body.note}", "approval")
    ctx.db.commit()
    return to_dict(r)


@router.post("/{lid}/cancel")
def cancel(lid: int, ctx: Ctx = Depends(current)):
    r = get_or_404(ctx.db, LeaveRequest, lid, ctx.tid)
    if r.user_id != ctx.user.id or r.status != "pending":
        raise HTTPException(403, "Only your own pending requests can be cancelled.")
    r.status = "cancelled"
    ctx.db.commit()
    return to_dict(r)
