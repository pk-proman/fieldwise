"""Background jobs, one pass per minute: reminders, alerts, hourly ERP sync, nightly recycle-bin purge."""
import asyncio
import logging
from datetime import datetime, timedelta
from sqlalchemy import select, delete
from .db import SessionLocal
from .models import (Tenant, User, Attendance, Task, Visit, LocationPing, Lead, Customer, Contact, ExpenseClaim, Product, Branch, Followup, Meeting, Quotation, DeviceHealth, utcnow)
from .common import local_now, local_today
from .notify import notify
from .models import Notification
from . import config, devhealth

log = logging.getLogger("fieldwise.scheduler")
_last = {"hourly": None, "daily": None}


def _tick_tenant(db, t: Tenant):
    s = t.settings or {}
    now_l, now = local_now(t), utcnow()
    today = now_l.date()
    off = set(s.get("weekly_off", [6]))
    users = db.scalars(select(User).where(User.tenant_id == t.id, User.active.is_(True), User.deleted_at.is_(None), User.role != "owner")).all()
    h, m = map(int, s.get("work_start", "09:30").split(":"))
    he, me = map(int, s.get("work_end", "18:30").split(":"))
    start = now_l.replace(hour=h, minute=m, second=0, microsecond=0)
    end = now_l.replace(hour=he, minute=me, second=0, microsecond=0)
    atts = {a.user_id: a for a in db.scalars(select(Attendance).where(Attendance.tenant_id == t.id, Attendance.day == today))}
    for u in users:
        a = atts.get(u.id)
        if today.weekday() not in off and start + timedelta(minutes=15) <= now_l < end and not (a and (a.check_in_at or a.status == "on_leave")):
            notify(db, t.id, u.id, f"Time to {s.get('labels', {}).get('check_in', 'check in')}", "You haven't marked attendance yet today.", "reminder", f"ci:{today}")
        if a and a.check_in_at and not a.check_out_at and now_l >= end + timedelta(minutes=30):
            notify(db, t.id, u.id, f"Time to {s.get('labels', {}).get('check_out', 'check out')}", "Your working hours have ended.", "reminder", f"co:{today}")
        if a and a.check_in_at and not a.check_out_at:  # GPS / internet status alerts
            last = db.scalar(select(LocationPing).where(LocationPing.tenant_id == t.id, LocationPing.user_id == u.id).order_by(LocationPing.ts.desc()).limit(1))
            lim = s.get("gps_off_alert_min", 15)
            silent = last is None or (now - last.ts) > timedelta(minutes=lim)
            if (last and not last.gps_on) or silent:
                dh = db.scalar(select(DeviceHealth).where(DeviceHealth.tenant_id == t.id, DeviceHealth.user_id == u.id))
                why = devhealth.likely_reason(dh.data if dh else None)   # the phone's own last report: battery restriction, permission, location off...
                body = f"{u.name}'s GPS is off or silent for over {lim} min." + (f" Likely cause: {why}." if why else "")
                notify(db, t.id, u.id, "GPS is OFF", "Turn on location so your day is tracked." + (f" {why}." if why else ""), "gps", f"gps:{u.id}:{today}:{now_l.hour}")
                if u.manager_id:
                    notify(db, t.id, u.manager_id, "Team GPS alert", body, "gps", f"gpsm:{u.id}:{today}:{now_l.hour}")
    for tk in db.scalars(select(Task).where(Task.tenant_id == t.id, Task.deleted_at.is_(None), Task.status != "done", Task.reminded.is_(False),
                                            Task.due_at.is_not(None), Task.due_at <= now + timedelta(minutes=60))):
        if tk.assignee_id:
            late = tk.due_at < now
            notify(db, t.id, tk.assignee_id, "Task overdue" if late else "Task due within an hour", tk.title, "task")
        tk.reminded = True
    # follow-up & meeting reminders
    for f in db.scalars(select(Followup).where(Followup.tenant_id == t.id, Followup.status == "pending", Followup.deleted_at.is_(None), Followup.reminded.is_(False), Followup.due_at <= now + timedelta(minutes=30))):
        late = f.due_at < now - timedelta(minutes=30)
        notify(db, t.id, f.owner_id, "Follow-up overdue" if late else "Follow-up due soon", f"{f.kind.title()}: {f.notes[:100]}", "followup")
        f.reminded = True
    for m in db.scalars(select(Meeting).where(Meeting.tenant_id == t.id, Meeting.status == "scheduled", Meeting.deleted_at.is_(None), Meeting.reminded.is_(False), Meeting.scheduled_at <= now + timedelta(minutes=30), Meeting.scheduled_at > now - timedelta(hours=2))):
        notify(db, t.id, m.owner_id, "Meeting starting soon", f"{m.title} at {m.scheduled_at + (now_l.utcoffset() or timedelta()):%H:%M}", "meeting")
        m.reminded = True
    # stale-lead nudge (weekly per lead)
    for l in db.scalars(select(Lead).where(Lead.tenant_id == t.id, Lead.deleted_at.is_(None), Lead.status.notin_(["won", "lost"]), Lead.owner_id.is_not(None), Lead.stage_changed_at < now - timedelta(days=14))).all()[:200]:
        notify(db, t.id, l.owner_id, "Lead going cold", f"'{l.name}' has been in {l.status} for 14+ days.", "lead", f"stale:{l.id}:{today.isocalendar()[1]}")
    # daily call report digest to managers after 19:30 local
    if s.get("dcr_digest", True) and now_l.hour * 60 + now_l.minute >= 19 * 60 + 30:
        from .routers.reports import dcr_data
        from .notify import send_email
        for mgr in [u for u in users if u.role in ("manager", "admin")]:
            if db.scalar(select(Notification.id).where(Notification.tenant_id == t.id, Notification.user_id == mgr.id, Notification.dedupe_key == f"dcr:{today}")):
                continue
            team, seen = [], {mgr.id}
            frontier = {mgr.id}
            while frontier:
                nxt = {x.id for x in users if x.manager_id in frontier} - seen
                seen |= nxt; frontier = nxt; team += [x for x in users if x.id in nxt]
            if not team:
                continue
            rows = [dcr_data(db, t, x.id, today) for x in team]
            body = "\n".join(f"{r['name']}: {r['status']}, {r['km']} km, {r['visits']} visits, {r['meetings']} meetings, {r['calls_done']} calls ({r['productive_calls']} productive), {r['quotations']} quotes" for r in rows)
            notify(db, t.id, mgr.id, f"Daily call report - {today:%d %b}", body[:590], "report", f"dcr:{today}")
            send_email(mgr.email, f"Daily call report {today}", body)
    wait_min = s.get("waiting_alert_min", 20)
    for v in db.scalars(select(Visit).where(Visit.tenant_id == t.id, Visit.ended_at.is_(None), Visit.otp_verified.is_(False), Visit.started_at <= now - timedelta(minutes=wait_min))):
        notify(db, t.id, v.user_id, "Waiting time alert", f"You have waited over {wait_min} min at this visit without verification.", "waiting", f"wait:{v.id}")
        u = db.get(User, v.user_id)
        if u and u.manager_id:
            notify(db, t.id, u.manager_id, "Waiting time alert", f"{u.name} has been waiting {wait_min}+ min at a customer.", "waiting", f"waitm:{v.id}")


def _hourly(db):
    from .integrations import erpnext as erp
    for t in db.scalars(select(Tenant).where(Tenant.active.is_(True))):
        cfg = (t.settings or {}).get("erpnext") or {}
        if cfg.get("enabled"):
            for d in ("products", "sales_team", "crm", "attendance", "invoices"):
                try:
                    erp.run_sync(t.id, d)
                except Exception as e:  # logged to SyncLog by run_sync
                    log.warning("ERP %s sync failed for tenant %s: %s", d, t.id, e)


def _daily(db):
    from .routers.compliance import purge_old_data
    try:
        purge_old_data(db)
    except Exception:
        db.rollback()
        log.exception("retention purge failed")
    from .routers.beats import extend_all, mark_missed
    from .routers.crmx import auto_regret, renewal_reminders
    try:
        extend_all(db)
        mark_missed(db)
        auto_regret(db)
        renewal_reminders(db)
        from .routers.fieldx import run_due_schedules
        run_due_schedules(db)
    except Exception:
        db.rollback()
        log.exception("beat daily jobs failed")
    from . import routeday
    for t in db.scalars(select(Tenant).where(Tenant.active.is_(True), Tenant.kind == "client")).all():
        try:
            routeday.finalize_recent(db, t, 2)
        except Exception:
            db.rollback()
            log.exception("route day rebuild failed for tenant %s", t.id)
    cutoff = utcnow() - timedelta(days=config.RECYCLE_DAYS)
    for m in (Lead, Customer, Contact, Task, ExpenseClaim, Product, Branch):
        db.execute(delete(m).where(m.deleted_at.is_not(None), m.deleted_at < cutoff))
    db.commit()


def run_once():
    db = SessionLocal()
    try:
        for t in db.scalars(select(Tenant).where(Tenant.active.is_(True))).all():
            try:
                _tick_tenant(db, t)
                db.commit()
            except Exception:
                db.rollback()
                log.exception("scheduler tenant %s failed", t.id)
        now = utcnow()
        if _last["hourly"] is None or now - _last["hourly"] > timedelta(hours=1):
            _last["hourly"] = now
            _hourly(db)
        if _last["daily"] is None or now - _last["daily"] > timedelta(days=1):
            _last["daily"] = now
            _daily(db)
    finally:
        db.close()


import os
import socket
from sqlalchemy import update, insert
from .models import JobLock
_HOLDER = f"{socket.gethostname()}:{os.getpid()}"


def acquire_leader(ttl: int = 150) -> bool:
    """Only one app process runs the scheduler: an atomic compare-and-set on a lock row (works on SQLite and PostgreSQL)."""
    db = SessionLocal()
    try:
        now = utcnow()
        if not db.get(JobLock, "scheduler"):
            try:
                db.execute(insert(JobLock).values(name="scheduler", holder="", until=None))
                db.commit()
            except Exception:
                db.rollback()
        r = db.execute(update(JobLock).where(JobLock.name == "scheduler", (JobLock.holder == _HOLDER) | (JobLock.until.is_(None)) | (JobLock.until < now)).values(holder=_HOLDER, until=now + timedelta(seconds=ttl)))
        db.commit()
        return r.rowcount == 1
    finally:
        db.close()


async def loop():
    await asyncio.sleep(5)
    while True:
        try:
            if await asyncio.to_thread(acquire_leader):
                await asyncio.to_thread(run_once)
        except Exception:
            log.exception("scheduler pass failed")
        await asyncio.sleep(60)
