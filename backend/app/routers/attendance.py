from datetime import datetime, date, timedelta, time as dtime
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, and_
from ..deps import Ctx, current
from ..models import Attendance, Branch, User, LocationPing, LeaveRequest, Holiday, utcnow
from ..common import (to_dict, local_today, local_now, parse_day, decode_data_url, save_upload, watermark_text,
                      local_day_bounds_utc, tz_of, csv_response_text)
from ..geo import haversine_m, path_km
from ..notify import notify
from .. import biometrics, config
from fastapi.responses import PlainTextResponse
from zoneinfo import ZoneInfo

router = APIRouter(prefix="/attendance", tags=["attendance"])


class PunchIn(BaseModel):
    lat: float | None = None
    lng: float | None = None
    accuracy: float | None = None
    mock: bool = False
    selfie: str | None = None            # data URL
    device_biometric: bool = False       # fingerprint verified on-device
    client_ts: datetime | None = None    # set when the punch was recorded offline (UTC)
    override_reason: str = ""


def _nearest_branch(ctx: Ctx, lat, lng):
    branches = ctx.db.scalars(select(Branch).where(Branch.tenant_id == ctx.tid, Branch.deleted_at.is_(None))).all()
    best = None
    for b in branches:
        d = haversine_m(lat, lng, b.lat, b.lng)
        if best is None or d < best[1]:
            best = (b, d)
    return best  # (branch, distance) or None


def _verify_identity(ctx: Ctx, body: PunchIn):
    selfie_path = None
    if body.selfie:
        raw, ext = decode_data_url(body.selfie)
        if ctx.user.face_ref:
            ref = (config.UPLOAD_DIR / ctx.user.face_ref).read_bytes()
            ok, sim = biometrics.compare(ref, raw)
            if not ok:
                raise HTTPException(403, "Face did not match your enrolled photo. Try again in better light.")
        else:
            ctx.user.face_ref = save_upload(ctx.tid, raw, ext)  # first selfie becomes the reference
        selfie_path = save_upload(ctx.tid, raw, ext, watermark_text(ctx.user.name, body.lat, body.lng, ctx.tenant))
    elif ctx.setting("require_face", False) and not body.device_biometric:
        raise HTTPException(422, "A face scan or fingerprint is required to punch in.")
    return selfie_path


def _check_location(ctx: Ctx, body: PunchIn, kind: str):
    flags = []
    if body.mock:
        if ctx.setting("block_mock_location", True):
            if ctx.user.manager_id:
                notify(ctx.db, ctx.tid, ctx.user.manager_id, "Mock location detected",
                       f"{ctx.user.name} tried to {kind} with a fake GPS location.", "alert")
            ctx.db.commit()
            raise HTTPException(403, "Fake/mock location detected. Turn off mock-location apps and try again.")
        flags.append("mock-location")
    if body.lat is None or body.lng is None:
        raise HTTPException(422, "Location is required. Turn on GPS and try again.")
    nb = _nearest_branch(ctx, body.lat, body.lng)
    branch_id, dist = (nb[0].id, nb[1]) if nb else (None, None)
    inside = bool(nb and nb[1] <= nb[0].radius_m)
    enforce = ctx.setting("enforce_geofence", True) if kind == "check in" else ctx.setting("checkout_geofence", True)
    if nb and not inside and enforce:
        if body.override_reason.strip():
            flags.append(f"outside-geofence({int(dist)}m): {body.override_reason.strip()[:80]}")
            if ctx.user.manager_id:
                notify(ctx.db, ctx.tid, ctx.user.manager_id, "Geo-fence override",
                       f"{ctx.user.name} {kind} {int(dist)} m outside {nb[0].name}: {body.override_reason}", "alert")
        else:
            raise HTTPException(403, f"You are {int(dist)} m from {nb[0].name} (limit {nb[0].radius_m} m). Move closer, or add a reason to request an override.")
    return branch_id, dist, flags


def _late(ctx: Ctx, when_utc: datetime) -> bool:
    from .field import shift_for
    local = when_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz_of(ctx.tenant))
    sh = shift_for(ctx.db, ctx.tenant, ctx.user, local.date())
    h, m = map(int, sh["start"].split(":"))
    limit = datetime.combine(local.date(), dtime(h, m), tzinfo=local.tzinfo) + timedelta(minutes=sh["grace"])
    return local > limit


def _today_row(ctx: Ctx, day: date):
    return ctx.db.scalar(select(Attendance).where(Attendance.tenant_id == ctx.tid, Attendance.user_id == ctx.user.id,
                                                  Attendance.day == day, Attendance.deleted_at.is_(None)))


@router.post("/check-in")
def check_in(body: PunchIn, ctx: Ctx = Depends(current)):
    from .auth import consent_needed
    if consent_needed(ctx):
        raise HTTPException(428, "CONSENT_REQUIRED")
    when = body.client_ts.replace(tzinfo=None) if body.client_ts else utcnow()
    if when < utcnow() - timedelta(days=2) or when > utcnow() + timedelta(minutes=5):
        raise HTTPException(422, "Punch time is outside the allowed window.")
    day = when.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz_of(ctx.tenant)).date()
    row = _today_row(ctx, day)
    if row and row.check_in_at and not row.check_out_at:
        raise HTTPException(409, "You are already checked in.")
    if row and row.check_out_at:
        raise HTTPException(409, "You already completed attendance for today.")
    selfie = _verify_identity(ctx, body)
    branch_id, dist, flags = _check_location(ctx, body, "check in")
    if body.client_ts:
        flags.append("offline-sync")
    status = "late" if _late(ctx, when) else "present"
    row = row or Attendance(tenant_id=ctx.tid, user_id=ctx.user.id, day=day)
    row.check_in_at, row.check_in_lat, row.check_in_lng = when, body.lat, body.lng
    row.check_in_branch_id, row.check_in_dist_m, row.check_in_selfie = branch_id, dist, selfie
    row.check_in_method = "+".join(filter(None, [("face" if biometrics.verified() else "selfie") if body.selfie else "", "fingerprint" if body.device_biometric else ""])) or "gps"
    row.status, row.flagged = status, "; ".join(flags)
    ctx.db.add(row)
    ctx.db.add(LocationPing(tenant_id=ctx.tid, user_id=ctx.user.id, ts=when, lat=body.lat, lng=body.lng, accuracy=body.accuracy, network="online", gps_on=True, mock=False))
    ctx.db.commit()
    from .. import hub
    hub.publish(ctx.tid, ctx.user.id, {"type": "ping", "user_id": ctx.user.id, "name": ctx.user.name, "lat": body.lat, "lng": body.lng, "battery": None, "network": "online", "gps_on": True, "ts": when.isoformat() + "Z"})
    return to_dict(row)


@router.post("/check-out")
def check_out(body: PunchIn, ctx: Ctx = Depends(current)):
    when = body.client_ts.replace(tzinfo=None) if body.client_ts else utcnow()
    row = ctx.db.scalar(select(Attendance).where(Attendance.tenant_id == ctx.tid, Attendance.user_id == ctx.user.id,
                                                 Attendance.check_in_at.is_not(None), Attendance.check_out_at.is_(None),
                                                 Attendance.deleted_at.is_(None)).order_by(Attendance.check_in_at.desc()))
    if not row:
        raise HTTPException(409, "You are not checked in.")
    _verify_identity(ctx, body)
    _, _, flags = _check_location(ctx, body, "check out")
    row.check_out_at, row.check_out_lat, row.check_out_lng = when, body.lat, body.lng
    pings = ctx.db.scalars(select(LocationPing).where(LocationPing.tenant_id == ctx.tid, LocationPing.user_id == ctx.user.id,
                                                      LocationPing.ts >= row.check_in_at, LocationPing.ts <= when,
                                                      LocationPing.lat.is_not(None), LocationPing.mock.is_(False)).order_by(LocationPing.ts)).all()
    row.total_distance_km = round(path_km((p.lat, p.lng) for p in pings), 2)
    hours = (when - row.check_in_at).total_seconds() / 3600
    if hours < 4:
        row.status = "half_day"
    if flags:
        row.flagged = "; ".join(filter(None, [row.flagged] + flags))
    ctx.db.commit()
    return to_dict(row)


@router.get("/today")
def today(ctx: Ctx = Depends(current)):
    row = _today_row(ctx, local_today(ctx.tenant))
    return {"attendance": to_dict(row) if row else None, "server_time": utcnow().isoformat() + "Z"}


@router.get("")
def list_attendance(frm: str | None = Query(None, alias="from"), to: str | None = None, user_id: int | None = None,
                    fmt: str = "json", ctx: Ctx = Depends(current)):
    today_ = local_today(ctx.tenant)
    d1, d2 = parse_day(frm, today_ - timedelta(days=30)), parse_day(to, today_)
    ids = ctx.team_ids()
    if user_id:
        if user_id not in ids:
            raise HTTPException(403, "You can only view your own team.")
        ids = [user_id]
    rows = ctx.db.execute(select(Attendance, User.name).join(User, User.id == Attendance.user_id).where(
        Attendance.tenant_id == ctx.tid, Attendance.user_id.in_(ids), Attendance.day >= d1, Attendance.day <= d2,
        Attendance.deleted_at.is_(None)).order_by(Attendance.day.desc(), User.name)).all()
    data = [{**to_dict(a), "user_name": n} for a, n in rows]
    if fmt == "csv":
        slim = [{"date": r["day"], "employee": r["user_name"], "status": r["status"], "check_in": r["check_in_at"],
                 "check_out": r["check_out_at"], "km": r["total_distance_km"], "flags": r["flagged"]} for r in data]
        return PlainTextResponse(csv_response_text(slim), media_type="text/csv")
    return data
