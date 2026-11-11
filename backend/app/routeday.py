"""Daily route summary + automatic expense calculation. Idempotent: running it again for the same person and day refreshes the same rows."""
import logging
from datetime import date, timedelta
from sqlalchemy import select
from .models import LocationPing, RouteDay, Attendance, ExpenseClaim, User, utcnow
from .common import local_day_bounds_utc
from . import routecalc

log = logging.getLogger("fieldwise")
POLICY_DEFAULT = {"rate_per_km": 6.0, "min_km": 0.0, "daily_cap": 0.0, "allowance_daily": 0.0, "allowance_outstation": 0.0, "outstation_km": 0.0}
KEYS = tuple(POLICY_DEFAULT)


def policy_for(tenant, user) -> dict:
    """Workspace expense rules for this person: the default row, overridden by the row for their designation."""
    s = tenant.settings or {}
    pol = s.get("expense_policy") or {}
    base = dict(POLICY_DEFAULT)
    base["rate_per_km"] = float(s.get("fuel_rate_per_km", base["rate_per_km"]))      # older workspaces only had these two
    base["daily_cap"] = float(s.get("fuel_daily_cap", 0) or 0)
    for k, v in (pol.get("default") or {}).items():
        if k in KEYS:
            base[k] = float(v or 0)
    # most specific wins: everyone < role < designation < vehicle
    layers = [(pol.get("by_role") or {}).get(getattr(user, "role", "") or ""), (pol.get("by_designation") or {}).get(str(user.designation_id)) if user.designation_id is not None else None,
              (pol.get("by_vehicle") or {}).get(getattr(user, "vehicle", "") or "")]
    for layer in layers:
        for k, v in (layer or {}).items():
            if k in KEYS and v not in (None, ""):
                base[k] = float(v)
    base["mode"] = pol.get("mode", "off")
    base["include_estimated"] = bool(pol.get("include_estimated", False))
    return base


def compute_expense(tenant, user, rd: RouteDay, att: Attendance | None) -> dict:
    p = policy_for(tenant, user)
    km = rd.distance_km + (rd.est_km if p["include_estimated"] else 0)
    notes = []
    fuel = 0.0
    if km >= max(p["min_km"], 0.01) and p["rate_per_km"] > 0:
        fuel = km * p["rate_per_km"]
        if p["daily_cap"] and fuel > p["daily_cap"]:
            fuel, notes = p["daily_cap"], notes + [f"fuel capped at {p['daily_cap']:g}"]
    allowance, kind = 0.0, ""
    if att and att.check_in_at:
        out = p["outstation_km"] > 0 and rd.far_km >= p["outstation_km"] and p["allowance_outstation"] > 0
        allowance, kind = (p["allowance_outstation"], "outstation") if out else (p["allowance_daily"], "daily")
    if rd.est_km and not p["include_estimated"]:
        notes.append(f"{rd.est_km:g} km across silent gaps not counted")
    if rd.quality < 50 and rd.points:
        notes.append("low tracking quality: please review")
    return {"km": round(km, 2), "rate": p["rate_per_km"], "fuel": round(fuel, 2), "allowance": round(allowance, 2), "allowance_kind": kind,
            "total": round(fuel + allowance, 2), "notes": notes, "mode": p["mode"], "quality": rd.quality}


def build(db, tenant, user_id: int, day: date) -> RouteDay | None:
    s, e = local_day_bounds_utc(tenant, day)
    pings = db.scalars(select(LocationPing).where(LocationPing.tenant_id == tenant.id, LocationPing.user_id == user_id, LocationPing.ts >= s, LocationPing.ts < e)
                       .order_by(LocationPing.ts)).all()
    rd = db.scalar(select(RouteDay).where(RouteDay.tenant_id == tenant.id, RouteDay.user_id == user_id, RouteDay.day == day))
    if not pings:
        return rd
    r = routecalc.analyse(pings)
    if rd is None:
        rd = RouteDay(tenant_id=tenant.id, user_id=user_id, day=day)
        db.add(rd)
    rd.distance_km, rd.est_km, rd.moving_min, rd.stop_min, rd.gap_min = r["distance_km"], r["est_km"], r["moving_min"], r["stop_min"], r["gap_min"]
    rd.points, rd.quality, rd.far_km, rd.first_at, rd.last_at = r["points"], r["quality"], r["far_from_start_km"], r["first_at"], r["last_at"]
    rd.stops, rd.polyline, rd.built_at = r["stops"], r["polyline"], utcnow()
    db.flush()
    return rd


def _levels(tenant, amount: float) -> int:
    lv = (tenant.settings or {}).get("expense_levels") or []
    n = 1
    for i, l in enumerate(lv[:-1]):
        if l["upto"] is not None and amount > l["upto"]:
            n = i + 2
    return min(n, max(len(lv), 1))


def apply_expense(db, tenant, user, rd: RouteDay, att: Attendance | None):
    """off: nothing. shadow: store the calculation on the route day only (nobody is paid). on: create / refresh the system claims."""
    p = policy_for(tenant, user)
    if p["mode"] == "off":
        return None
    res = compute_expense(tenant, user, rd, att)
    rd.expense = res
    if p["mode"] != "on":
        return res
    cur = tenant.settings.get("currency", "INR") if tenant.settings else "INR"
    for cat, amt, desc in (("fuel", res["fuel"], f"{res['km']:g} km x {res['rate']:g}/km (from your tracked route)"),
                           ("da", res["allowance"], f"{res['allowance_kind']} allowance")):
        existing = db.scalar(select(ExpenseClaim).where(ExpenseClaim.tenant_id == tenant.id, ExpenseClaim.user_id == user.id, ExpenseClaim.day == rd.day,
                                                         ExpenseClaim.category == cat, ExpenseClaim.status != "rejected", ExpenseClaim.deleted_at.is_(None)))
        if existing is not None:
            if existing.source == "auto" and existing.status == "pending" and existing.level == 0 and amt > 0 and round(existing.amount, 2) != amt:
                existing.amount, existing.description = amt, desc + (" · " + "; ".join(res["notes"]) if res["notes"] else "")   # refreshed route
                existing.distance_km = res["km"] if cat == "fuel" else None
            continue    # a manual claim for the same category and day wins: never pay twice
        if amt <= 0:
            continue
        db.add(ExpenseClaim(tenant_id=tenant.id, user_id=user.id, day=rd.day, category=cat, amount=amt, currency=cur, source="auto", distance_km=res["km"] if cat == "fuel" else None,
                            description=(desc + (" · " + "; ".join(res["notes"]) if res["notes"] else ""))[:500], levels_required=_levels(tenant, amt)))
    return res


def finalize_day(db, tenant, user_id: int, day: date):
    """Refresh the route summary and the expense rules for one person-day. Never raises: tracking must not break because of this."""
    try:
        rd = build(db, tenant, user_id, day)
        user = db.get(User, user_id)
        if rd and user:
            att = db.scalar(select(Attendance).where(Attendance.tenant_id == tenant.id, Attendance.user_id == user_id, Attendance.day == day, Attendance.deleted_at.is_(None)))
            apply_expense(db, tenant, user, rd, att)
        db.commit()
        return rd
    except Exception:
        db.rollback()
        log.exception("route day build failed for user %s day %s", user_id, day)
        return None


def finalize_recent(db, tenant, days: int = 2):
    """Scheduler: rebuild the last few days for everyone who sent location points (catches days where the person never checked out)."""
    from .common import local_today
    today = local_today(tenant)
    since, _ = local_day_bounds_utc(tenant, today - timedelta(days=days - 1))
    uids = {u for (u,) in db.execute(select(LocationPing.user_id).where(LocationPing.tenant_id == tenant.id, LocationPing.ts >= since).distinct())}
    for uid in uids:
        for i in range(days):
            finalize_day(db, tenant, uid, today - timedelta(days=i))
