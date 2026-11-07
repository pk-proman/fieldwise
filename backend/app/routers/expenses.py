from datetime import date
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy import select, func
from ..deps import Ctx, current, require
from ..models import ExpenseClaim, Attendance, User, utcnow
from ..common import to_dict, get_or_404, local_today, save_upload, watermark_text, parse_day, csv_response_text
from ..notify import notify
from fastapi.responses import PlainTextResponse

router = APIRouter(prefix="/expenses", tags=["expenses"])
CATEGORIES = ("fuel", "travel", "food", "lodging", "da", "other")
RANK = {"employee": 1, "manager": 2, "admin": 3, "owner": 4}


def _levels_for(ctx, amount: float) -> int:
    """How many approval levels a claim of this size needs (workspace setting expense_levels, default one)."""
    lv = (ctx.tenant.settings or {}).get("expense_levels") or []
    n = 1
    for i, l in enumerate(lv[:-1]):
        if l["upto"] is not None and amount > l["upto"]:
            n = i + 2
    return min(n, max(len(lv), 1))
MAX_RECEIPT = 8 * 1024 * 1024


@router.post("")
async def create(day: str = Form(...), category: str = Form(...), amount: float = Form(...), description: str = Form(""),
                 lat: float | None = Form(None), lng: float | None = Form(None), receipt: UploadFile | None = File(None),
                 ctx: Ctx = Depends(current)):
    if category not in CATEGORIES:
        raise HTTPException(422, f"Category must be one of {', '.join(CATEGORIES)}.")
    if amount <= 0 or amount > 10_000_000:
        raise HTTPException(422, "Enter an amount greater than zero.")
    d = parse_day(day, local_today(ctx.tenant))
    if d > local_today(ctx.tenant):
        raise HTTPException(422, "Expense date cannot be in the future.")
    path = None
    if receipt and receipt.filename:
        raw = await receipt.read()
        if len(raw) > MAX_RECEIPT:
            raise HTTPException(413, "Receipt is larger than 8 MB.")
        ext = receipt.filename.rsplit(".", 1)[-1] if "." in receipt.filename else "jpg"
        path = save_upload(ctx.tid, raw, ext, watermark_text(ctx.user.name, lat, lng, ctx.tenant))
    e = ExpenseClaim(tenant_id=ctx.tid, user_id=ctx.user.id, day=d, category=category, amount=round(amount, 2), currency=ctx.setting("currency", "INR"),
                     description=description[:500], receipt=path)
    e.levels_required = _levels_for(ctx, e.amount)
    ctx.db.add(e)
    if ctx.user.manager_id:
        notify(ctx.db, ctx.tid, ctx.user.manager_id, "Expense to approve", f"{ctx.user.name}: {e.currency} {e.amount:g} ({category})", "approval")
    ctx.db.commit()
    return to_dict(e)


class FuelIn(BaseModel):
    day: date | None = None


@router.post("/fuel-auto")
def fuel_auto(body: FuelIn, ctx: Ctx = Depends(current)):
    """Fuel claim computed from GPS distance recorded on the attendance record."""
    d = body.day or local_today(ctx.tenant)
    att = ctx.db.scalar(select(Attendance).where(Attendance.tenant_id == ctx.tid, Attendance.user_id == ctx.user.id, Attendance.day == d))
    if not att or att.total_distance_km <= 0:
        raise HTTPException(422, "No tracked distance for that day. Fuel is calculated after you check out.")
    dup = ctx.db.scalar(select(ExpenseClaim.id).where(ExpenseClaim.tenant_id == ctx.tid, ExpenseClaim.user_id == ctx.user.id, ExpenseClaim.day == d,
                                                      ExpenseClaim.category == "fuel", ExpenseClaim.distance_km.is_not(None),
                                                      ExpenseClaim.status != "rejected", ExpenseClaim.deleted_at.is_(None)))
    if dup:
        raise HTTPException(409, "A fuel claim for that day already exists.")
    rate, cap = float(ctx.setting("fuel_rate_per_km", 6.0)), float(ctx.setting("fuel_daily_cap", 0) or 0)
    amount = att.total_distance_km * rate
    capped = bool(cap and amount > cap)
    amount = cap if capped else amount
    e = ExpenseClaim(tenant_id=ctx.tid, user_id=ctx.user.id, day=d, category="fuel", amount=round(amount, 2), currency=ctx.setting("currency", "INR"), levels_required=_levels_for(ctx, amount),
                     distance_km=att.total_distance_km, description=f"{att.total_distance_km:g} km x {rate:g}/km" + (" (daily cap applied)" if capped else ""))
    ctx.db.add(e)
    ctx.db.commit()
    return to_dict(e)


@router.get("")
def list_expenses(status: str | None = None, category: str | None = None, user_id: int | None = None, scope: str = "team",
                  frm: str | None = None, to: str | None = None, fmt: str = "json", ctx: Ctx = Depends(current)):
    ids = [ctx.user.id] if scope == "mine" else ctx.team_ids()
    if user_id and user_id in ids:
        ids = [user_id]
    q = select(ExpenseClaim, User.name).join(User, User.id == ExpenseClaim.user_id).where(
        ExpenseClaim.tenant_id == ctx.tid, ExpenseClaim.user_id.in_(ids), ExpenseClaim.deleted_at.is_(None))
    if status:
        q = q.where(ExpenseClaim.status == status)
    if category:
        q = q.where(ExpenseClaim.category == category)
    if frm:
        q = q.where(ExpenseClaim.day >= parse_day(frm, date.today()))
    if to:
        q = q.where(ExpenseClaim.day <= parse_day(to, date.today()))
    rows = [{**to_dict(e), "user_name": n} for e, n in ctx.db.execute(q.order_by(ExpenseClaim.id.desc()).limit(1000))]
    if fmt == "csv":
        return PlainTextResponse(csv_response_text([{k: r[k] for k in ("day", "user_name", "category", "amount", "currency", "distance_km", "status", "description")} for r in rows]), media_type="text/csv")
    return rows


class DecideIn(BaseModel):
    ids: list[int]
    decision: str
    note: str = ""


@router.post("/decide")
def decide(body: DecideIn, bg: BackgroundTasks, ctx: Ctx = Depends(require("manager"))):
    """Single or bulk approve / reject. Only people in the approver's reporting line can be decided on."""
    if body.decision not in ("approved", "rejected", "paid"):
        raise HTTPException(422, "Decision must be approved, rejected or paid.")
    team, done, skipped, awaiting = ctx.team_ids(), [], [], []
    levels = (ctx.tenant.settings or {}).get("expense_levels") or []
    for eid in body.ids[:500]:
        e = ctx.db.get(ExpenseClaim, eid)
        if not e or e.tenant_id != ctx.tid or e.deleted_at:
            continue
        if e.user_id == ctx.user.id or e.user_id not in team:
            skipped.append(eid)
            continue
        ok_from = {"approved": ["pending"], "rejected": ["pending"], "paid": ["approved"]}[body.decision]
        if e.status not in ok_from:
            skipped.append(eid)
            continue
        if body.decision == "approved":
            need = (levels[e.level]["role"] if e.level < len(levels) else "manager")
            if RANK[ctx.user.role] < RANK[need] or (any(a.get("by") == ctx.user.id for a in (e.approvals or [])) and ctx.user.role != "owner"):
                skipped.append(eid)  # needs a more senior approver (or a different person for the next level)
                continue
            e.approvals = [*(e.approvals or []), {"by": ctx.user.id, "name": ctx.user.name, "at": utcnow().isoformat() + "Z", "note": body.note[:200]}]
            e.level = (e.level or 0) + 1
            e.approver_id, e.decision_note = ctx.user.id, body.note[:300]
            if e.level < (e.levels_required or 1):
                nxt = levels[e.level]["role"] if e.level < len(levels) else "admin"
                for (uid,) in ctx.db.execute(select(User.id).where(User.tenant_id == ctx.tid, User.role.in_(["admin", "owner"] if nxt != "manager" else ["manager", "admin", "owner"]), User.active.is_(True))):
                    if uid != ctx.user.id:
                        notify(ctx.db, ctx.tid, uid, "Expense awaiting next approval", f"{e.currency} {e.amount:g} ({e.category}) is at level {e.level + 1} of {e.levels_required}.", "approval")
                notify(ctx.db, ctx.tid, e.user_id, "Expense partly approved", f"Level {e.level} of {e.levels_required} done.", "approval")
                awaiting.append(eid)
                continue
        e.status, e.approver_id, e.decision_note = body.decision, ctx.user.id, body.note[:300]
        notify(ctx.db, ctx.tid, e.user_id, f"Expense {body.decision}", f"{e.currency} {e.amount:g} ({e.category}) {body.note}", "approval")
        done.append(eid)
    ctx.db.commit()
    if body.decision == "approved" and (ctx.tenant.settings.get("erpnext") or {}).get("enabled"):
        from ..integrations.erpnext import push_expenses_task
        bg.add_task(push_expenses_task, ctx.tid, done)
    return {"updated": done, "skipped": skipped, "awaiting_next_level": awaiting}


@router.post("/{eid}/cancel")
def cancel(eid: int, ctx: Ctx = Depends(current)):
    e = get_or_404(ctx.db, ExpenseClaim, eid, ctx.tid)
    if e.user_id != ctx.user.id or e.status != "pending":
        raise HTTPException(403, "Only your own pending claims can be withdrawn.")
    e.deleted_at = utcnow()
    ctx.db.commit()
    return {"ok": True}
