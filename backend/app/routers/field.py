"""Field-force intelligence: work-hours analysis, stops, productivity, anomalies, territories & geofence events, shifts & roster, dispatch."""
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from ..deps import Ctx, current, require
from ..models import (Attendance, LocationPing, Customer, Branch, Territory, GeoEvent, Shift, ShiftRoster, ShiftSwap, Task, User, Visit, Holiday, LeaveRequest, BeatVisit, Tenant, utcnow)
from ..common import to_dict, get_or_404, local_today, parse_day, local_day_bounds_utc, tz_of
from ..geo import haversine_m
from ..notify import notify

router = APIRouter(prefix="/field", tags=["field"])

STOP_RADIUS_M = 80       # points within this of the cluster centre belong to the same stop
STOP_MIN_MIN = 8         # stationary at least this long = a stop
MOVING_MPS = 1.2         # faster than ~4 km/h counts as travelling
GAP_MIN = 20             # silence longer than this is a tracking gap
TELEPORT_KMH = 250


# ================================================================== analysis
def _pl(p):  # ping -> (lat, lng)
    return p.lat, p.lng


def analyze(pings: list, start: datetime, end: datetime, customers: list, branches: list, shift_end: datetime | None = None, break_min: int = 0, radius_default: int = 100) -> dict:
    """Turns raw breadcrumbs into: travel / on-site / idle / gap minutes, stops with places, and anomalies. All datetimes naive UTC."""
    pts = [p for p in pings if p.lat is not None and not p.mock]
    pts.sort(key=lambda p: p.ts)
    span_min = max(0.0, (end - start).total_seconds() / 60)
    travel = gaps = 0.0
    stops, cur, anomalies, km = [], None, [], 0.0
    prev = None
    for p in pts:
        if prev:
            dt = (p.ts - prev.ts).total_seconds()
            d = haversine_m(prev.lat, prev.lng, p.lat, p.lng)
            if dt > 0:
                if dt / 60 > GAP_MIN:
                    gaps += dt / 60
                    anomalies.append({"type": "tracking_gap", "at": prev.ts.isoformat() + "Z", "minutes": round(dt / 60), "detail": "No location for %d min" % (dt / 60)})
                else:
                    if d / dt >= MOVING_MPS:
                        travel += dt / 60
                    if d > 15:
                        km += d / 1000
                if d / dt * 3.6 > TELEPORT_KMH and d > 800:
                    anomalies.append({"type": "teleport", "at": p.ts.isoformat() + "Z", "detail": "Jumped %.1f km in %d s (%.0f km/h)" % (d / 1000, dt, d / dt * 3.6)})
        # stop clustering on consecutive points
        if cur and haversine_m(cur["lat"], cur["lng"], p.lat, p.lng) <= STOP_RADIUS_M:
            cur["n"] += 1
            cur["lat"] += (p.lat - cur["lat"]) / cur["n"]
            cur["lng"] += (p.lng - cur["lng"]) / cur["n"]
            cur["end"] = p.ts
        else:
            if cur:
                stops.append(cur)
            cur = {"lat": p.lat, "lng": p.lng, "n": 1, "start": p.ts, "end": p.ts}
        prev = p
    if cur:
        stops.append(cur)
    out_stops, on_site = [], 0.0
    for s in stops:
        mins = (s["end"] - s["start"]).total_seconds() / 60
        if mins < STOP_MIN_MIN:
            continue
        place, kind = None, "other"
        for c in (customers(s["lat"], s["lng"]) if callable(customers) else customers):
            if c.lat is not None and haversine_m(s["lat"], s["lng"], c.lat, c.lng) <= max(radius_default, 120):
                place, kind = c.name, "customer"
                break
        if not place:
            for b in branches:
                if haversine_m(s["lat"], s["lng"], b.lat, b.lng) <= max(b.radius_m, 100):
                    place, kind = b.name, "branch"
                    break
        if kind == "customer":
            on_site += mins
        out_stops.append({"lat": round(s["lat"], 6), "lng": round(s["lng"], 6), "start": s["start"].isoformat() + "Z", "end": s["end"].isoformat() + "Z", "minutes": round(mins), "place": place, "kind": kind})
    idle = sum(s["minutes"] for s in out_stops if s["kind"] == "other")
    at_branch = sum(s["minutes"] for s in out_stops if s["kind"] == "branch")
    worked = max(0.0, span_min - break_min)
    overtime = max(0.0, (end - shift_end).total_seconds() / 60) if shift_end and end > shift_end else 0.0
    mock = sum(1 for p in pings if p.mock)
    off = sum(1 for p in pings if not p.gps_on)
    if mock:
        anomalies.append({"type": "mock_location", "detail": f"{mock} fake-location points"})
    if off >= 3:
        anomalies.append({"type": "gps_off", "detail": f"GPS reported off {off} times"})
    return {"span_min": round(span_min), "worked_min": round(worked), "travel_min": round(travel), "on_site_min": round(on_site), "at_branch_min": round(at_branch), "idle_min": round(idle), "gap_min": round(gaps),
            "distance_km": round(km, 2), "overtime_min": round(overtime), "stops": out_stops, "anomalies": anomalies, "points": len(pts),
            "productive_pct": round(on_site / span_min * 100) if span_min else 0}


def shift_for(db, tenant: Tenant, user: User, day: date) -> dict:
    """Resolve the shift for a person on a day: roster override > assigned shift > workspace default. Returns {start,end,grace,break,off}."""
    s = tenant.settings or {}
    default = {"name": "Default", "start": s.get("work_start", "09:30"), "end": s.get("work_end", "18:30"), "grace": s.get("late_grace_min", 15), "break": 60, "off": day.weekday() in set(s.get("weekly_off", [6]))}
    r = db.scalar(select(ShiftRoster).where(ShiftRoster.tenant_id == tenant.id, ShiftRoster.user_id == user.id, ShiftRoster.day == day))
    if r is not None:
        if r.shift_id is None:
            return {**default, "off": True, "name": "Day off"}
        sh = db.get(Shift, r.shift_id)
        if sh:
            return {"name": sh.name, "start": sh.start, "end": sh.end, "grace": sh.grace_min, "break": sh.break_min, "off": False}
    if user.shift_id:
        sh = db.get(Shift, user.shift_id)
        if sh and not sh.deleted_at:
            return {"name": sh.name, "start": sh.start, "end": sh.end, "grace": sh.grace_min, "break": sh.break_min, "off": day.weekday() in set(sh.weekly_off or [])}
    return default


def _shift_end_utc(tenant, day: date, shift: dict) -> datetime:
    h, m = map(int, shift["end"].split(":"))
    tz = tz_of(tenant)
    return datetime(day.year, day.month, day.day, h, m, tzinfo=tz).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def analyze_session(db, tenant: Tenant, a: Attendance, user: User | None = None, pre: dict | None = None) -> dict:
    """`pre` (from bulk_context) lets reports analyse hundreds of sessions with a handful of queries instead of several per session."""
    user = user or db.get(User, a.user_id)
    end = a.check_out_at or utcnow()
    if pre is not None:
        import bisect
        arr, keys = pre["pings"].get(a.user_id, ([], []))
        pings = arr[bisect.bisect_left(keys, a.check_in_at):bisect.bisect_right(keys, end)]
        custs, branches, sh = pre["lookup"], pre["branches"], pre["shift"](a.user_id, a.day)
    else:
        pings = db.scalars(select(LocationPing).where(LocationPing.tenant_id == tenant.id, LocationPing.user_id == a.user_id, LocationPing.ts >= a.check_in_at, LocationPing.ts <= end).order_by(LocationPing.ts)).all()
        # only customers near the track (bounding box) to keep this cheap
        lats = [p.lat for p in pings if p.lat is not None]
        lngs = [p.lng for p in pings if p.lng is not None]
        custs = []
        if lats:
            custs = db.scalars(select(Customer).where(Customer.tenant_id == tenant.id, Customer.deleted_at.is_(None), Customer.lat.is_not(None), Customer.lat.between(min(lats) - .01, max(lats) + .01),
                                                      Customer.lng.between(min(lngs) - .01, max(lngs) + .01))).all()
        branches = db.scalars(select(Branch).where(Branch.tenant_id == tenant.id, Branch.deleted_at.is_(None))).all()
        sh = shift_for(db, tenant, user, a.day)
    r = analyze(pings, a.check_in_at, end, custs, branches, _shift_end_utc(tenant, a.day, sh), sh["break"], (tenant.settings or {}).get("geofence_default_m", 100))
    r["shift"] = sh
    return r


def bulk_context(db, tenant: Tenant, atts: list, users: dict) -> dict:
    """Everything the analysis needs for many sessions, loaded once: breadcrumbs (lightweight rows, sorted), customers, branches, shifts and roster."""
    if not atts:
        return {"pings": {}, "customers": [], "lookup": lambda a, b: [], "branches": [], "shift": lambda u, d: {}}
    uids = {a.user_id for a in atts}
    lo, hi = min(a.check_in_at for a in atts), max((a.check_out_at or utcnow()) for a in atts)
    rows = db.execute(select(LocationPing.user_id, LocationPing.ts, LocationPing.lat, LocationPing.lng, LocationPing.mock, LocationPing.gps_on).where(
        LocationPing.tenant_id == tenant.id, LocationPing.user_id.in_(uids), LocationPing.ts >= lo, LocationPing.ts <= hi).order_by(LocationPing.user_id, LocationPing.ts)).all()
    by_user: dict = {}
    for r in rows:
        by_user.setdefault(r.user_id, []).append(r)
    pings = {u: (v, [x.ts for x in v]) for u, v in by_user.items()}
    customers = db.scalars(select(Customer).where(Customer.tenant_id == tenant.id, Customer.deleted_at.is_(None), Customer.lat.is_not(None))).all()
    branches = db.scalars(select(Branch).where(Branch.tenant_id == tenant.id, Branch.deleted_at.is_(None))).all()
    shifts = {s.id: s for s in db.scalars(select(Shift).where(Shift.tenant_id == tenant.id, Shift.deleted_at.is_(None)))}
    days = {a.day for a in atts}
    roster = {(r.user_id, r.day): r.shift_id for r in db.scalars(select(ShiftRoster).where(ShiftRoster.tenant_id == tenant.id, ShiftRoster.user_id.in_(uids), ShiftRoster.day >= min(days), ShiftRoster.day <= max(days)))}
    s = tenant.settings or {}
    default = lambda day: {"name": "Default", "start": s.get("work_start", "09:30"), "end": s.get("work_end", "18:30"), "grace": s.get("late_grace_min", 15), "break": 60, "off": day.weekday() in set(s.get("weekly_off", [6]))}

    def resolve(uid, day):
        if (uid, day) in roster:
            sid = roster[(uid, day)]
            if sid is None:
                return {**default(day), "off": True, "name": "Day off"}
            if sid in shifts:
                x = shifts[sid]
                return {"name": x.name, "start": x.start, "end": x.end, "grace": x.grace_min, "break": x.break_min, "off": False}
        u = users.get(uid)
        if u and u.shift_id in shifts:
            x = shifts[u.shift_id]
            return {"name": x.name, "start": x.start, "end": x.end, "grace": x.grace_min, "break": x.break_min, "off": day.weekday() in set(x.weekly_off or [])}
        return default(day)
    CELL = 0.003  # about 330 m: a 3x3 block of cells covers any customer within the matching radius
    grid: dict = {}
    for c in customers:
        grid.setdefault((int(c.lat // CELL), int(c.lng // CELL)), []).append(c)

    def lookup(lat, lng):
        gx, gy = int(lat // CELL), int(lng // CELL)
        return [c for dx in (-1, 0, 1) for dy in (-1, 0, 1) for c in grid.get((gx + dx, gy + dy), ())]
    return {"pings": pings, "customers": customers, "lookup": lookup, "branches": branches, "shift": resolve}


@router.get("/day-summary")
def day_summary(user_id: int | None = None, day: str | None = None, ctx: Ctx = Depends(current)):
    uid = user_id or ctx.user.id
    if uid not in ctx.team_ids():
        raise HTTPException(403, "You can only view your own team.")
    d = parse_day(day, local_today(ctx.tenant))
    a = ctx.db.scalar(select(Attendance).where(Attendance.tenant_id == ctx.tid, Attendance.user_id == uid, Attendance.day == d, Attendance.check_in_at.is_not(None), Attendance.deleted_at.is_(None)))
    if not a:
        return {"day": d.isoformat(), "present": False}
    u = ctx.db.get(User, uid)
    return {"day": d.isoformat(), "present": True, "name": u.name, "check_in_at": a.check_in_at.isoformat() + "Z", "check_out_at": a.check_out_at.isoformat() + "Z" if a.check_out_at else None, "status": a.status, **analyze_session(ctx.db, ctx.tenant, a, u)}


@router.get("/productivity")
def productivity(frm: str | None = None, to: str | None = None, ctx: Ctx = Depends(require("manager"))):
    today = local_today(ctx.tenant)
    d1, d2 = parse_day(frm, today - timedelta(days=6)), parse_day(to, today)
    if (d2 - d1).days > 31:
        raise HTTPException(422, "Choose a range of at most 31 days.")
    ids = ctx.team_ids()
    users = {u.id: u for u in ctx.db.scalars(select(User).where(User.tenant_id == ctx.tid, User.id.in_(ids), User.active.is_(True), User.deleted_at.is_(None), User.role != "owner"))}
    rows = {}
    visits = dict(ctx.db.execute(select(Visit.user_id, func.count()).where(Visit.tenant_id == ctx.tid, Visit.user_id.in_(list(users) or [0]), Visit.started_at >= local_day_bounds_utc(ctx.tenant, d1)[0], Visit.started_at < local_day_bounds_utc(ctx.tenant, d2)[1]).group_by(Visit.user_id)).all())
    atts = ctx.db.scalars(select(Attendance).where(Attendance.tenant_id == ctx.tid, Attendance.user_id.in_(list(users) or [0]), Attendance.day >= d1, Attendance.day <= d2, Attendance.check_in_at.is_not(None), Attendance.deleted_at.is_(None))).all()
    pre = bulk_context(ctx.db, ctx.tenant, atts, users)
    for a in atts:
        r = rows.setdefault(a.user_id, {"user_id": a.user_id, "name": users[a.user_id].name, "days": 0, "late": 0, "worked_min": 0, "travel_min": 0, "on_site_min": 0, "idle_min": 0, "gap_min": 0, "overtime_min": 0, "distance_km": 0.0, "stops": 0, "flagged": 0})
        x = analyze_session(ctx.db, ctx.tenant, a, users[a.user_id], pre)
        r["days"] += 1
        r["late"] += a.status == "late"
        r["flagged"] += bool(a.flagged) or any(an["type"] in ("mock_location", "teleport") for an in x["anomalies"])
        for k in ("worked_min", "travel_min", "on_site_min", "idle_min", "gap_min", "overtime_min"):
            r[k] += x[k]
        r["distance_km"] += x["distance_km"]
        r["stops"] += len([s for s in x["stops"] if s["kind"] == "customer"])
    out = []
    for uid, u in users.items():
        r = rows.get(uid) or {"user_id": uid, "name": u.name, "days": 0, "late": 0, "worked_min": 0, "travel_min": 0, "on_site_min": 0, "idle_min": 0, "gap_min": 0, "overtime_min": 0, "distance_km": 0.0, "stops": 0, "flagged": 0}
        r["distance_km"] = round(r["distance_km"], 1)
        r["visits"] = visits.get(uid, 0)
        r["avg_worked_h"] = round(r["worked_min"] / r["days"] / 60, 1) if r["days"] else 0
        r["productive_pct"] = round(r["on_site_min"] / r["worked_min"] * 100) if r["worked_min"] else 0
        r["idle_pct"] = round(r["idle_min"] / r["worked_min"] * 100) if r["worked_min"] else 0
        out.append(r)
    out.sort(key=lambda r: (-r["productive_pct"], r["name"]))
    return {"from": d1.isoformat(), "to": d2.isoformat(), "rows": out}


@router.get("/anomalies")
def anomalies(frm: str | None = None, to: str | None = None, ctx: Ctx = Depends(require("manager"))):
    today = local_today(ctx.tenant)
    d1, d2 = parse_day(frm, today - timedelta(days=2)), parse_day(to, today)
    if (d2 - d1).days > 14:
        raise HTTPException(422, "Choose a range of at most 14 days.")
    out = []
    pairs = ctx.db.execute(select(Attendance, User).join(User, User.id == Attendance.user_id).where(
        Attendance.tenant_id == ctx.tid, Attendance.user_id.in_(ctx.team_ids()), Attendance.day >= d1, Attendance.day <= d2, Attendance.check_in_at.is_not(None))).all()
    pre = bulk_context(ctx.db, ctx.tenant, [a for a, _ in pairs], {u.id: u for _, u in pairs})
    for att, u in pairs:
        x = analyze_session(ctx.db, ctx.tenant, att, u, pre)
        for an in x["anomalies"]:
            if an["type"] == "tracking_gap" and an["minutes"] < 30:
                continue
            out.append({"user": u.name, "user_id": u.id, "day": att.day.isoformat(), **an})
        if att.flagged:
            out.append({"user": u.name, "user_id": u.id, "day": att.day.isoformat(), "type": "attendance_flag", "detail": att.flagged})
        if att.check_in_at and not att.check_out_at and att.day < today:
            out.append({"user": u.name, "user_id": u.id, "day": att.day.isoformat(), "type": "no_checkout", "detail": "Never checked out"})
        if x["points"] == 0 and att.check_in_at:
            out.append({"user": u.name, "user_id": u.id, "day": att.day.isoformat(), "type": "no_tracking", "detail": "Checked in but no GPS data was recorded"})
    out.sort(key=lambda r: (r["day"], r["user"]), reverse=True)
    return out


# ================================================================== territories & geofence events
class TerritoryIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    polygon: list[list[float]] = Field(min_length=3, max_length=200)
    user_ids: list[int] = []
    alert: bool = True


def _inside(poly, lat, lng) -> bool:
    inside, j = False, len(poly) - 1
    for i in range(len(poly)):
        yi, xi, yj, xj = poly[i][0], poly[i][1], poly[j][0], poly[j][1]
        if (xi > lng) != (xj > lng) and lat < (yj - yi) * (lng - xi) / ((xj - xi) or 1e-12) + yi:
            inside = not inside
        j = i
    return inside


@router.post("/territories")
def save_territory(body: TerritoryIn, id: int | None = None, ctx: Ctx = Depends(require("manager"))):
    if any(len(p) != 2 or not (-90 <= p[0] <= 90 and -180 <= p[1] <= 180) for p in body.polygon):
        raise HTTPException(422, "Each polygon point must be [latitude, longitude].")
    t = ctx.db.get(Territory, id) if id else None
    if t and t.tenant_id != ctx.tid:
        raise HTTPException(404, "Territory not found.")
    t = t or Territory(tenant_id=ctx.tid)
    t.name, t.polygon, t.user_ids, t.alert = body.name, body.polygon, [u for u in body.user_ids if u in ctx.team_ids()], body.alert
    ctx.db.add(t)
    ctx.db.commit()
    return to_dict(t)


@router.get("/territories")
def territories(ctx: Ctx = Depends(current)):
    return [to_dict(t) for t in ctx.db.scalars(select(Territory).where(Territory.tenant_id == ctx.tid, Territory.deleted_at.is_(None)).order_by(Territory.name))]


@router.delete("/territories/{tid}")
def del_territory(tid: int, ctx: Ctx = Depends(require("manager"))):
    get_or_404(ctx.db, Territory, tid, ctx.tid).deleted_at = utcnow()
    ctx.db.commit()
    return {"ok": True}


def process_geo(db, tenant: Tenant, user: User, ping) -> None:
    """Called after location pings are stored. Detects arriving at / leaving customers and entering / leaving territories."""
    if ping.lat is None or ping.mock:
        return
    db.flush()
    s = tenant.settings or {}
    radius = int(s.get("geofence_default_m", 100))
    lat, lng, ts = ping.lat, ping.lng, ping.ts
    # --- customer sites
    near = db.scalars(select(Customer).where(Customer.tenant_id == tenant.id, Customer.deleted_at.is_(None), Customer.lat.is_not(None), Customer.lat.between(lat - .02, lat + .02), Customer.lng.between(lng - .02, lng + .02))).all()
    open_arr = {}
    for ev in db.scalars(select(GeoEvent).where(GeoEvent.tenant_id == tenant.id, GeoEvent.user_id == user.id, GeoEvent.ref_type == "customer", GeoEvent.kind.in_(["arrive", "depart"]), GeoEvent.ts >= ts - timedelta(hours=16)).order_by(GeoEvent.ts)):
        if ev.kind == "arrive":
            open_arr[ev.ref_id] = ev
        else:
            open_arr.pop(ev.ref_id, None)
    inside_now = set()
    for c in near:
        d = haversine_m(lat, lng, c.lat, c.lng)
        if d <= radius:
            inside_now.add(c.id)
            if c.id not in open_arr:
                db.add(GeoEvent(tenant_id=tenant.id, user_id=user.id, kind="arrive", ref_type="customer", ref_id=c.id, ts=ts, lat=lat, lng=lng))
                # nudge: today's beat stop for this customer
                bv = db.scalar(select(BeatVisit).where(BeatVisit.tenant_id == tenant.id, BeatVisit.user_id == user.id, BeatVisit.customer_id == c.id, BeatVisit.day == local_today(tenant), BeatVisit.status == "planned"))
                notify(db, tenant.id, user.id, f"You've arrived at {c.name}", "Start the visit to log it." if bv else "Open Visits to log this call.", "geo", f"arr:{user.id}:{c.id}:{ts:%Y%m%d%H}")
                if s.get("notify_arrivals") and user.manager_id:
                    notify(db, tenant.id, user.manager_id, "Arrival", f"{user.name} arrived at {c.name}.", "geo", f"arrm:{user.id}:{c.id}:{ts:%Y%m%d%H}")
    for cid, ev in open_arr.items():
        if cid in inside_now:
            continue
        c = next((x for x in near if x.id == cid), None) or db.get(Customer, cid)
        if c and c.lat is not None and haversine_m(lat, lng, c.lat, c.lng) <= radius * 1.5:
            continue  # hysteresis: GPS jitter at the edge isn't a departure
        db.add(GeoEvent(tenant_id=tenant.id, user_id=user.id, kind="depart", ref_type="customer", ref_id=cid, ts=ts, lat=lat, lng=lng, minutes=max(0, round((ts - ev.ts).total_seconds() / 60))))
    # --- territories (only while on duty)
    on_duty = db.scalar(select(Attendance.id).where(Attendance.tenant_id == tenant.id, Attendance.user_id == user.id, Attendance.check_in_at.is_not(None), Attendance.check_out_at.is_(None), Attendance.day == local_today(tenant)))
    if on_duty:
        for t in db.scalars(select(Territory).where(Territory.tenant_id == tenant.id, Territory.deleted_at.is_(None))):
            if user.id not in (t.user_ids or []):
                continue
            inside = _inside(t.polygon, lat, lng)
            last = db.scalar(select(GeoEvent).where(GeoEvent.tenant_id == tenant.id, GeoEvent.user_id == user.id, GeoEvent.ref_type == "territory", GeoEvent.ref_id == t.id).order_by(GeoEvent.ts.desc()).limit(1))
            was_inside = (last is None) or last.kind == "territory_enter"
            if inside != was_inside or (last is None and not inside):
                db.add(GeoEvent(tenant_id=tenant.id, user_id=user.id, kind="territory_enter" if inside else "territory_exit", ref_type="territory", ref_id=t.id, ts=ts, lat=lat, lng=lng))
                if not inside and t.alert and user.manager_id:
                    notify(db, tenant.id, user.manager_id, "Left territory", f"{user.name} left '{t.name}'.", "geo", f"tx:{user.id}:{t.id}:{ts:%Y%m%d%H}")
                    notify(db, tenant.id, user.id, "Outside your territory", f"You are outside '{t.name}'.", "geo", f"txu:{user.id}:{t.id}:{ts:%Y%m%d%H}")
    db.flush()  # the session doesn't autoflush: later pings in the same batch must see these events


@router.get("/geo-events")
def geo_events(user_id: int | None = None, day: str | None = None, ctx: Ctx = Depends(current)):
    d = parse_day(day, local_today(ctx.tenant))
    s, e = local_day_bounds_utc(ctx.tenant, d)
    ids = [user_id] if user_id and user_id in ctx.team_ids() else ctx.team_ids()
    users = {u.id: u.name for u in ctx.db.scalars(select(User).where(User.tenant_id == ctx.tid))}
    cn = {c.id: c.name for c in ctx.db.scalars(select(Customer).where(Customer.tenant_id == ctx.tid))}
    tn = {t.id: t.name for t in ctx.db.scalars(select(Territory).where(Territory.tenant_id == ctx.tid))}
    rows = ctx.db.scalars(select(GeoEvent).where(GeoEvent.tenant_id == ctx.tid, GeoEvent.user_id.in_(ids), GeoEvent.ts >= s, GeoEvent.ts < e).order_by(GeoEvent.ts.desc()).limit(500)).all()
    return [{**to_dict(g), "user_name": users.get(g.user_id), "place": (cn if g.ref_type == "customer" else tn).get(g.ref_id)} for g in rows]


# ================================================================== shifts & roster
class ShiftIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    start: str = Field(pattern=r"^\d{2}:\d{2}$")
    end: str = Field(pattern=r"^\d{2}:\d{2}$")
    grace_min: int = Field(15, ge=0, le=120)
    break_min: int = Field(60, ge=0, le=240)
    weekly_off: list[int] = []


@router.post("/shifts")
def save_shift(body: ShiftIn, id: int | None = None, ctx: Ctx = Depends(require("admin"))):
    sh = ctx.db.get(Shift, id) if id else None
    if sh and sh.tenant_id != ctx.tid:
        raise HTTPException(404, "Shift not found.")
    sh = sh or Shift(tenant_id=ctx.tid)
    sh.name, sh.start, sh.end, sh.grace_min, sh.break_min, sh.weekly_off = body.name, body.start, body.end, body.grace_min, body.break_min, sorted(set(body.weekly_off))
    ctx.db.add(sh)
    ctx.db.commit()
    return to_dict(sh)


@router.get("/shifts")
def shifts(ctx: Ctx = Depends(current)):
    counts = dict(ctx.db.execute(select(User.shift_id, func.count()).where(User.tenant_id == ctx.tid, User.deleted_at.is_(None)).group_by(User.shift_id)).all())
    return [{**to_dict(s), "people": counts.get(s.id, 0)} for s in ctx.db.scalars(select(Shift).where(Shift.tenant_id == ctx.tid, Shift.deleted_at.is_(None)).order_by(Shift.start))]


@router.delete("/shifts/{sid}")
def del_shift(sid: int, ctx: Ctx = Depends(require("admin"))):
    s = get_or_404(ctx.db, Shift, sid, ctx.tid)
    if ctx.db.scalar(select(func.count()).where(User.tenant_id == ctx.tid, User.shift_id == sid, User.deleted_at.is_(None))):
        raise HTTPException(409, "People are still on this shift. Move them first.")
    s.deleted_at = utcnow()
    ctx.db.commit()
    return {"ok": True}


@router.get("/roster")
def roster(week: str | None = None, ctx: Ctx = Depends(current)):
    """Seven days from `week` (a Monday; default this week) for the people you manage, with the effective shift per day."""
    start = parse_day(week, local_today(ctx.tenant))
    start -= timedelta(days=start.weekday())
    days = [start + timedelta(days=i) for i in range(7)]
    ids = ctx.team_ids() if ctx.is_at_least("manager") else [ctx.user.id]
    users = ctx.db.scalars(select(User).where(User.tenant_id == ctx.tid, User.id.in_(ids), User.active.is_(True), User.deleted_at.is_(None), User.role != "owner").order_by(User.name)).all()
    over = {(r.user_id, r.day): r for r in ctx.db.scalars(select(ShiftRoster).where(ShiftRoster.tenant_id == ctx.tid, ShiftRoster.day >= days[0], ShiftRoster.day <= days[-1]))}
    hol = {h.day: h.name for h in ctx.db.scalars(select(Holiday).where(Holiday.tenant_id == ctx.tid, Holiday.deleted_at.is_(None), Holiday.day >= days[0], Holiday.day <= days[-1]))}
    rows = []
    for u in users:
        cells = []
        for d in days:
            sh = shift_for(ctx.db, ctx.tenant, u, d)
            cells.append({"day": d.isoformat(), "shift": sh["name"], "start": sh["start"], "end": sh["end"], "off": sh["off"], "override": (u.id, d) in over, "holiday": hol.get(d)})
        rows.append({"user_id": u.id, "name": u.name, "cells": cells})
    return {"week": days[0].isoformat(), "days": [d.isoformat() for d in days], "rows": rows}


class RosterIn(BaseModel):
    user_id: int
    day: date
    shift_id: int | None = None   # null = day off
    clear: bool = False           # remove the override


@router.put("/roster")
def set_roster(body: RosterIn, ctx: Ctx = Depends(require("manager"))):
    if body.user_id not in ctx.team_ids():
        raise HTTPException(403, "You can only roster people in your team.")
    if body.shift_id:
        get_or_404(ctx.db, Shift, body.shift_id, ctx.tid)
    r = ctx.db.scalar(select(ShiftRoster).where(ShiftRoster.tenant_id == ctx.tid, ShiftRoster.user_id == body.user_id, ShiftRoster.day == body.day))
    if body.clear:
        if r:
            ctx.db.delete(r)
    else:
        r = r or ShiftRoster(tenant_id=ctx.tid, user_id=body.user_id, day=body.day)
        r.shift_id = body.shift_id
        ctx.db.add(r)
    if body.user_id != ctx.user.id:
        notify(ctx.db, ctx.tid, body.user_id, "Roster updated", f"Your shift for {body.day:%a %d %b} was changed.", "roster")
    ctx.db.commit()
    return {"ok": True}


class SwapIn(BaseModel):
    with_user_id: int
    day: date
    reason: str = ""


@router.post("/swaps")
def request_swap(body: SwapIn, ctx: Ctx = Depends(current)):
    other = ctx.db.get(User, body.with_user_id)
    if not other or other.tenant_id != ctx.tid or other.id == ctx.user.id:
        raise HTTPException(422, "Choose a colleague to swap with.")
    if body.day < local_today(ctx.tenant):
        raise HTTPException(422, "You can't swap a past day.")
    sw = ShiftSwap(tenant_id=ctx.tid, from_user=ctx.user.id, to_user=other.id, day=body.day, reason=body.reason[:200])
    ctx.db.add(sw)
    notify(ctx.db, ctx.tid, other.id, "Shift swap request", f"{ctx.user.name} asks to swap shifts on {body.day:%a %d %b}. {body.reason}", "roster")
    ctx.db.commit()
    return to_dict(sw)


@router.get("/swaps")
def swaps(ctx: Ctx = Depends(current)):
    users = {u.id: u.name for u in ctx.db.scalars(select(User).where(User.tenant_id == ctx.tid))}
    team = set(ctx.team_ids())
    rows = ctx.db.scalars(select(ShiftSwap).where(ShiftSwap.tenant_id == ctx.tid, ShiftSwap.deleted_at.is_(None)).order_by(ShiftSwap.id.desc()).limit(100)).all()
    return [{**to_dict(r), "from_name": users.get(r.from_user), "to_name": users.get(r.to_user)} for r in rows if ctx.user.id in (r.from_user, r.to_user) or (ctx.is_at_least("manager") and (r.from_user in team or r.to_user in team))]


@router.post("/swaps/{sid}/respond")
def respond_swap(sid: int, body: dict, ctx: Ctx = Depends(current)):
    sw = get_or_404(ctx.db, ShiftSwap, sid, ctx.tid)
    accept = bool(body.get("accept"))
    if sw.status == "requested":
        if sw.to_user != ctx.user.id:
            raise HTTPException(403, "Only the colleague being asked can answer.")
        sw.status = "accepted" if accept else "declined"
        if accept:
            for uid in {ctx.db.get(User, sw.from_user).manager_id, ctx.db.get(User, sw.to_user).manager_id} - {None}:
                notify(ctx.db, ctx.tid, uid, "Shift swap to approve", f"{ctx.db.get(User, sw.from_user).name} ↔ {ctx.user.name} on {sw.day:%a %d %b}.", "approval")
        else:
            notify(ctx.db, ctx.tid, sw.from_user, "Swap declined", f"{ctx.user.name} declined the swap.", "roster")
    elif sw.status == "accepted":
        if not ctx.is_at_least("manager") or (sw.from_user not in ctx.team_ids() and sw.to_user not in ctx.team_ids()):
            raise HTTPException(403, "A manager of either person must approve.")
        if not accept:
            sw.status = "rejected"
        else:
            a, b = ctx.db.get(User, sw.from_user), ctx.db.get(User, sw.to_user)
            sa, sb = shift_for(ctx.db, ctx.tenant, a, sw.day), shift_for(ctx.db, ctx.tenant, b, sw.day)
            def sid_of(user, sh):
                if sh["off"]:
                    return None
                x = ctx.db.scalar(select(Shift).where(Shift.tenant_id == ctx.tid, Shift.name == sh["name"], Shift.deleted_at.is_(None)))
                return x.id if x else None
            for u, new_sh in ((a, sb), (b, sa)):
                r = ctx.db.scalar(select(ShiftRoster).where(ShiftRoster.tenant_id == ctx.tid, ShiftRoster.user_id == u.id, ShiftRoster.day == sw.day)) or ShiftRoster(tenant_id=ctx.tid, user_id=u.id, day=sw.day)
                r.shift_id = sid_of(u, new_sh)
                ctx.db.add(r)
            sw.status = "approved"
            for uid in (sw.from_user, sw.to_user):
                notify(ctx.db, ctx.tid, uid, "Shift swap approved", f"Your shift on {sw.day:%a %d %b} was swapped.", "roster")
    else:
        raise HTTPException(409, f"This request is already {sw.status}.")
    ctx.db.commit()
    return to_dict(sw)


# ================================================================== dispatch: nearest available executive
@router.get("/dispatch/{task_id}")
def suggest(task_id: int, ctx: Ctx = Depends(require("manager"))):
    t = get_or_404(ctx.db, Task, task_id, ctx.tid)
    if t.lat is None:
        raise HTTPException(422, "This task has no location. Pick a customer with a map location first.")
    now = utcnow()
    ids = ctx.team_ids()
    sessions = {a.user_id for a in ctx.db.scalars(select(Attendance).where(Attendance.tenant_id == ctx.tid, Attendance.user_id.in_(ids), Attendance.day == local_today(ctx.tenant), Attendance.check_in_at.is_not(None), Attendance.check_out_at.is_(None)))}
    latest = select(LocationPing.user_id, func.max(LocationPing.ts).label("ts")).where(LocationPing.tenant_id == ctx.tid, LocationPing.user_id.in_(ids), LocationPing.ts >= now - timedelta(hours=3), LocationPing.lat.is_not(None)).group_by(LocationPing.user_id).subquery()
    pos = {p.user_id: p for p in ctx.db.scalars(select(LocationPing).join(latest, (LocationPing.user_id == latest.c.user_id) & (LocationPing.ts == latest.c.ts)))}
    load = dict(ctx.db.execute(select(Task.assignee_id, func.count()).where(Task.tenant_id == ctx.tid, Task.assignee_id.in_(ids), Task.status.notin_(["done"]), Task.deleted_at.is_(None)).group_by(Task.assignee_id)).all())
    out = []
    for u in ctx.db.scalars(select(User).where(User.tenant_id == ctx.tid, User.id.in_(ids), User.active.is_(True), User.deleted_at.is_(None), User.role != "owner")):
        p = pos.get(u.id)
        if not p:
            continue
        km = haversine_m(p.lat, p.lng, t.lat, t.lng) / 1000 * 1.3
        age = (now - p.ts).total_seconds() / 60
        out.append({"user_id": u.id, "name": u.name, "distance_km": round(km, 1), "eta_min": round(km / 25 * 60), "open_tasks": load.get(u.id, 0), "on_duty": u.id in sessions, "last_seen_min": round(age),
                    "score": round(km + 1.5 * load.get(u.id, 0) + (0 if u.id in sessions else 50) + age / 30, 1)})
    out.sort(key=lambda r: r["score"])
    return {"task": t.title, "suggestions": out[:6]}


class ProgressIn(BaseModel):
    step: str  # en_route | checked_in | rescheduled
    lat: float | None = None
    lng: float | None = None
    due_at: datetime | None = None
    reason: str = ""


@router.post("/tasks/{task_id}/progress")
def task_progress(task_id: int, body: ProgressIn, ctx: Ctx = Depends(current)):
    """Assigned → En route → Checked in → Completed (via the normal complete action) / Rescheduled."""
    t = get_or_404(ctx.db, Task, task_id, ctx.tid)
    if t.assignee_id != ctx.user.id and not (ctx.is_at_least("manager") and t.assignee_id in ctx.team_ids()):
        raise HTTPException(403, "This task belongs to someone else.")
    if t.status == "done":
        raise HTTPException(409, "This task is already completed.")
    if body.step == "en_route":
        t.status = "en_route"
        if t.created_by and t.created_by != ctx.user.id:
            notify(ctx.db, ctx.tid, t.created_by, "Task en route", f"{ctx.user.name} is on the way: {t.title}", "task")
    elif body.step == "checked_in":
        if t.lat is not None and t.geofence_m:
            if body.lat is None:
                raise HTTPException(422, "Turn on GPS to check in at the task location.")
            d = haversine_m(body.lat, body.lng, t.lat, t.lng)
            if d > t.geofence_m * 2:
                raise HTTPException(403, f"You are {int(d)} m from the task location.")
        t.status = "checked_in"
    elif body.step == "rescheduled":
        if not body.due_at or not body.reason.strip():
            raise HTTPException(422, "Choose a new time and give a reason.")
        t.due_at, t.status, t.reminded = body.due_at.replace(tzinfo=None), "rescheduled", False
        if t.created_by and t.created_by != ctx.user.id:
            notify(ctx.db, ctx.tid, t.created_by, "Task rescheduled", f"{t.title}: {body.reason}", "task")
    else:
        raise HTTPException(422, "Step must be en_route, checked_in or rescheduled.")
    ctx.db.commit()
    return to_dict(t)
