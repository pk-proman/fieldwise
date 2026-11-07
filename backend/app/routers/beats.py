"""Beat plans (permanent journey plans): recurring, ordered customer visit schedules per field executive."""
from datetime import date, datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func, and_
from ..deps import Ctx, current, require
from ..db import SessionLocal
from ..models import Beat, BeatVisit, Customer, User, Visit, Holiday, LeaveRequest, Tenant, Branch, utcnow
from ..common import to_dict, get_or_404, local_today, parse_day
from ..geo import optimize_route, haversine_m
from ..notify import notify
from .visits import begin_visit
from .orders import outstanding_of

router = APIRouter(prefix="/beats", tags=["beats"])
HORIZON_DAYS = 14


# ------------------------------------------------------------------ generation
def _is_planned_day(beat: Beat, d: date) -> bool:
    return d >= beat.start_date and d.weekday() in (beat.days or []) and ((d - beat.start_date).days // 7) % max(beat.every_weeks, 1) == 0


def generate(db, tenant: Tenant, beat: Beat, frm: date, to: date) -> int:
    """Idempotently create the planned stops for a beat in [frm, to]; skips holidays and approved leave."""
    holidays = {h for (h,) in db.execute(select(Holiday.day).where(Holiday.tenant_id == tenant.id, Holiday.deleted_at.is_(None), Holiday.day >= frm, Holiday.day <= to))}
    leaves = db.execute(select(LeaveRequest.from_day, LeaveRequest.to_day).where(LeaveRequest.tenant_id == tenant.id, LeaveRequest.user_id == beat.user_id, LeaveRequest.status == "approved", LeaveRequest.to_day >= frm)).all()
    live = {c for (c,) in db.execute(select(Customer.id).where(Customer.tenant_id == tenant.id, Customer.id.in_(beat.customer_ids or [0]), Customer.deleted_at.is_(None)))}
    existing = {(b.customer_id, b.day) for b in db.scalars(select(BeatVisit).where(BeatVisit.beat_id == beat.id, BeatVisit.day >= frm, BeatVisit.day <= to))}
    n, d = 0, frm
    while d <= to:
        if _is_planned_day(beat, d) and d not in holidays and not any(a <= d <= b for a, b in leaves):
            for seq, cid in enumerate(beat.customer_ids or []):
                if cid in live and (cid, d) not in existing:
                    db.add(BeatVisit(tenant_id=tenant.id, beat_id=beat.id, user_id=beat.user_id, customer_id=cid, day=d, seq=seq))
                    n += 1
        d += timedelta(days=1)
    return n


def extend_all(db):
    for t in db.scalars(select(Tenant).where(Tenant.active.is_(True))).all():
        today = local_today(t)
        for b in db.scalars(select(Beat).where(Beat.tenant_id == t.id, Beat.active.is_(True), Beat.deleted_at.is_(None))):
            generate(db, t, b, today, today + timedelta(days=HORIZON_DAYS))
    db.commit()


def mark_missed(db):
    """Stops from earlier days that were never visited become 'missed'; each manager gets one summary."""
    for t in db.scalars(select(Tenant).where(Tenant.active.is_(True))).all():
        today = local_today(t)
        rows = db.scalars(select(BeatVisit).where(BeatVisit.tenant_id == t.id, BeatVisit.day < today, BeatVisit.status.in_(["planned", "in_progress"]))).all()
        per_user: dict = {}
        for r in rows:
            r.status = "missed"
            per_user[r.user_id] = per_user.get(r.user_id, 0) + 1
        for uid, n in per_user.items():
            u = db.get(User, uid)
            if u:
                notify(db, t.id, u.id, "Missed beat visits", f"{n} planned visit(s) were not completed.", "beat")
                if u.manager_id:
                    notify(db, t.id, u.manager_id, "Beat adherence alert", f"{u.name} missed {n} planned visit(s).", "beat")
    db.commit()


# ------------------------------------------------------------------ CRUD
class BeatIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    user_id: int
    days: list[int] = Field(min_length=1)
    every_weeks: int = Field(1, ge=1, le=4)
    start_date: date | None = None
    customer_ids: list[int] = Field(min_length=1, max_length=60)
    territory: str = ""
    active: bool = True


def _check(ctx: Ctx, body: BeatIn):
    if body.user_id not in ctx.team_ids():
        raise HTTPException(403, "You can only plan beats for people in your team.")
    if any(d not in range(7) for d in body.days):
        raise HTTPException(422, "Days must be 0 (Mon) to 6 (Sun).")
    if len(set(body.customer_ids)) != len(body.customer_ids):
        raise HTTPException(422, "A customer appears twice in this beat.")
    found = {c for (c,) in ctx.db.execute(select(Customer.id).where(Customer.tenant_id == ctx.tid, Customer.id.in_(body.customer_ids), Customer.deleted_at.is_(None)))}
    if found != set(body.customer_ids):
        raise HTTPException(422, "One of the customers doesn't exist.")


def _regen(ctx: Ctx, beat: Beat):
    today = local_today(ctx.tenant)
    ctx.db.query(BeatVisit).filter(BeatVisit.beat_id == beat.id, BeatVisit.day >= today, BeatVisit.status == "planned").delete()  # keep history and visited stops
    ctx.db.flush()
    if beat.active:
        generate(ctx.db, ctx.tenant, beat, today, today + timedelta(days=HORIZON_DAYS))


@router.post("")
def create_beat(body: BeatIn, ctx: Ctx = Depends(require("manager"))):
    _check(ctx, body)
    b = Beat(tenant_id=ctx.tid, name=body.name.strip(), user_id=body.user_id, days=sorted(set(body.days)), every_weeks=body.every_weeks, start_date=body.start_date or local_today(ctx.tenant),
             customer_ids=body.customer_ids, territory=body.territory, active=body.active, created_by=ctx.user.id)
    ctx.db.add(b)
    ctx.db.flush()
    _regen(ctx, b)
    if b.user_id != ctx.user.id:
        notify(ctx.db, ctx.tid, b.user_id, "New beat plan", f"{ctx.user.name} assigned you '{b.name}' ({len(b.customer_ids)} customers).", "beat")
    ctx.db.commit()
    return to_dict(b)


@router.patch("/{bid}")
def update_beat(bid: int, body: BeatIn, ctx: Ctx = Depends(require("manager"))):
    b = get_or_404(ctx.db, Beat, bid, ctx.tid)
    if b.user_id not in ctx.team_ids():
        raise HTTPException(403, "This beat belongs to someone outside your team.")
    _check(ctx, body)
    b.name, b.user_id, b.days, b.every_weeks, b.customer_ids, b.territory, b.active = body.name.strip(), body.user_id, sorted(set(body.days)), body.every_weeks, body.customer_ids, body.territory, body.active
    if body.start_date:
        b.start_date = body.start_date
    _regen(ctx, b)
    ctx.db.commit()
    return to_dict(b)


@router.delete("/{bid}")
def delete_beat(bid: int, ctx: Ctx = Depends(require("manager"))):
    b = get_or_404(ctx.db, Beat, bid, ctx.tid)
    if b.user_id not in ctx.team_ids():
        raise HTTPException(403, "This beat belongs to someone outside your team.")
    b.deleted_at, b.active = utcnow(), False
    today = local_today(ctx.tenant)
    ctx.db.query(BeatVisit).filter(BeatVisit.beat_id == b.id, BeatVisit.day >= today, BeatVisit.status == "planned").delete()
    ctx.db.commit()
    return {"ok": True}


@router.get("")
def list_beats(scope: str = "team", ctx: Ctx = Depends(current)):
    ids = [ctx.user.id] if scope == "mine" or not ctx.is_at_least("manager") else ctx.team_ids()
    users = {u.id: u.name for u in ctx.db.scalars(select(User).where(User.tenant_id == ctx.tid))}
    custs = {c.id: c.name for c in ctx.db.scalars(select(Customer).where(Customer.tenant_id == ctx.tid))}
    rows = ctx.db.scalars(select(Beat).where(Beat.tenant_id == ctx.tid, Beat.deleted_at.is_(None), Beat.user_id.in_(ids)).order_by(Beat.name)).all()
    return [{**to_dict(b), "user_name": users.get(b.user_id), "customers": [{"id": c, "name": custs.get(c, "?")} for c in b.customer_ids]} for b in rows]


class OptIn(BaseModel):
    customer_ids: list[int]
    user_id: int | None = None


@router.post("/optimize")
def optimize(body: OptIn, ctx: Ctx = Depends(require("manager"))):
    """Reorder stops to shorten travel (nearest-neighbour + 2-opt) starting from the executive's branch."""
    cs = {c.id: c for c in ctx.db.scalars(select(Customer).where(Customer.tenant_id == ctx.tid, Customer.id.in_(body.customer_ids or [0])))}
    mapped = [{"id": i, "lat": cs[i].lat, "lng": cs[i].lng} for i in body.customer_ids if i in cs and cs[i].lat is not None]
    unmapped = [i for i in body.customer_ids if i in cs and cs[i].lat is None]
    if len(mapped) < 2:
        raise HTTPException(422, "At least two customers need a map location to optimise the order.")
    u = ctx.db.get(User, body.user_id) if body.user_id else ctx.user
    br = ctx.db.get(Branch, u.branch_id) if u and u.branch_id else None
    start = (br.lat, br.lng) if br else (mapped[0]["lat"], mapped[0]["lng"])
    ordered, km = optimize_route(start, mapped)
    return {"customer_ids": [s["id"] for s in ordered] + unmapped, "distance_km": round(km * 1.3, 1), "unmapped": len(unmapped)}


# ------------------------------------------------------------------ the day's plan
@router.get("/day")
def day_plan(day: str | None = None, user_id: int | None = None, ctx: Ctx = Depends(current)):
    uid = user_id or ctx.user.id
    if uid not in ctx.team_ids():
        raise HTTPException(403, "You can only view your own team.")
    d = parse_day(day, local_today(ctx.tenant))
    rows = ctx.db.execute(select(BeatVisit, Customer, Beat.name).join(Customer, Customer.id == BeatVisit.customer_id).join(Beat, Beat.id == BeatVisit.beat_id).where(
        BeatVisit.tenant_id == ctx.tid, BeatVisit.user_id == uid, BeatVisit.day == d).order_by(Beat.name, BeatVisit.seq)).all()
    out = []
    for bv, c, bn in rows:
        out.append({**to_dict(bv), "beat_name": bn, "customer": {"id": c.id, "name": c.name, "address": c.address, "city": c.city, "phone": c.phone, "lat": c.lat, "lng": c.lng},
                    "outstanding": round(outstanding_of(ctx, c.id))})
    done = sum(1 for r in out if r["status"] == "visited")
    return {"day": d.isoformat(), "user_id": uid, "stops": out, "planned": len(out), "visited": done, "skipped": sum(1 for r in out if r["status"] == "skipped"),
            "adherence": round(done / len(out) * 100) if out else None}


def _bv(ctx: Ctx, bvid: int) -> BeatVisit:
    bv = get_or_404(ctx.db, BeatVisit, bvid, ctx.tid)
    if bv.user_id != ctx.user.id:
        raise HTTPException(403, "This stop belongs to another executive.")
    return bv


class StartIn(BaseModel):
    lat: float | None = None
    lng: float | None = None


@router.post("/visits/{bvid}/start")
def start_stop(bvid: int, body: StartIn, ctx: Ctx = Depends(current)):
    bv = _bv(ctx, bvid)
    if bv.status not in ("planned", "missed"):
        raise HTTPException(409, f"This stop is already {bv.status.replace('_', ' ')}.")
    if bv.day != local_today(ctx.tenant):
        raise HTTPException(409, "You can only start today's stops.")
    res = begin_visit(ctx, bv.customer_id, None, body.lat, body.lng)  # same geo-check + OTP as an ad-hoc visit
    bv.status, bv.visit_id = "in_progress", res["visit"]["id"]
    ctx.db.commit()
    return res


class SkipIn(BaseModel):
    reason: str = Field(min_length=3, max_length=200)


@router.post("/visits/{bvid}/skip")
def skip_stop(bvid: int, body: SkipIn, ctx: Ctx = Depends(current)):
    bv = _bv(ctx, bvid)
    if bv.status != "planned":
        raise HTTPException(409, f"This stop is already {bv.status.replace('_', ' ')}.")
    bv.status, bv.skip_reason = "skipped", body.reason.strip()
    c = ctx.db.get(Customer, bv.customer_id)
    if ctx.user.manager_id:
        notify(ctx.db, ctx.tid, ctx.user.manager_id, "Beat stop skipped", f"{ctx.user.name} skipped {c.name}: {body.reason}", "beat")
    ctx.db.commit()
    return to_dict(bv)


# ------------------------------------------------------------------ adherence & coverage
@router.get("/adherence")
def adherence(frm: str | None = None, to: str | None = None, ctx: Ctx = Depends(require("manager"))):
    today = local_today(ctx.tenant)
    d1, d2 = parse_day(frm, today - timedelta(days=30)), parse_day(to, today)
    ids = ctx.team_ids()
    rows = ctx.db.execute(select(BeatVisit.user_id, BeatVisit.beat_id, BeatVisit.status, func.count()).where(
        BeatVisit.tenant_id == ctx.tid, BeatVisit.user_id.in_(ids), BeatVisit.day >= d1, BeatVisit.day <= d2).group_by(BeatVisit.user_id, BeatVisit.beat_id, BeatVisit.status)).all()
    users = {u.id: u.name for u in ctx.db.scalars(select(User).where(User.tenant_id == ctx.tid))}
    beats = {b.id: b.name for b in ctx.db.scalars(select(Beat).where(Beat.tenant_id == ctx.tid))}
    mk = lambda: {"planned": 0, "visited": 0, "skipped": 0, "missed": 0, "open": 0}
    by_user, by_beat = {}, {}
    for uid, bid, st, n in rows:
        for bucket, key in ((by_user, uid), (by_beat, bid)):
            e = bucket.setdefault(key, mk())
            e["open" if st in ("planned", "in_progress") else st] += n
            if st != "in_progress" and not (st == "planned" and False):
                pass
            e["planned"] += n
    fin = lambda k, name, e: {"id": k, "name": name, **e, "adherence": round(e["visited"] / (e["planned"] - e["open"]) * 100) if e["planned"] - e["open"] else None}
    # coverage: customers on any active beat and when they were last visited
    on_beat = {c for b in ctx.db.scalars(select(Beat).where(Beat.tenant_id == ctx.tid, Beat.active.is_(True), Beat.deleted_at.is_(None), Beat.user_id.in_(ids))) for c in b.customer_ids}
    last = dict(ctx.db.execute(select(BeatVisit.customer_id, func.max(BeatVisit.day)).where(BeatVisit.tenant_id == ctx.tid, BeatVisit.status == "visited", BeatVisit.customer_id.in_(on_beat or [0])).group_by(BeatVisit.customer_id)).all())
    names = {c.id: c.name for c in ctx.db.scalars(select(Customer).where(Customer.tenant_id == ctx.tid, Customer.id.in_(on_beat or [0])))}
    cover = sorted(({"customer_id": c, "name": names.get(c, "?"), "last_visited": last[c].isoformat() if c in last else None, "days_since": (today - last[c]).days if c in last else None} for c in on_beat),
                   key=lambda r: (-(r["days_since"] if r["days_since"] is not None else 10_000)))
    tot = mk()
    for e in by_user.values():
        for k in tot:
            tot[k] += e[k]
    return {"from": d1.isoformat(), "to": d2.isoformat(), "total": fin(0, "All", tot), "by_user": sorted((fin(k, users.get(k, "?"), e) for k, e in by_user.items()), key=lambda r: (r["adherence"] is None, r["adherence"])),
            "by_beat": [fin(k, beats.get(k, "?"), e) for k, e in by_beat.items()], "coverage": cover[:100]}
