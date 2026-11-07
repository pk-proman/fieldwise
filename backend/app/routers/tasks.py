import csv
import io
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request
from pydantic import BaseModel
from sqlalchemy import select
from ..deps import Ctx, current
from ..models import Task, Customer, User, utcnow
from ..common import to_dict, get_or_404, local_today, parse_day, local_day_bounds_utc
from ..geo import haversine_m, optimize_route
from ..notify import notify
from ..models import Visit, LocationPing
from .tracking import trail_stats

router = APIRouter(tags=["tasks"])
WRITABLE = ["title", "description", "assignee_id", "customer_id", "priority", "status", "due_at", "repeat", "lat", "lng", "geofence_m", "custom"]
PRIORITIES = ("low", "medium", "high", "urgent")
REPEATS = ("", "daily", "weekly", "monthly")


def _clean(ctx: Ctx, data: dict) -> dict:
    out = {k: v for k, v in data.items() if k in WRITABLE}
    if "due_at" in out and isinstance(out["due_at"], str):
        out["due_at"] = datetime.fromisoformat(out["due_at"].replace("Z", "+00:00")).replace(tzinfo=None) if out["due_at"] else None
    if out.get("priority", "medium") not in PRIORITIES:
        raise HTTPException(422, "Priority must be low, medium, high or urgent.")
    if out.get("repeat", "") not in REPEATS:
        raise HTTPException(422, "Repeat must be daily, weekly or monthly.")
    if "status" in out and out["status"] not in ctx.setting("task_statuses", ["open", "in_progress", "done"]):
        raise HTTPException(422, "Unknown task status for this workspace.")
    if out.get("assignee_id") and out["assignee_id"] not in ctx.team_ids():
        raise HTTPException(403, "You can only assign tasks to people on your team.")
    if out.get("customer_id"):
        from ..refs import own
        c = own(ctx, Customer, out["customer_id"], "Customer")
        if out.get("lat") is None and c.lat is not None:  # inherit geo-fence centre from the customer
            out["lat"], out["lng"] = c.lat, c.lng
            out.setdefault("geofence_m", ctx.setting("geofence_default_m", 150))
    return out


@router.get("/tasks")
def list_tasks(request: Request, ctx: Ctx = Depends(current)):
    p = request.query_params
    q = select(Task, User.name).outerjoin(User, User.id == Task.assignee_id).where(Task.tenant_id == ctx.tid, Task.deleted_at.is_(None))
    ids = ctx.team_ids()
    q = q.where((Task.assignee_id.in_(ids)) | (Task.created_by == ctx.user.id) | (Task.assignee_id.is_(None)))
    if p.get("assignee_id"):
        q = q.where(Task.assignee_id == int(p["assignee_id"]))
    if p.get("mine") == "1":
        q = q.where(Task.assignee_id == ctx.user.id)
    for k in ("status", "priority"):
        if p.get(k):
            q = q.where(getattr(Task, k) == p[k])
    if p.get("q"):
        q = q.where(Task.title.ilike(f"%{p['q']}%"))
    if p.get("overdue") == "1":
        q = q.where(Task.due_at < utcnow(), Task.status != "done")
    rows = ctx.db.execute(q.order_by(Task.due_at.is_(None), Task.due_at, Task.id.desc()).limit(500)).all()
    return [{**to_dict(t), "assignee_name": n} for t, n in rows]


@router.post("/tasks")
def create_task(body: dict, ctx: Ctx = Depends(current)):
    d = _clean(ctx, body)
    if not d.get("title"):
        raise HTTPException(422, "Title is required.")
    d.setdefault("assignee_id", ctx.user.id)
    t = Task(tenant_id=ctx.tid, created_by=ctx.user.id, **d)
    ctx.db.add(t)
    if t.assignee_id != ctx.user.id:
        notify(ctx.db, ctx.tid, t.assignee_id, "New task assigned", f"{ctx.user.name} assigned: {t.title}", "task")
    ctx.db.commit()
    return to_dict(t)


@router.patch("/tasks/{tid}")
def update_task(tid: int, body: dict, ctx: Ctx = Depends(current)):
    t = get_or_404(ctx.db, Task, tid, ctx.tid)
    if t.assignee_id not in ctx.team_ids() and t.created_by != ctx.user.id:
        raise HTTPException(403, "This task belongs to someone outside your team.")
    d = _clean(ctx, body)
    if d.get("status") == "done" and t.status != "done":
        return _complete(ctx, t, body.get("lat"), body.get("lng"))
    for k, v in d.items():
        setattr(t, k, v)
    ctx.db.commit()
    return to_dict(t)


def _complete(ctx: Ctx, t: Task, lat, lng):
    if t.lat is not None and t.geofence_m:
        if lat is None or lng is None:
            raise HTTPException(422, "Turn on GPS to complete this location-based task.")
        d = haversine_m(lat, lng, t.lat, t.lng)
        if d > t.geofence_m:
            raise HTTPException(403, f"You are {int(d)} m from the task location (limit {t.geofence_m} m).")
    t.status, t.completed_at = "done", utcnow()
    step = {"daily": timedelta(days=1), "weekly": timedelta(weeks=1), "monthly": timedelta(days=30)}.get(t.repeat)
    if step:  # recurring: spawn the next occurrence
        nxt = Task(tenant_id=ctx.tid, title=t.title, description=t.description, assignee_id=t.assignee_id, created_by=t.created_by,
                   customer_id=t.customer_id, priority=t.priority, status="open", repeat=t.repeat, lat=t.lat, lng=t.lng,
                   geofence_m=t.geofence_m, due_at=(t.due_at or utcnow()) + step, custom=t.custom)
        ctx.db.add(nxt)
    ctx.db.commit()
    return to_dict(t)


@router.delete("/tasks/{tid}")
def delete_task(tid: int, ctx: Ctx = Depends(current)):
    t = get_or_404(ctx.db, Task, tid, ctx.tid)
    if t.assignee_id not in ctx.team_ids() and t.created_by != ctx.user.id:
        raise HTTPException(403, "This task belongs to someone outside your team.")
    t.deleted_at = utcnow()
    ctx.db.commit()
    return {"ok": True}


@router.post("/tasks/bulk-upload")
async def bulk_upload(file: UploadFile = File(...), ctx: Ctx = Depends(current)):
    """CSV columns: title, description, assignee_email, priority, due (YYYY-MM-DD[ HH:MM]), customer, repeat"""
    raw = (await file.read()).decode("utf-8-sig", errors="replace")
    if len(raw) > 2_000_000:
        raise HTTPException(413, "File too large (max 2 MB).")
    users = {u.email: u.id for u in ctx.db.scalars(select(User).where(User.tenant_id == ctx.tid))}
    custs = {c.name.lower(): c.id for c in ctx.db.scalars(select(Customer).where(Customer.tenant_id == ctx.tid, Customer.deleted_at.is_(None)))}
    ok, errors = 0, []
    for i, row in enumerate(csv.DictReader(io.StringIO(raw)), start=2):
        try:
            aid = users.get((row.get("assignee_email") or "").strip().lower(), ctx.user.id)
            due = None
            if (row.get("due") or "").strip():
                s = row["due"].strip()
                due = datetime.fromisoformat(s if " " in s or "T" in s else s + " 09:00")
            d = _clean(ctx, {"title": (row.get("title") or "").strip(), "description": row.get("description", ""), "assignee_id": aid,
                             "priority": (row.get("priority") or "medium").strip().lower(), "repeat": (row.get("repeat") or "").strip().lower(),
                             "customer_id": custs.get((row.get("customer") or "").strip().lower()), "due_at": due})
            if not d["title"]:
                raise ValueError("title is empty")
            ctx.db.add(Task(tenant_id=ctx.tid, created_by=ctx.user.id, **d))
            ok += 1
        except Exception as e:  # noqa
            errors.append(f"Row {i}: {getattr(e, 'detail', e)}")
    ctx.db.commit()
    return {"created": ok, "errors": errors[:50]}


# ---------------------------------- Route curation ----------------------------------
class RouteIn(BaseModel):
    start_lat: float
    start_lng: float
    customer_ids: list[int] = []
    task_ids: list[int] = []
    avg_speed_kmph: float = 25.0
    minutes_per_stop: int = 30


@router.post("/routes/optimize")
def route_optimize(body: RouteIn, ctx: Ctx = Depends(current)):
    stops = []
    for c in ctx.db.scalars(select(Customer).where(Customer.tenant_id == ctx.tid, Customer.id.in_(body.customer_ids or [0]), Customer.deleted_at.is_(None))):
        if c.lat is not None:
            stops.append({"kind": "customer", "id": c.id, "name": c.name, "lat": c.lat, "lng": c.lng, "address": c.address})
    for t in ctx.db.scalars(select(Task).where(Task.tenant_id == ctx.tid, Task.id.in_(body.task_ids or [0]), Task.deleted_at.is_(None))):
        if t.lat is not None:
            stops.append({"kind": "task", "id": t.id, "name": t.title, "lat": t.lat, "lng": t.lng, "address": ""})
    if not stops:
        raise HTTPException(422, "None of the selected stops have a map location yet.")
    if len(stops) > 40:
        raise HTTPException(422, "Plan at most 40 stops per route.")
    naive, naive_km = None, 0.0
    ordered, km = optimize_route((body.start_lat, body.start_lng), stops)
    prev = (body.start_lat, body.start_lng)
    for s in ordered:
        naive_km += haversine_m(prev[0], prev[1], s["lat"], s["lng"]) / 1000
        prev = (s["lat"], s["lng"])
    # straight-line distance * 1.3 approximates road distance
    road_km = km * 1.3
    drive_min = road_km / max(body.avg_speed_kmph, 5) * 60
    return {"stops": ordered, "distance_km": round(road_km, 1), "drive_minutes": round(drive_min),
            "total_minutes": round(drive_min + body.minutes_per_stop * len(ordered)),
            "saved_km": round(max(0, naive_km * 1.3 - road_km), 1),
            "note": "Straight-line optimisation x1.3 road factor; open in Google Maps for live traffic."}


@router.get("/routes/past")
def route_past(user_id: int | None = None, day: str | None = None, ctx: Ctx = Depends(current)):
    uid = user_id or ctx.user.id
    if uid not in ctx.team_ids():
        raise HTTPException(403, "You can only view your own team.")
    d = parse_day(day, local_today(ctx.tenant))
    s, e = local_day_bounds_utc(ctx.tenant, d)
    pings = ctx.db.scalars(select(LocationPing).where(LocationPing.tenant_id == ctx.tid, LocationPing.user_id == uid,
                                                      LocationPing.ts >= s, LocationPing.ts < e).order_by(LocationPing.ts)).all()
    visits = ctx.db.execute(select(Visit, Customer.name, Customer.lat, Customer.lng).outerjoin(Customer, Customer.id == Visit.customer_id).where(
        Visit.tenant_id == ctx.tid, Visit.user_id == uid, Visit.started_at >= s, Visit.started_at < e).order_by(Visit.started_at)).all()
    return {"stats": trail_stats(pings),
            "points": [[p.lat, p.lng] for p in pings if p.lat is not None],
            "visits": [{"id": v.id, "customer": n, "lat": la or v.lat, "lng": lo or v.lng, "started_at": v.started_at.isoformat() + "Z",
                        "ended_at": v.ended_at.isoformat() + "Z" if v.ended_at else None} for v, n, la, lo in visits]}
