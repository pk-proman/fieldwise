from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from ..db import SessionLocal
from ..deps import Ctx, current, _auth
from ..models import LocationPing, User, Attendance, Branch, Visit, Customer, DeviceHealth, RouteDay, utcnow
from ..deps import require
from .. import devhealth
from ..common import to_dict, local_today, parse_day, local_day_bounds_utc
from ..geo import path_km
from .. import hub

router = APIRouter(tags=["tracking"])


class PingIn(BaseModel):
    ts: datetime
    lat: float | None = Field(None, ge=-90, le=90)
    lng: float | None = Field(None, ge=-180, le=180)
    accuracy: float | None = None
    battery: int | None = Field(None, ge=0, le=100)
    network: str = "online"
    gps_on: bool = True
    mock: bool = False


@router.post("/tracking/pings")
def ingest(pings: list[PingIn], ctx: Ctx = Depends(current)):
    """Accepts a batch so offline breadcrumbs can be flushed on reconnect."""
    if len(pings) > 1000:
        raise HTTPException(413, "Send at most 1000 pings per request.")
    now = utcnow()
    existing = {t for (t,) in ctx.db.execute(select(LocationPing.ts).where(
        LocationPing.tenant_id == ctx.tid, LocationPing.user_id == ctx.user.id,
        LocationPing.ts >= now - timedelta(days=3)))}
    added, last, fresh = 0, None, []
    for p in sorted(pings, key=lambda x: x.ts):
        ts = p.ts.replace(tzinfo=None) if p.ts.tzinfo is None else p.ts.astimezone(None).replace(tzinfo=None)
        if ts > now + timedelta(minutes=5) or ts < now - timedelta(days=3) or ts in existing:
            continue
        lp = LocationPing(tenant_id=ctx.tid, user_id=ctx.user.id, ts=ts, lat=p.lat, lng=p.lng, accuracy=p.accuracy, battery=p.battery, network=p.network, gps_on=p.gps_on, mock=p.mock)
        ctx.db.add(lp)
        fresh.append(lp)
        existing.add(ts)
        added += 1
        last = (ts, p)
    from .field import process_geo
    step = max(1, len(fresh) // 40)  # offline replays: sample, the last point always counts
    for lp in fresh[::step][:40] + fresh[-1:]:
        try:
            process_geo(ctx.db, ctx.tenant, ctx.user, lp)
        except Exception:  # never lose location data over an alerting problem
            import logging
            logging.getLogger("fieldwise").exception("geo processing failed")
    ctx.db.commit()
    if last:
        ts, p = last
        hub.publish(ctx.tid, ctx.user.id, {"type": "ping", "user_id": ctx.user.id, "name": ctx.user.name, "lat": p.lat, "lng": p.lng,
                                           "battery": p.battery, "network": p.network, "gps_on": p.gps_on, "ts": ts.isoformat() + "Z"})
    return {"accepted": added}


class HealthIn(BaseModel):
    location: str = "unknown"                     # always | while_in_use | denied | unknown
    location_services: bool | None = None
    notifications: bool | None = None
    battery_unrestricted: bool | None = None
    autostart_confirmed: bool | None = None
    disclosure_accepted: bool | None = None
    manufacturer: str = Field("", max_length=40)
    model: str = Field("", max_length=60)
    android: int | None = None
    app_version: str = Field("", max_length=20)
    tracking_active: bool = False
    last_ping_age_min: float | None = None
    stop_reason: str = Field("", max_length=30)


@router.post("/tracking/health")
def report_health(body: HealthIn, ctx: Ctx = Depends(current)):
    """The phone reports whether tracking can survive (permissions, battery restriction, brand). Sent on app open, resume and every 30 min while on duty."""
    row = ctx.db.scalar(select(DeviceHealth).where(DeviceHealth.tenant_id == ctx.tid, DeviceHealth.user_id == ctx.user.id))
    if not row:
        row = DeviceHealth(tenant_id=ctx.tid, user_id=ctx.user.id)
        ctx.db.add(row)
    row.data = body.model_dump()
    row.reported_at = utcnow()
    ctx.db.commit()
    return {"problems": devhealth.problems(row.data)}


@router.get("/tracking/health")
def team_health(ctx: Ctx = Depends(require("manager"))):
    """Per-person tracking health for the people this manager can see: what is wrong and how to fix it."""
    ids = ctx.team_ids()
    now = utcnow()
    users = {u.id: u for u in ctx.db.scalars(select(User).where(User.tenant_id == ctx.tid, User.id.in_(ids), User.active.is_(True), User.deleted_at.is_(None)))}
    rows = {r.user_id: r for r in ctx.db.scalars(select(DeviceHealth).where(DeviceHealth.tenant_id == ctx.tid, DeviceHealth.user_id.in_(ids)))}
    att = _current_sessions(ctx, list(users))
    last = dict(ctx.db.execute(select(LocationPing.user_id, func.max(LocationPing.ts)).where(
        LocationPing.tenant_id == ctx.tid, LocationPing.user_id.in_(list(users)), LocationPing.ts >= now - timedelta(hours=24)).group_by(LocationPing.user_id)).all())
    out = []
    for uid, u in users.items():
        r = rows.get(uid)
        a = att.get(uid)
        on_duty = bool(a and not a.check_out_at)
        d = r.data if r else {}
        age = round((now - last[uid]).total_seconds() / 60) if uid in last else None
        out.append({"user_id": uid, "name": u.name, "on_duty": on_duty, "reported": bool(r), "reported_at": r.reported_at.isoformat() + "Z" if r else None,
                    "device": (f"{d.get('manufacturer', '')} {d.get('model', '')}".strip() or None), "android": d.get("android"), "app_version": d.get("app_version") or None,
                    "battery": None, "last_ping_min": age, "problems": devhealth.problems(d) if r else []})
    out.sort(key=lambda x: (not x["on_duty"], -len(x["problems"]), x["name"]))
    return out


def _current_sessions(ctx: Ctx, ids) -> dict:
    """Each member's latest session: today's, or one still open from yesterday (forgot to check out)."""
    now = utcnow()
    rows = ctx.db.scalars(select(Attendance).where(
        Attendance.tenant_id == ctx.tid, Attendance.user_id.in_(ids), Attendance.check_in_at.is_not(None), Attendance.deleted_at.is_(None),
        (Attendance.day == local_today(ctx.tenant)) | (Attendance.check_out_at.is_(None) & (Attendance.check_in_at >= now - timedelta(hours=36)))).order_by(Attendance.check_in_at))
    return {a.user_id: a for a in rows}  # latest wins


def _session_pings(ctx: Ctx, a: Attendance):
    end = a.check_out_at or utcnow()
    return ctx.db.scalars(select(LocationPing).where(LocationPing.tenant_id == ctx.tid, LocationPing.user_id == a.user_id, LocationPing.ts >= a.check_in_at,
                                                    LocationPing.ts <= end).order_by(LocationPing.ts)).all()


@router.get("/tracking/live-routes")
def live_routes(ctx: Ctx = Depends(current)):
    """Route travelled since each member's last check-in."""
    out = {}
    for uid, a in _current_sessions(ctx, ctx.team_ids()).items():
        pings = _session_pings(ctx, a)
        pts = [[p.lat, p.lng] for p in pings if p.lat is not None and not p.mock]
        if not pts and a.check_in_lat is not None:
            pts = [[a.check_in_lat, a.check_in_lng]]
        out[uid] = {"attendance_id": a.id, "check_in_at": a.check_in_at.isoformat() + "Z", "check_out_at": a.check_out_at.isoformat() + "Z" if a.check_out_at else None,
                    "start": [a.check_in_lat, a.check_in_lng] if a.check_in_lat is not None else None, "points": pts, "stats": trail_stats(pings)}
    return out


@router.get("/tracking/sessions")
def sessions(user_id: int | None = None, limit: int = 30, ctx: Ctx = Depends(current)):
    """Previous check-ins for a member, newest first."""
    uid = user_id or ctx.user.id
    if uid not in ctx.team_ids():
        raise HTTPException(403, "You can only view your own team.")
    brs = {b.id: b.name for b in ctx.db.scalars(select(Branch).where(Branch.tenant_id == ctx.tid))}
    rows = ctx.db.scalars(select(Attendance).where(Attendance.tenant_id == ctx.tid, Attendance.user_id == uid, Attendance.check_in_at.is_not(None), Attendance.deleted_at.is_(None))
                          .order_by(Attendance.check_in_at.desc()).limit(min(limit, 90))).all()
    return [{"id": a.id, "day": a.day.isoformat(), "check_in_at": a.check_in_at.isoformat() + "Z", "check_out_at": a.check_out_at.isoformat() + "Z" if a.check_out_at else None,
             "branch": brs.get(a.check_in_branch_id), "distance_km": a.total_distance_km, "status": a.status, "flagged": a.flagged, "open": a.check_out_at is None} for a in rows]


@router.get("/tracking/session/{aid}")
def session_route(aid: int, ctx: Ctx = Depends(current)):
    a = ctx.db.get(Attendance, aid)
    if not a or a.tenant_id != ctx.tid or a.user_id not in ctx.team_ids() or not a.check_in_at:
        raise HTTPException(404, "Check-in not found.")
    pings = _session_pings(ctx, a)
    end = a.check_out_at or utcnow()
    visits = ctx.db.execute(select(Visit, Customer.name, Customer.lat, Customer.lng).outerjoin(Customer, Customer.id == Visit.customer_id).where(
        Visit.tenant_id == ctx.tid, Visit.user_id == a.user_id, Visit.started_at >= a.check_in_at, Visit.started_at <= end).order_by(Visit.started_at)).all()
    return {"attendance": {"id": a.id, "check_in_at": a.check_in_at.isoformat() + "Z", "check_out_at": a.check_out_at.isoformat() + "Z" if a.check_out_at else None,
                           "in": [a.check_in_lat, a.check_in_lng], "out": [a.check_out_lat, a.check_out_lng] if a.check_out_lat is not None else None, "status": a.status, "flagged": a.flagged},
            "stats": trail_stats(pings), "analysis": __import__("app.routers.field", fromlist=["x"]).analyze_session(ctx.db, ctx.tenant, a), "points": [{"lat": p.lat, "lng": p.lng, "ts": p.ts.isoformat() + "Z", "battery": p.battery, "gps_on": p.gps_on, "network": p.network, "mock": p.mock} for p in pings if p.lat is not None],
            "visits": [{"customer": n, "lat": la or v.lat, "lng": lo or v.lng, "started_at": v.started_at.isoformat() + "Z", "ended_at": v.ended_at.isoformat() + "Z" if v.ended_at else None} for v, n, la, lo in visits]}


@router.get("/tracking/live")
def live(ctx: Ctx = Depends(current)):
    """Everyone on duty today: latest GPS ping if there is one, otherwise the check-in location."""
    ids = ctx.team_ids()
    now = utcnow()
    latest = select(LocationPing.user_id, func.max(LocationPing.ts).label("ts")).where(
        LocationPing.tenant_id == ctx.tid, LocationPing.user_id.in_(ids), LocationPing.ts >= now - timedelta(hours=24)
    ).group_by(LocationPing.user_id).subquery()
    pings = {u.id: (p, u) for p, u in ctx.db.execute(select(LocationPing, User).join(latest, (LocationPing.user_id == latest.c.user_id) & (LocationPing.ts == latest.c.ts))
                                                   .join(User, User.id == LocationPing.user_id)).all()}
    att = _current_sessions(ctx, ids)
    out = []
    for uid in set(pings) | set(att):
        a = att.get(uid)
        on_duty = bool(a and not a.check_out_at)
        if uid in pings:
            p, u = pings[uid]
            age = (now - p.ts).total_seconds() / 60
            if a and a.check_in_at and p.ts < a.check_in_at and a.check_in_lat is not None:  # stale ping from an earlier day: prefer the check-in fix
                p = None
        else:
            p, u = None, ctx.db.get(User, uid)
        if p is not None:
            state = "live" if age < 10 else ("idle" if age < 60 else "offline")
            if a and a.check_out_at:
                state = "checked_out"
            out.append({"user_id": u.id, "name": u.name, "role": u.role, "lat": p.lat, "lng": p.lng, "battery": p.battery, "network": p.network, "gps_on": p.gps_on,
                        "ts": p.ts.isoformat() + "Z", "minutes_ago": round(age), "state": state, "checked_in": bool(a), "check_in_at": a.check_in_at.isoformat() + "Z" if a else None})
        elif a:  # checked in but no breadcrumbs yet (tracker not running / phone offline)
            age = (now - a.check_in_at).total_seconds() / 60
            out.append({"user_id": u.id, "name": u.name, "role": u.role, "lat": a.check_in_lat, "lng": a.check_in_lng, "battery": None, "network": "unknown", "gps_on": True,
                        "ts": a.check_in_at.isoformat() + "Z", "minutes_ago": round(age), "state": "checked_out" if a.check_out_at else "no_gps", "checked_in": True, "check_in_at": a.check_in_at.isoformat() + "Z"})
    return out


def trail_stats(pings) -> dict:
    pts = [(p.lat, p.lng) for p in pings if p.lat is not None and not p.mock]
    gps_off = net_off = 0.0
    for a, b in zip(pings, pings[1:]):
        gap = (b.ts - a.ts).total_seconds() / 60
        if gap > 120:
            continue
        if not a.gps_on:
            gps_off += gap
        if a.network == "offline":
            net_off += gap
    return {"distance_km": round(path_km(pts), 2), "gps_off_min": round(gps_off), "internet_off_min": round(net_off),
            "points": len(pings), "mock_points": sum(1 for p in pings if p.mock),
            "min_battery": min((p.battery for p in pings if p.battery is not None), default=None)}


@router.get("/tracking/trail")
def trail(user_id: int | None = None, day: str | None = None, ctx: Ctx = Depends(current)):
    uid = user_id or ctx.user.id
    if uid not in ctx.team_ids():
        raise HTTPException(403, "You can only view your own team.")
    d = parse_day(day, local_today(ctx.tenant))
    s, e = local_day_bounds_utc(ctx.tenant, d)
    pings = ctx.db.scalars(select(LocationPing).where(LocationPing.tenant_id == ctx.tid, LocationPing.user_id == uid,
                                                      LocationPing.ts >= s, LocationPing.ts < e).order_by(LocationPing.ts)).all()
    return {"stats": trail_stats(pings), "points": [{"lat": p.lat, "lng": p.lng, "ts": p.ts.isoformat() + "Z", "battery": p.battery,
                                                      "gps_on": p.gps_on, "network": p.network, "mock": p.mock}
                                                     for p in pings if p.lat is not None]}


@router.websocket("/ws/live")
async def ws_live(ws: WebSocket, token: str = Query("")):
    await ws.accept()
    db = SessionLocal()
    try:
        ctx = _auth(db, token)
        if not ctx.is_at_least("manager"):
            await ws.close(code=4403)
            return
        allowed = set(ctx.team_ids())
        tid = ctx.tid
    except Exception:
        await ws.close(code=4401)
        return
    finally:
        db.close()
    await hub.subscribe(tid, ws, allowed)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.unsubscribe(tid, ws)


# ============================================================ route history (month view, scoped by the reporting hierarchy)
def _month_bounds(month: str | None, tenant):
    from datetime import date
    today = local_today(tenant)
    try:
        y, m = (int(x) for x in (month or f"{today.year}-{today.month:02d}").split("-"))
        first = date(y, m, 1)
    except (ValueError, TypeError):
        raise HTTPException(422, "Month must look like 2026-11.")
    nxt = date(y + (m == 12), 1 if m == 12 else m + 1, 1)
    return first, nxt


def _row(rd: RouteDay, full=False):
    d = {"day": rd.day.isoformat(), "distance_km": rd.distance_km, "est_km": rd.est_km, "moving_min": rd.moving_min, "stop_min": rd.stop_min, "gap_min": rd.gap_min, "points": rd.points,
         "quality": rd.quality, "first_at": rd.first_at.isoformat() + "Z" if rd.first_at else None, "last_at": rd.last_at.isoformat() + "Z" if rd.last_at else None,
         "stops_n": len(rd.stops or []), "expense": rd.expense}
    if full:
        d.update({"stops": rd.stops, "polyline": rd.polyline})
    return d


@router.get("/tracking/history/team")
def history_team(month: str | None = None, manager_id: int | None = None, ctx: Ctx = Depends(current)):
    """One level of the hierarchy: the chosen manager (default: me) and each direct report, with the whole branch under each report rolled up."""
    first, nxt = _month_bounds(month, ctx.tenant)
    team = set(ctx.team_ids())
    root = manager_id or ctx.user.id
    if root not in team:
        raise HTTPException(403, "You can only view your own team.")
    rows = ctx.db.execute(select(RouteDay.user_id, func.sum(RouteDay.distance_km), func.sum(RouteDay.est_km), func.count(RouteDay.id), func.avg(RouteDay.quality),
                                 func.sum(RouteDay.stop_min), func.sum(RouteDay.moving_min)).where(
        RouteDay.tenant_id == ctx.tid, RouteDay.day >= first, RouteDay.day < nxt, RouteDay.user_id.in_(team), RouteDay.points > 0).group_by(RouteDay.user_id)).all()
    agg = {r[0]: r[1:] for r in rows}
    users = {u.id: u for u in ctx.db.scalars(select(User).where(User.tenant_id == ctx.tid, User.id.in_(team)))}

    def roll(ids):
        km = est = days = q = st = mv = 0.0
        n = 0
        for i in ids:
            a = agg.get(i)
            if a:
                km += a[0] or 0; est += a[1] or 0; days += a[2]; q += (a[3] or 0) * a[2]; n += a[2]; st += a[4] or 0; mv += a[5] or 0
        return {"distance_km": round(km, 1), "est_km": round(est, 1), "days": int(days), "quality": round(q / n) if n else None, "stop_min": int(st), "moving_min": int(mv)}
    out = []
    me = users.get(root)
    if me:
        out.append({"user_id": me.id, "name": me.name, "role": me.role, "self": True, "people": 1, **roll([me.id])})
    direct = ctx.direct_report_ids(root)
    if root == ctx.user.id and ctx.is_at_least("admin"):    # the top of the organisation: people with no manager are shown too, so nobody is invisible
        direct += [u.id for u in users.values() if u.manager_id is None and u.id != root and u.id not in direct and u.role != "owner"]
    for rid in direct:
        if rid not in team or rid not in users:
            continue
        branch = ctx.subtree_ids(rid) & team
        out.append({"user_id": rid, "name": users[rid].name, "role": users[rid].role, "self": False, "people": len(branch), "has_team": len(branch) > 1, **roll(branch)})
    chain, cur, seen = [], users.get(root), set()
    while cur and cur.id not in seen and cur.id != ctx.user.id and len(chain) < 20:   # breadcrumb back up to the viewer
        chain.insert(0, {"user_id": cur.id, "name": cur.name})
        seen.add(cur.id)
        cur = users.get(cur.manager_id)
    return {"month": first.strftime("%Y-%m"), "root": {"user_id": root, "name": me.name if me else ""}, "path": chain, "rows": out}


@router.get("/tracking/history/person")
def history_person(user_id: int, month: str | None = None, ctx: Ctx = Depends(current)):
    first, nxt = _month_bounds(month, ctx.tenant)
    if user_id not in ctx.team_ids():
        raise HTTPException(403, "You can only view your own team.")
    rds = ctx.db.scalars(select(RouteDay).where(RouteDay.tenant_id == ctx.tid, RouteDay.user_id == user_id, RouteDay.day >= first, RouteDay.day < nxt, RouteDay.points > 0)
                         .order_by(RouteDay.day)).all()
    u = ctx.db.get(User, user_id)
    return {"user": {"id": u.id, "name": u.name}, "month": first.strftime("%Y-%m"), "days": [_row(r) for r in rds],
            "totals": {"distance_km": round(sum(r.distance_km for r in rds), 1), "est_km": round(sum(r.est_km for r in rds), 1), "days": len(rds)}}


@router.get("/tracking/history/day")
def history_day(user_id: int, day: str, ctx: Ctx = Depends(current)):
    from .. import routeday
    if user_id not in ctx.team_ids():
        raise HTTPException(403, "You can only view your own team.")
    d = parse_day(day, local_today(ctx.tenant))
    rd = ctx.db.scalar(select(RouteDay).where(RouteDay.tenant_id == ctx.tid, RouteDay.user_id == user_id, RouteDay.day == d))
    if rd is None or d >= local_today(ctx.tenant):     # today (or a day not summarised yet) is built on request
        rd = routeday.build(ctx.db, ctx.tenant, user_id, d)
        ctx.db.commit()
    if rd is None:
        return {"day": d.isoformat(), "found": False}
    return {"found": True, **_row(rd, full=True)}


@router.get("/tracking/history/export")
def history_export(month: str | None = None, manager_id: int | None = None, ctx: Ctx = Depends(current)):
    """CSV: one row per person per day for everyone under the chosen manager (default: me and my whole team)."""
    import csv, io
    from fastapi.responses import Response
    first, nxt = _month_bounds(month, ctx.tenant)
    team = set(ctx.team_ids())
    root = manager_id or ctx.user.id
    if root not in team:
        raise HTTPException(403, "You can only view your own team.")
    ids = ctx.subtree_ids(root) & team
    names = {u.id: (u.name, u.employee_code) for u in ctx.db.scalars(select(User).where(User.tenant_id == ctx.tid, User.id.in_(ids)))}
    rds = ctx.db.scalars(select(RouteDay).where(RouteDay.tenant_id == ctx.tid, RouteDay.user_id.in_(ids), RouteDay.day >= first, RouteDay.day < nxt, RouteDay.points > 0)
                         .order_by(RouteDay.user_id, RouteDay.day)).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["employee", "code", "date", "tracked_km", "estimated_km_not_counted", "moving_min", "stop_min", "silent_gap_min", "tracking_quality", "stops", "auto_expense_total"])
    for r in rds:
        n, c = names.get(r.user_id, ("", ""))
        w.writerow([n, c, r.day.isoformat(), r.distance_km, r.est_km, r.moving_min, r.stop_min, r.gap_min, r.quality, len(r.stops or []), (r.expense or {}).get("total", "")])
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="routes-{first:%Y-%m}.csv"'})
