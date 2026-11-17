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
CATEGORIES = ("fuel", "travel", "taxi", "toll", "parking", "food", "lodging", "da", "other")
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
    if category == "fuel":
        auto = ctx.db.scalar(select(ExpenseClaim.id).where(ExpenseClaim.tenant_id == ctx.tid, ExpenseClaim.user_id == ctx.user.id, ExpenseClaim.day == d, ExpenseClaim.category == "fuel",
                                                           ExpenseClaim.source.in_(("auto", "edited")), ExpenseClaim.status != "rejected", ExpenseClaim.deleted_at.is_(None)))
        if auto:
            raise HTTPException(409, "A fuel claim for that day was already created automatically from your route. Ask your manager if it needs correcting.")
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
        raise HTTPException(422, "No tracked distance for that day. Fuel is calculated after you log out.")
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


class PolicyIn(BaseModel):
    mode: str = "off"                       # off | shadow | on
    include_estimated: bool = False
    default: dict = {}
    by_designation: dict = {}
    by_role: dict = {}
    by_vehicle: dict = {}


@router.get("/policy")
def get_policy(ctx: Ctx = Depends(require("admin"))):
    from ..routeday import POLICY_DEFAULT, policy_for
    pol = (ctx.tenant.settings or {}).get("expense_policy") or {}
    return {"mode": pol.get("mode", "off"), "include_estimated": bool(pol.get("include_estimated")), "default": {k: policy_for(ctx.tenant, type("U", (), {"designation_id": None})())[k] for k in POLICY_DEFAULT},
            "by_designation": pol.get("by_designation") or {}, "by_role": pol.get("by_role") or {}, "by_vehicle": pol.get("by_vehicle") or {}}


@router.put("/policy")
def put_policy(body: PolicyIn, ctx: Ctx = Depends(require("admin"))):
    from ..routeday import KEYS
    if body.mode not in ("off", "shadow", "on"):
        raise HTTPException(422, "Mode must be off, shadow or on.")
    def clean(d):
        out = {}
        for k, v in (d or {}).items():
            if k in KEYS and v not in (None, ""):
                try:
                    f = float(v)
                except (TypeError, ValueError):
                    raise HTTPException(422, f"{k} must be a number.")
                if f < 0 or f > 1_000_000:
                    raise HTTPException(422, f"{k} is out of range.")
                out[k] = f
        return out
    st = dict(ctx.tenant.settings or {})
    st["expense_policy"] = {"mode": body.mode, "include_estimated": body.include_estimated, "default": clean(body.default),
                            "by_designation": {str(k): clean(v) for k, v in (body.by_designation or {}).items() if str(k).isdigit()},
                            "by_role": {k: clean(v) for k, v in (body.by_role or {}).items() if k in ("employee", "manager", "admin")},
                            "by_vehicle": {k: clean(v) for k, v in (body.by_vehicle or {}).items() if k in ("bike", "car", "public", "other")}}
    ctx.tenant.settings = st
    from ..audit import audit
    audit(ctx, "expense.policy", "tenant", ctx.tid, f"mode={body.mode}")
    ctx.db.commit()
    return get_policy(ctx)


@router.get("/period")
def period(start: str | None = None, end: str | None = None, user_id: int | None = None, ctx: Ctx = Depends(current)):
    """Travel and expenses for a period, day by day: distance, visits, what the rules compute and what is already claimed. Defaults to my current month."""
    from datetime import timedelta
    from ..models import RouteDay, Visit, Customer
    from .. import routeday
    from ..common import tz_of
    uid = user_id or ctx.user.id
    if uid not in ctx.team_ids():
        raise HTTPException(403, "You can only view your own team.")
    today = local_today(ctx.tenant)
    d1 = parse_day(start, today.replace(day=1))
    d2 = parse_day(end, today)
    if d2 < d1 or (d2 - d1).days > 92:
        raise HTTPException(422, "Choose a period of at most 3 months.")
    user = ctx.db.get(User, uid)
    from ..common import local_day_bounds_utc
    s0, e0 = local_day_bounds_utc(ctx.tenant, d1)[0], local_day_bounds_utc(ctx.tenant, d2)[1]
    rds = {r.day: r for r in ctx.db.scalars(select(RouteDay).where(RouteDay.tenant_id == ctx.tid, RouteDay.user_id == uid, RouteDay.day >= d1, RouteDay.day <= d2))}
    from ..models import LocationPing
    ping_days = set()
    tz = tz_of(ctx.tenant)
    for (ts,) in ctx.db.execute(select(LocationPing.ts).where(LocationPing.tenant_id == ctx.tid, LocationPing.user_id == uid, LocationPing.ts >= s0, LocationPing.ts < e0, LocationPing.lat.is_not(None))):
        ping_days.add(ts.replace(tzinfo=__import__("datetime").timezone.utc).astimezone(tz).date())
    for d in sorted(ping_days - set(rds)):          # days with points but no summary yet (today, or the nightly job has not run)
        rd = routeday.build(ctx.db, ctx.tenant, uid, d)
        if rd:
            rds[d] = rd
    visits = ctx.db.execute(select(Visit, Customer.name).outerjoin(Customer, Customer.id == Visit.customer_id).where(
        Visit.tenant_id == ctx.tid, Visit.user_id == uid, Visit.started_at >= s0, Visit.started_at < e0)).all()
    vday: dict = {}
    for v, cn in visits:
        dd = v.started_at.replace(tzinfo=__import__("datetime").timezone.utc).astimezone(tz).date()
        vday.setdefault(dd, []).append({"id": v.id, "customer": cn or (v.prospect or {}).get("name"), "minutes": int(((v.ended_at or v.started_at) - v.started_at).total_seconds() // 60), "done": bool(v.ended_at), "outcome": v.outcome})
    claims: dict = {}
    items: dict = {}
    for c in ctx.db.scalars(select(ExpenseClaim).where(ExpenseClaim.tenant_id == ctx.tid, ExpenseClaim.user_id == uid, ExpenseClaim.day >= d1, ExpenseClaim.day <= d2,
                                                        ExpenseClaim.status != "rejected", ExpenseClaim.deleted_at.is_(None)).order_by(ExpenseClaim.id)):
        claims[c.day] = claims.get(c.day, 0) + c.amount
        items.setdefault(c.day, []).append({"id": c.id, "category": c.category, "amount": c.amount, "description": c.description, "status": c.status, "level": c.level, "source": c.source,
                                            "distance_km": c.distance_km, "calc_amount": c.calc_amount, "adjust_note": c.adjust_note, "receipt": c.receipt, "erp_ref": c.erp_ref})
    atts = {a.day: a for a in ctx.db.scalars(select(Attendance).where(Attendance.tenant_id == ctx.tid, Attendance.user_id == uid, Attendance.day >= d1, Attendance.day <= d2))}
    days, d = [], d1
    while d <= d2:
        rd, vs = rds.get(d), vday.get(d, [])
        if rd or vs or d in claims:
            calc = routeday.compute_expense(ctx.tenant, user, rd, atts.get(d)) if (rd or atts.get(d)) else None
            its = items.get(d, [])
            other = sum(i["amount"] for i in its if i["category"] not in ("fuel", "da"))
            days.append({"day": d.isoformat(), "km": rd.distance_km if rd else 0, "est_km": rd.est_km if rd else 0, "quality": rd.quality if rd else None, "visits": vs,
                         "calc": calc, "claimed": round(claims.get(d, 0), 2), "items": its, "other": round(other, 2)})
        d += timedelta(days=1)
    pol = routeday.policy_for(ctx.tenant, user)
    mode = pol["mode"]
    tot = {"km": round(sum(x["km"] for x in days), 1), "est_km": round(sum(x["est_km"] for x in days), 1), "visits": sum(len(x["visits"]) for x in days), "days": len([x for x in days if x["km"] or x["visits"]]),
           "calculated": round(sum((x["calc"] or {}).get("total", 0) for x in days), 2), "claimed": round(sum(x["claimed"] for x in days), 2),
           "other": round(sum(x["other"] for x in days), 2), "unclaimed_days": len([x for x in days if x["calc"] and x["calc"]["total"] > 0 and not any(i["category"] in ("fuel", "da") for i in x["items"])])}
    ctx.db.commit()
    return {"user": {"id": user.id, "name": user.name}, "me": uid == ctx.user.id, "start": d1.isoformat(), "end": d2.isoformat(), "mode": mode, "rate": pol["rate_per_km"], "cap": pol["daily_cap"], "currency": ctx.setting("currency", "INR"), "totals": tot, "days": days}


class DayTravelIn(BaseModel):
    day: date
    km: float | None = None            # distance to claim; default is the tracked distance
    amount: float | None = None        # travel amount; default is km x rate (with the rules' cap)
    allowance: float | None = None     # daily allowance; default is what the rules give
    reason: str = ""


@router.post("/day-travel")
def day_travel(body: DayTravelIn, ctx: Ctx = Depends(current)):
    """Claim (or change a still-pending claim for) one day's travel and allowance. Starts from what the rules calculated; anything changed by hand needs a reason."""
    from .. import routeday
    d = body.day
    if d > local_today(ctx.tenant):
        raise HTTPException(422, "You can't claim for a future date.")
    for v in (body.km, body.amount, body.allowance):
        if v is not None and (v < 0 or v > 10_000_000):
            raise HTTPException(422, "Enter amounts and distances of zero or more.")
    rd = routeday.build(ctx.db, ctx.tenant, ctx.user.id, d)
    att = ctx.db.scalar(select(Attendance).where(Attendance.tenant_id == ctx.tid, Attendance.user_id == ctx.user.id, Attendance.day == d, Attendance.deleted_at.is_(None)))
    calc = routeday.compute_expense(ctx.tenant, ctx.user, rd, att)
    pol = routeday.policy_for(ctx.tenant, ctx.user)
    km = calc["km"] if body.km is None else round(body.km, 2)
    fuel_rule, notes = routeday.fuel_for(pol, km)
    fuel = round(fuel_rule if body.amount is None else body.amount, 2)
    allowance = round(calc["allowance"] if body.allowance is None else body.allowance, 2)
    changed_fuel = abs(fuel - calc["fuel"]) > 0.005 or abs(km - calc["km"]) > 0.005
    changed_da = abs(allowance - calc["allowance"]) > 0.005
    reason = body.reason.strip()[:250]
    if (changed_fuel or changed_da) and len(reason) < 3:
        raise HTTPException(422, "You changed what the rules calculated. Please say why (a few words).")
    cur = ctx.setting("currency", "INR")
    saved = []
    for cat, amt, rule_amt, changed, text in (("fuel", fuel, calc["fuel"], changed_fuel, f"{km:g} km x {pol['rate_per_km']:g}/km"), ("da", allowance, calc["allowance"], changed_da, f"{calc['allowance_kind'] or 'daily'} allowance")):
        ex = ctx.db.scalar(select(ExpenseClaim).where(ExpenseClaim.tenant_id == ctx.tid, ExpenseClaim.user_id == ctx.user.id, ExpenseClaim.day == d, ExpenseClaim.category == cat,
                                                     ExpenseClaim.status != "rejected", ExpenseClaim.deleted_at.is_(None)))
        desc = (text + (f" · changed: {reason}" if changed else "") + (" · " + "; ".join(notes) if cat == "fuel" and notes and not changed else ""))[:500]
        if ex is not None:
            if abs(ex.amount - amt) < 0.005 and (cat != "fuel" or abs((ex.distance_km or 0) - km) < 0.005):
                continue
            if ex.status != "pending" or (ex.level or 0) > 0:
                raise HTTPException(409, f"The {'travel' if cat == 'fuel' else 'allowance'} claim for that day is already {ex.status if ex.status != 'pending' else 'partly approved'} and can't be changed. Ask your manager.")
            if amt <= 0:
                ex.deleted_at = utcnow()
                continue
            ex.amount, ex.description, ex.levels_required = amt, desc, _levels_for(ctx, amt)
            ex.distance_km = km if cat == "fuel" else None
            ex.calc_amount, ex.adjust_note = (rule_amt if changed else None), (reason if changed else "")
            ex.source = "edited" if changed else ex.source
            saved.append(ex)
            continue
        if amt <= 0:
            continue
        e = ExpenseClaim(tenant_id=ctx.tid, user_id=ctx.user.id, day=d, category=cat, amount=amt, currency=cur, description=desc, distance_km=km if cat == "fuel" else None, source="edited" if changed else "auto",
                         calc_amount=rule_amt if changed else None, adjust_note=reason if changed else "", levels_required=_levels_for(ctx, amt))
        ctx.db.add(e)
        saved.append(e)
    if saved and ctx.user.manager_id:
        notify(ctx.db, ctx.tid, ctx.user.manager_id, "Expense to approve", f"{ctx.user.name}: {cur} {sum(x.amount for x in saved):g} travel and allowance for {d:%d %b}", "approval")
    ctx.db.commit()
    return {"saved": [to_dict(x) for x in saved], "calc": calc}


class AdjustIn(BaseModel):
    amount: float
    note: str


@router.post("/{eid}/adjust")
def adjust(eid: int, body: AdjustIn, ctx: Ctx = Depends(require("manager"))):
    """An approver corrects the amount of a pending claim in their reporting line (the employee is told, and the original stays on record)."""
    e = get_or_404(ctx.db, ExpenseClaim, eid, ctx.tid)
    if e.user_id == ctx.user.id or e.user_id not in ctx.team_ids():
        raise HTTPException(403, "You can only adjust claims of people reporting to you.")
    if e.status != "pending":
        raise HTTPException(409, "Only claims that are still pending can be adjusted.")
    if body.amount <= 0 or body.amount > 10_000_000:
        raise HTTPException(422, "Enter an amount greater than zero.")
    if len(body.note.strip()) < 3:
        raise HTTPException(422, "Please say why you changed it.")
    old = e.amount
    if e.calc_amount is None:
        e.calc_amount = old
    e.amount, e.adjust_note = round(body.amount, 2), f"{ctx.user.name}: {body.note.strip()}"[:300]
    e.levels_required = max(e.levels_required or 1, _levels_for(ctx, e.amount))
    if e.source == "auto":
        e.source = "edited"
    notify(ctx.db, ctx.tid, e.user_id, "Expense amount changed", f"{e.currency} {old:g} -> {e.amount:g} ({e.category}): {body.note.strip()}", "approval")
    ctx.db.commit()
    return to_dict(e)


@router.get("/auto-preview")
def auto_preview(day: str | None = None, ctx: Ctx = Depends(current)):
    """What the expense rules computed from my route for a day (shown to me in shadow mode too, so I can compare with my own records)."""
    from ..models import RouteDay
    from .. import routeday
    d = parse_day(day, local_today(ctx.tenant))
    rd = routeday.build(ctx.db, ctx.tenant, ctx.user.id, d)
    if rd is None:
        return {"day": d.isoformat(), "found": False}
    att = ctx.db.scalar(select(Attendance).where(Attendance.tenant_id == ctx.tid, Attendance.user_id == ctx.user.id, Attendance.day == d))
    res = routeday.compute_expense(ctx.tenant, ctx.user, rd, att)
    mode = res["mode"]
    ctx.db.commit()
    return {"day": d.isoformat(), "found": True, "mode": mode, "tracked_km": rd.distance_km, "estimated_km": rd.est_km, "quality": rd.quality, "calc": res}


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
