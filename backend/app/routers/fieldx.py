"""Visit forms & checklists, daily allowance, expense approval levels, announcements, scheduled e-reports."""
import csv
import io
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.orm.attributes import flag_modified
from ..deps import Ctx, current, require
from ..models import (FormTemplate, FormResponse, Visit, Meeting, Customer, ExpenseClaim, Attendance, Branch, LocationPing, User, Announcement, ReportSchedule, Tenant, utcnow)
from ..common import to_dict, get_or_404, local_today, local_now, parse_day, decode_data_url, save_upload, csv_response_text, local_day_bounds_utc
from ..geo import haversine_m
from ..notify import notify
from .. import svc

router = APIRouter(tags=["field-extras"])
FIELD_TYPES = ("text", "number", "select", "checkbox", "rating", "photo", "signature", "date")


# ================================================================== forms
class FormIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    applies_to: str = "visit"
    fields: list[dict] = Field(min_length=1, max_length=60)
    mandatory: bool = False
    active: bool = True


def _clean_fields(fields: list[dict]) -> list[dict]:
    out, seen = [], set()
    for f in fields:
        label = str(f.get("label", "")).strip()
        typ = f.get("type", "text")
        if not label or typ not in FIELD_TYPES:
            raise HTTPException(422, "Every question needs a label and a valid type.")
        key = "".join(c if c.isalnum() else "_" for c in (f.get("key") or label).lower()).strip("_")[:40] or f"q{len(out) + 1}"
        while key in seen:
            key += "_"
        seen.add(key)
        out.append({"key": key, "label": label[:200], "type": typ, "required": bool(f.get("required")), "options": [str(o)[:80] for o in (f.get("options") or [])][:20] if typ == "select" else []})
    return out


@router.post("/forms")
def save_form(body: FormIn, id: int | None = None, ctx: Ctx = Depends(require("admin"))):
    if body.applies_to not in ("visit", "meeting", "task", "general"):
        raise HTTPException(422, "A form applies to a visit, meeting, task or general use.")
    f = ctx.db.get(FormTemplate, id) if id else None
    if f and f.tenant_id != ctx.tid:
        raise HTTPException(404, "Form not found.")
    f = f or FormTemplate(tenant_id=ctx.tid)
    f.name, f.applies_to, f.fields, f.mandatory, f.active = body.name, body.applies_to, _clean_fields(body.fields), body.mandatory, body.active
    ctx.db.add(f)
    ctx.db.commit()
    return to_dict(f)


@router.get("/forms")
def list_forms(applies_to: str | None = None, ctx: Ctx = Depends(current)):
    q = select(FormTemplate).where(FormTemplate.tenant_id == ctx.tid, FormTemplate.deleted_at.is_(None))
    if applies_to:
        q = q.where(FormTemplate.applies_to == applies_to)
    if not ctx.is_at_least("admin"):
        q = q.where(FormTemplate.active.is_(True))
    counts = dict(ctx.db.execute(select(FormResponse.template_id, func.count()).where(FormResponse.tenant_id == ctx.tid).group_by(FormResponse.template_id)).all())
    return [{**to_dict(f), "responses": counts.get(f.id, 0)} for f in ctx.db.scalars(q.order_by(FormTemplate.name))]


@router.delete("/forms/{fid}")
def del_form(fid: int, ctx: Ctx = Depends(require("admin"))):
    get_or_404(ctx.db, FormTemplate, fid, ctx.tid).deleted_at = utcnow()
    ctx.db.commit()
    return {"ok": True}


class RespIn(BaseModel):
    visit_id: int | None = None
    meeting_id: int | None = None
    task_id: int | None = None
    customer_id: int | None = None
    data: dict


@router.post("/forms/{fid}/responses")
def submit_form(fid: int, body: RespIn, ctx: Ctx = Depends(current)):
    f = get_or_404(ctx.db, FormTemplate, fid, ctx.tid)
    if not f.active:
        raise HTTPException(409, "This form is switched off.")
    out = {}
    for q in f.fields:
        v = body.data.get(q["key"])
        empty = v in (None, "", False) if q["type"] != "checkbox" else v is None
        if q["required"] and (v in (None, "") or (q["type"] == "checkbox" and v is not True)):
            raise HTTPException(422, f"'{q['label']}' is required.")
        if v in (None, ""):
            continue
        if q["type"] == "number":
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise HTTPException(422, f"'{q['label']}' must be a number.")
        elif q["type"] == "rating":
            if int(v) not in range(1, 6):
                raise HTTPException(422, f"'{q['label']}' must be 1 to 5.")
            v = int(v)
        elif q["type"] == "select" and q["options"] and v not in q["options"]:
            raise HTTPException(422, f"'{q['label']}': choose one of the listed options.")
        elif q["type"] == "checkbox":
            v = bool(v)
        elif q["type"] in ("photo", "signature"):
            raw, ext = decode_data_url(v)
            v = save_upload(ctx.tid, raw, ext)
        out[q["key"]] = v
    if body.visit_id:
        v = get_or_404(ctx.db, Visit, body.visit_id, ctx.tid)
        if v.user_id != ctx.user.id and not ctx.is_at_least("manager"):
            raise HTTPException(403, "This is not your visit.")
    r = FormResponse(tenant_id=ctx.tid, template_id=f.id, user_id=ctx.user.id, visit_id=body.visit_id, meeting_id=body.meeting_id, task_id=body.task_id, customer_id=body.customer_id, data=out)
    ctx.db.add(r)
    ctx.db.commit()
    return to_dict(r)


@router.get("/forms/responses")
def responses(template_id: int | None = None, visit_id: int | None = None, customer_id: int | None = None, fmt: str = "json", ctx: Ctx = Depends(current)):
    q = select(FormResponse).where(FormResponse.tenant_id == ctx.tid, FormResponse.deleted_at.is_(None), FormResponse.user_id.in_(ctx.team_ids()))
    for col, v in ((FormResponse.template_id, template_id), (FormResponse.visit_id, visit_id), (FormResponse.customer_id, customer_id)):
        if v:
            q = q.where(col == v)
    users = {u.id: u.name for u in ctx.db.scalars(select(User).where(User.tenant_id == ctx.tid))}
    tpl = {t.id: t for t in ctx.db.scalars(select(FormTemplate).where(FormTemplate.tenant_id == ctx.tid))}
    cust = {c.id: c.name for c in ctx.db.scalars(select(Customer).where(Customer.tenant_id == ctx.tid))}
    rows = [{**to_dict(r), "user_name": users.get(r.user_id), "form": tpl[r.template_id].name if r.template_id in tpl else "?", "customer_name": cust.get(r.customer_id)} for r in ctx.db.scalars(q.order_by(FormResponse.id.desc()).limit(1000))]
    if fmt == "csv" and template_id and template_id in tpl:
        keys = [f["key"] for f in tpl[template_id].fields]
        return PlainTextResponse(csv_response_text([{"when": r["created_at"], "by": r["user_name"], "customer": r["customer_name"], **{k: r["data"].get(k, "") for k in keys}} for r in rows]), media_type="text/csv")
    return rows


def pending_mandatory(db, tenant_id: int, visit_id: int) -> list[str]:
    """Names of mandatory visit forms that have not been filled for this visit."""
    need = db.scalars(select(FormTemplate).where(FormTemplate.tenant_id == tenant_id, FormTemplate.applies_to == "visit", FormTemplate.mandatory.is_(True), FormTemplate.active.is_(True), FormTemplate.deleted_at.is_(None))).all()
    done = {t for (t,) in db.execute(select(FormResponse.template_id).where(FormResponse.tenant_id == tenant_id, FormResponse.visit_id == visit_id))}
    return [f.name for f in need if f.id not in done]


# ================================================================== daily allowance (DA)
@router.get("/da-settings")
def da_settings(ctx: Ctx = Depends(current)):
    d = dict((ctx.tenant.settings or {}).get("da") or {})
    return {"enabled": d.get("enabled", False), "rates": d.get("rates", {"metro": 0, "other": 0, "outstation": 0}), "metro_keywords": d.get("metro_keywords", ["Mumbai", "Delhi", "Bengaluru", "Bangalore", "Chennai", "Hyderabad", "Kolkata", "Pune", "Dubai", "Riyadh"]),
            "outstation_km": d.get("outstation_km", 60), "min_hours": d.get("min_hours", 6), "levels": (ctx.tenant.settings or {}).get("expense_levels", [])}


@router.put("/da-settings")
def put_da(body: dict, ctx: Ctx = Depends(require("admin"))):
    s = dict(ctx.tenant.settings or {})
    s["da"] = {"enabled": bool(body.get("enabled")), "rates": {k: max(0.0, float((body.get("rates") or {}).get(k, 0))) for k in ("metro", "other", "outstation")},
               "metro_keywords": [str(x).strip() for x in (body.get("metro_keywords") or []) if str(x).strip()][:60], "outstation_km": max(1, float(body.get("outstation_km", 60))), "min_hours": max(0, float(body.get("min_hours", 6)))}
    if "levels" in body:
        lv = sorted(([{"upto": (None if l.get("upto") in (None, "") else float(l["upto"])), "role": l.get("role")} for l in body["levels"]]), key=lambda l: (l["upto"] is None, l["upto"] or 0))
        if any(l["role"] not in ("manager", "admin", "owner") for l in lv):
            raise HTTPException(422, "Each approval level needs a role: manager, admin or owner.")
        if lv and lv[-1]["upto"] is not None:
            raise HTTPException(422, "The last approval level must have no upper limit.")
        s["expense_levels"] = lv
    ctx.tenant.settings = s
    flag_modified(ctx.tenant, "settings")
    ctx.db.commit()
    return da_settings(ctx)


@router.post("/expenses/da-auto")
def da_auto(body: dict, ctx: Ctx = Depends(current)):
    """Daily allowance by location tier: metro / other / outstation (far from the home branch)."""
    cfg = da_settings(ctx)
    if not cfg["enabled"]:
        raise HTTPException(422, "Daily allowance isn't switched on for this workspace.")
    d = parse_day(body.get("day"), local_today(ctx.tenant))
    a = ctx.db.scalar(select(Attendance).where(Attendance.tenant_id == ctx.tid, Attendance.user_id == ctx.user.id, Attendance.day == d, Attendance.check_in_at.is_not(None), Attendance.check_out_at.is_not(None)))
    if not a:
        raise HTTPException(422, "Daily allowance is available after you check out for that day.")
    hours = (a.check_out_at - a.check_in_at).total_seconds() / 3600
    if hours < cfg["min_hours"]:
        raise HTTPException(422, f"You need at least {cfg['min_hours']:g} working hours; you logged {hours:.1f}.")
    if ctx.db.scalar(select(ExpenseClaim.id).where(ExpenseClaim.tenant_id == ctx.tid, ExpenseClaim.user_id == ctx.user.id, ExpenseClaim.day == d, ExpenseClaim.category == "da", ExpenseClaim.status != "rejected", ExpenseClaim.deleted_at.is_(None))):
        raise HTTPException(409, "A daily allowance claim for that day already exists.")
    home = ctx.db.get(Branch, ctx.user.branch_id) if ctx.user.branch_id else ctx.db.scalar(select(Branch).where(Branch.tenant_id == ctx.tid, Branch.deleted_at.is_(None)))
    far = 0.0
    if home:
        for p in ctx.db.scalars(select(LocationPing).where(LocationPing.tenant_id == ctx.tid, LocationPing.user_id == ctx.user.id, LocationPing.ts >= a.check_in_at, LocationPing.ts <= a.check_out_at, LocationPing.lat.is_not(None), LocationPing.mock.is_(False))):
            far = max(far, haversine_m(home.lat, home.lng, p.lat, p.lng) / 1000)
    tier = "outstation" if far > cfg["outstation_km"] else ("metro" if home and any(k.lower() in f"{home.name} {home.address}".lower() for k in cfg["metro_keywords"]) else "other")
    rate = float(cfg["rates"].get(tier, 0))
    if rate <= 0:
        raise HTTPException(422, f"No daily allowance rate is set for the '{tier}' tier.")
    e = ExpenseClaim(tenant_id=ctx.tid, user_id=ctx.user.id, day=d, category="da", amount=rate, currency=ctx.setting("currency", "INR"), description=f"Daily allowance ({tier}; {hours:.1f} h; furthest {far:.0f} km from base)")
    from .expenses import _levels_for
    e.levels_required = _levels_for(ctx, rate)
    ctx.db.add(e)
    if ctx.user.manager_id:
        notify(ctx.db, ctx.tid, ctx.user.manager_id, "Expense to approve", f"{ctx.user.name}: DA {e.currency} {rate:g}", "approval")
    ctx.db.commit()
    return to_dict(e)


# ================================================================== announcements
class AnnIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    body: str = ""
    audience: str = "all"
    user_ids: list[int] = []
    pinned: bool = False
    expires_in_days: int | None = Field(None, ge=1, le=365)


@router.post("/announcements")
def post_announcement(body: AnnIn, ctx: Ctx = Depends(require("manager"))):
    if body.audience not in ("all", "team", "users"):
        raise HTTPException(422, "Audience must be all, team or users.")
    team = set(ctx.team_ids())
    a = Announcement(tenant_id=ctx.tid, title=body.title, body=body.body, audience=body.audience, user_ids=[u for u in body.user_ids if u in team] if body.audience == "users" else [], pinned=body.pinned, created_by=ctx.user.id,
                     expires_at=utcnow() + timedelta(days=body.expires_in_days) if body.expires_in_days else None, read_by=[ctx.user.id])
    ctx.db.add(a)
    targets = {u for (u,) in ctx.db.execute(select(User.id).where(User.tenant_id == ctx.tid, User.active.is_(True), User.deleted_at.is_(None)))} if body.audience == "all" else (team if body.audience == "team" else set(a.user_ids))
    for uid in targets - {ctx.user.id}:
        notify(ctx.db, ctx.tid, uid, "📣 " + body.title, body.body[:200], "announcement")
    ctx.db.commit()
    return to_dict(a)


def _visible(ctx: Ctx, a: Announcement) -> bool:
    if a.created_by == ctx.user.id or a.audience == "all":
        return True
    if a.audience == "users":
        return ctx.user.id in (a.user_ids or [])
    author = ctx.db.get(User, a.created_by)
    return bool(author) and ctx.user.id in {u for (u,) in ctx.db.execute(select(User.id).where(User.tenant_id == ctx.tid))} and _in_subtree(ctx, author.id)


def _in_subtree(ctx: Ctx, root: int) -> bool:
    """True when the current user is `root` or reports (directly or not) to `root`."""
    return ctx.user.id in ctx.subtree_ids(root)


@router.get("/announcements")
def announcements(ctx: Ctx = Depends(current)):
    now = utcnow()
    users = {u.id: u.name for u in ctx.db.scalars(select(User).where(User.tenant_id == ctx.tid))}
    rows = [a for a in ctx.db.scalars(select(Announcement).where(Announcement.tenant_id == ctx.tid, Announcement.deleted_at.is_(None)).order_by(Announcement.pinned.desc(), Announcement.id.desc()).limit(100))
            if (a.expires_at is None or a.expires_at > now) and _visible(ctx, a)]
    return [{**to_dict(a), "author": users.get(a.created_by), "unread": ctx.user.id not in (a.read_by or []), "read_count": len(a.read_by or []) if a.created_by == ctx.user.id or ctx.is_at_least("admin") else None, "read_by": None} for a in rows]


@router.post("/announcements/{aid}/read")
def read_announcement(aid: int, ctx: Ctx = Depends(current)):
    a = get_or_404(ctx.db, Announcement, aid, ctx.tid)
    if ctx.user.id not in (a.read_by or []):
        a.read_by = [*(a.read_by or []), ctx.user.id]
        ctx.db.commit()
    return {"ok": True}


@router.delete("/announcements/{aid}")
def del_announcement(aid: int, ctx: Ctx = Depends(require("manager"))):
    a = get_or_404(ctx.db, Announcement, aid, ctx.tid)
    if a.created_by != ctx.user.id and not ctx.is_at_least("admin"):
        raise HTTPException(403, "Only the author or an admin can remove this.")
    a.deleted_at = utcnow()
    ctx.db.commit()
    return {"ok": True}


# ================================================================== scheduled e-reports
REPORTS = {"attendance": "Attendance (last period)", "expenses": "Expenses (last period)", "dcr": "Daily call report (yesterday)", "productivity": "Productivity & idle time (last period)", "beats": "Beat adherence (last period)"}


class SchedIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    report: str
    frequency: str = "daily"
    hour: int = Field(8, ge=0, le=23)
    recipients: str
    active: bool = True


@router.post("/report-schedules")
def save_schedule(body: SchedIn, id: int | None = None, ctx: Ctx = Depends(require("manager"))):
    if body.report not in REPORTS or body.frequency not in ("daily", "weekly", "monthly"):
        raise HTTPException(422, "Choose a report and a frequency.")
    emails = [e.strip() for e in body.recipients.split(",") if "@" in e]
    if not emails:
        raise HTTPException(422, "Add at least one recipient email.")
    s = ctx.db.get(ReportSchedule, id) if id else None
    if s and s.tenant_id != ctx.tid:
        raise HTTPException(404, "Schedule not found.")
    s = s or ReportSchedule(tenant_id=ctx.tid, created_by=ctx.user.id)
    s.name, s.report, s.frequency, s.hour, s.recipients, s.active = body.name, body.report, body.frequency, body.hour, ",".join(emails), body.active
    ctx.db.add(s)
    ctx.db.commit()
    return to_dict(s)


@router.get("/report-schedules")
def schedules(ctx: Ctx = Depends(require("manager"))):
    return {"reports": REPORTS, "items": [to_dict(s) for s in ctx.db.scalars(select(ReportSchedule).where(ReportSchedule.tenant_id == ctx.tid, ReportSchedule.deleted_at.is_(None)).order_by(ReportSchedule.id))]}


@router.delete("/report-schedules/{sid}")
def del_schedule(sid: int, ctx: Ctx = Depends(require("manager"))):
    get_or_404(ctx.db, ReportSchedule, sid, ctx.tid).deleted_at = utcnow()
    ctx.db.commit()
    return {"ok": True}


def build_report(db, tenant: Tenant, schedule: ReportSchedule) -> tuple[str, bytes]:
    """CSV for the schedule's report over its natural period, for the creator's team."""
    creator = db.get(User, schedule.created_by)
    from ..deps import Ctx as C
    ctx = C(creator, tenant, db)
    today = local_today(tenant)
    days = {"daily": 1, "weekly": 7, "monthly": 30}[schedule.frequency]
    d1, d2 = today - timedelta(days=days), today - timedelta(days=1)
    ids = ctx.team_ids()
    names = {u.id: u.name for u in db.scalars(select(User).where(User.tenant_id == tenant.id))}
    rows = []
    if schedule.report == "attendance":
        for a in db.scalars(select(Attendance).where(Attendance.tenant_id == tenant.id, Attendance.user_id.in_(ids), Attendance.day >= d1, Attendance.day <= d2).order_by(Attendance.day)):
            rows.append({"date": a.day, "employee": names.get(a.user_id), "status": a.status, "check_in": a.check_in_at, "check_out": a.check_out_at, "km": a.total_distance_km, "flags": a.flagged})
    elif schedule.report == "expenses":
        for e in db.scalars(select(ExpenseClaim).where(ExpenseClaim.tenant_id == tenant.id, ExpenseClaim.user_id.in_(ids), ExpenseClaim.day >= d1, ExpenseClaim.day <= d2, ExpenseClaim.deleted_at.is_(None))):
            rows.append({"date": e.day, "employee": names.get(e.user_id), "category": e.category, "amount": e.amount, "status": e.status, "description": e.description})
    elif schedule.report == "dcr":
        from .reports import dcr_data
        for uid in ids:
            u = db.get(User, uid)
            if u and u.active and u.role != "owner":
                rows.append(dcr_data(db, tenant, uid, d2))
    elif schedule.report == "productivity":
        from .field import analyze_session
        agg = {}
        for a in db.scalars(select(Attendance).where(Attendance.tenant_id == tenant.id, Attendance.user_id.in_(ids), Attendance.day >= d1, Attendance.day <= d2, Attendance.check_in_at.is_not(None))):
            x = analyze_session(db, tenant, a)
            r = agg.setdefault(a.user_id, {"employee": names.get(a.user_id), "days": 0, "worked_min": 0, "on_site_min": 0, "travel_min": 0, "idle_min": 0, "overtime_min": 0, "km": 0.0})
            r["days"] += 1
            for k in ("worked_min", "on_site_min", "travel_min", "idle_min", "overtime_min"):
                r[k] += x[k]
            r["km"] += x["distance_km"]
        rows = list(agg.values())
    elif schedule.report == "beats":
        from ..models import BeatVisit
        agg = {}
        for b in db.scalars(select(BeatVisit).where(BeatVisit.tenant_id == tenant.id, BeatVisit.user_id.in_(ids), BeatVisit.day >= d1, BeatVisit.day <= d2)):
            r = agg.setdefault(b.user_id, {"employee": names.get(b.user_id), "planned": 0, "visited": 0, "skipped": 0, "missed": 0})
            r["planned"] += 1
            if b.status in r:
                r[b.status] += 1
        rows = list(agg.values())
    text = csv_response_text([{k: ("" if v is None else v) for k, v in r.items()} for r in rows]) or "No data for this period\n"
    return f"{schedule.report}_{d1}_{d2}.csv", text.encode()


def send_schedule(db, tenant: Tenant, s: ReportSchedule) -> str:
    fn, data = build_report(db, tenant, s)
    ok_all, last = True, "sent"
    for to in s.recipients.split(","):
        ok, detail = svc.send_email_t(tenant, to.strip(), f"{s.name} ({REPORTS[s.report]})", f"Attached: {fn}\n\nSent automatically by Fieldwise.", [(fn, data, "text/csv")])
        if not ok:
            ok_all, last = False, detail
    s.last_sent, s.last_status = utcnow(), last[:200]
    return last


@router.post("/report-schedules/{sid}/run")
def run_now(sid: int, ctx: Ctx = Depends(require("manager"))):
    s = get_or_404(ctx.db, ReportSchedule, sid, ctx.tid)
    status = send_schedule(ctx.db, ctx.tenant, s)
    ctx.db.commit()
    return {"status": status}


def run_due_schedules(db):
    for t in db.scalars(select(Tenant).where(Tenant.active.is_(True))).all():
        now = local_now(t)
        for s in db.scalars(select(ReportSchedule).where(ReportSchedule.tenant_id == t.id, ReportSchedule.active.is_(True), ReportSchedule.deleted_at.is_(None))):
            if now.hour < s.hour:
                continue
            due = {"daily": now.date(), "weekly": now.date() if now.weekday() == 0 else None, "monthly": now.date() if now.day == 1 else None}[s.frequency]
            if not due:
                continue
            if s.last_sent and s.last_sent.replace(tzinfo=ZoneInfo("UTC")).astimezone(now.tzinfo).date() >= due:
                continue
            send_schedule(db, t, s)
    db.commit()
