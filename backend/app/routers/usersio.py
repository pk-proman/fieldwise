"""Team import / export (CSV). Import is a two-step flow: a dry run that reports what would happen row by row, then the real run."""
import csv
import io
import secrets
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import Response
from sqlalchemy import select, func
from ..deps import Ctx, require
from ..models import User, Branch, Department, Designation
from ..security import hash_password
from ..audit import audit
import re

router = APIRouter(tags=["team-io"])

COLUMNS = ["name", "email", "phone", "role", "employee_code", "reporting_manager", "branch", "department", "designation", "territory", "vehicle", "active"]
HELP = {"reporting_manager": "email or employee code of the manager (must be in this file or already exist)", "role": "employee | manager | admin (blank = employee)",
        "active": "yes / no (blank = yes)", "branch": "office name as set up in Settings", "department": "must exist", "designation": "must exist"}
ROLES = ("employee", "manager", "admin")
MAX_ROWS = 2000


def _csv(rows: list[list]) -> Response:
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8")


@router.get("/users/import-template")
def template(ctx: Ctx = Depends(require("admin"))):
    r = _csv([COLUMNS, ["Asha Rao", "asha@example.com", "9876543210", "employee", "E101", "ravi@example.com", "", "", "", "North", "bike", "yes"]])
    r.headers["Content-Disposition"] = 'attachment; filename="team-import-template.csv"'
    return r


@router.get("/users/export")
def export(ctx: Ctx = Depends(require("manager"))):
    ids = set(ctx.team_ids())
    users = list(ctx.db.scalars(select(User).where(User.tenant_id == ctx.tid, User.deleted_at.is_(None), User.id.in_(ids)).order_by(User.name)))
    by = {u.id: u for u in ctx.db.scalars(select(User).where(User.tenant_id == ctx.tid))}
    brs = {b.id: b.name for b in ctx.db.scalars(select(Branch).where(Branch.tenant_id == ctx.tid))}
    deps = {d.id: d.name for d in ctx.db.scalars(select(Department).where(Department.tenant_id == ctx.tid))}
    dsg = {d.id: d.name for d in ctx.db.scalars(select(Designation).where(Designation.tenant_id == ctx.tid))}
    rows = [COLUMNS]
    for u in users:
        m = by.get(u.manager_id)
        rows.append([u.name, u.email, u.phone, u.role, u.employee_code, (m.email if m else ""), brs.get(u.branch_id, ""), deps.get(u.department_id, ""),
                     dsg.get(u.designation_id, ""), u.territory, u.vehicle, "yes" if u.active else "no"])
    audit(ctx, "users.export", "user", None, f"{len(users)} rows")
    ctx.db.commit()
    r = _csv(rows)
    r.headers["Content-Disposition"] = 'attachment; filename="team.csv"'
    return r


def _truthy(v: str, default=True) -> bool:
    v = (v or "").strip().lower()
    return default if not v else v in ("yes", "y", "true", "1", "active")


@router.post("/users/import")
async def import_users(file: UploadFile = File(...), commit: bool = Form(False), ctx: Ctx = Depends(require("admin"))):
    raw = (await file.read()).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(raw))
    if not reader.fieldnames:
        raise HTTPException(422, "The file is empty.")
    norm = {(h or "").strip().lower().replace(" ", "_"): h for h in reader.fieldnames}
    if "email" not in norm or "name" not in norm:
        raise HTTPException(422, "The file needs at least the columns: name, email. Download the template to see all columns.")
    rows = []
    for i, r in enumerate(reader, start=2):
        rec = {k: (r.get(h) or "").strip() for k, h in norm.items()}
        if any(rec.values()):
            rows.append((i, rec))
    if len(rows) > MAX_ROWS:
        raise HTTPException(413, f"At most {MAX_ROWS} rows per file.")
    db, tid = ctx.db, ctx.tid
    existing = list(db.scalars(select(User).where(User.tenant_id == tid, User.deleted_at.is_(None))))
    by_email = {u.email.lower(): u for u in existing}
    by_code = {u.employee_code.lower(): u for u in existing if u.employee_code}
    branches = {b.name.lower(): b.id for b in db.scalars(select(Branch).where(Branch.tenant_id == tid, Branch.deleted_at.is_(None)))}
    deps = {d.name.lower(): d.id for d in db.scalars(select(Department).where(Department.tenant_id == tid))}
    dsg = {d.name.lower(): d.id for d in db.scalars(select(Designation).where(Designation.tenant_id == tid))}
    seats_free = ctx.tenant.seat_limit - db.scalar(select(func.count(User.id)).where(User.tenant_id == tid, User.deleted_at.is_(None), User.active.is_(True)))
    errors, plan, seen_emails = [], [], set()
    for i, rec in rows:
        email = rec.get("email", "").lower()
        err = None
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            err = "Invalid email."
        elif email in seen_emails:
            err = "Email appears twice in the file."
        elif len(rec.get("name", "")) < 2:
            err = "Name is required."
        role = (rec.get("role") or "employee").lower()
        if not err and role not in ROLES:
            err = "Role must be employee, manager or admin."
        for key, table, label in (("branch", branches, "Office"), ("department", deps, "Department"), ("designation", dsg, "Designation")):
            if not err and rec.get(key) and rec[key].lower() not in table:
                err = f"{label} '{rec[key]}' does not exist."
        u = by_email.get(email) or (by_code.get(rec.get("employee_code", "").lower()) if rec.get("employee_code") else None)
        if not err and u and u.role == "owner":
            err = "The owner account cannot be changed by import."
        if err:
            errors.append({"row": i, "email": email, "error": err})
            continue
        seen_emails.add(email)
        plan.append({"row": i, "email": email, "rec": rec, "role": role, "user": u})
    # reporting managers: resolve against the file first, then existing users
    file_by_key = {}
    for p in plan:
        file_by_key[p["email"]] = p["email"]
        if p["rec"].get("employee_code"):
            file_by_key[p["rec"]["employee_code"].lower()] = p["email"]
    mgr_of = {}
    for p in list(plan):
        key = p["rec"].get("reporting_manager", "").lower()
        if not key:
            continue
        target = file_by_key.get(key) or (by_email[key].email.lower() if key in by_email else None) or (by_code[key].email.lower() if key in by_code else None)
        if not target:
            errors.append({"row": p["row"], "email": p["email"], "error": f"Reporting manager '{p['rec']['reporting_manager']}' was not found."})
            plan.remove(p)
        elif target == p["email"]:
            errors.append({"row": p["row"], "email": p["email"], "error": "A person cannot report to themselves."})
            plan.remove(p)
        else:
            mgr_of[p["email"]] = target
    # cycle check on the final graph
    by_id = {u.id: u for u in existing}
    graph = {u.email.lower(): (by_id[u.manager_id].email.lower() if u.manager_id in by_id else None) for u in existing}
    for p in plan:
        if p["email"] in mgr_of:
            graph[p["email"]] = mgr_of[p["email"]]
        else:
            graph.setdefault(p["email"], None)
    def drop(p, msg):
        errors.append({"row": p["row"], "email": p["email"], "error": msg})
        plan.remove(p)
        mgr_of.pop(p["email"], None)
        graph[p["email"]] = None

    for p in list(plan):
        if p not in plan:
            continue
        chain, cur = [p["email"]], graph.get(p["email"])
        while cur and cur not in chain and len(chain) < 200:
            chain.append(cur)
            cur = graph.get(cur)
        if cur and cur in chain:   # loop: drop everyone on it
            for q in [q for q in list(plan) if q["email"] in chain[chain.index(cur):]]:
                drop(q, "That would make a reporting loop.")
    changed = True
    while changed:   # anyone whose manager row was dropped cannot be placed
        changed = False
        kept = {q["email"] for q in plan} | {e for e in by_email}
        for p in list(plan):
            m = mgr_of.get(p["email"])
            if m and m not in kept:
                drop(p, "Their reporting manager's row was skipped.")
                changed = True
    new_count = sum(1 for p in plan if not p["user"] and _truthy(p["rec"].get("active")))
    if new_count > seats_free:
        raise HTTPException(402, f"Your plan allows {ctx.tenant.seat_limit} active users; this file adds {new_count} but only {max(seats_free, 0)} seats are free.")
    created = updated = 0
    result_rows = []
    if commit:
        created_users = {}
        for p in plan:
            rec, u = p["rec"], p["user"]
            vals = dict(name=rec["name"], phone=rec.get("phone", ""), role=p["role"], employee_code=rec.get("employee_code", ""), territory=rec.get("territory", ""), vehicle=(rec.get("vehicle", "").lower() if rec.get("vehicle", "").lower() in ("bike", "car", "public", "other") else ""),
                        branch_id=branches.get(rec.get("branch", "").lower()), department_id=deps.get(rec.get("department", "").lower()),
                        designation_id=dsg.get(rec.get("designation", "").lower()), active=_truthy(rec.get("active")))
            if u:
                for k, v in vals.items():
                    if k in ("branch_id", "department_id", "designation_id") and v is None:
                        continue  # blank cell leaves the existing value
                    setattr(u, k, v)
                updated += 1
            else:
                u = User(tenant_id=tid, email=p["email"], password_hash=hash_password(secrets.token_urlsafe(24)), **vals)
                db.add(u)
                created += 1
            created_users[p["email"]] = u
        db.flush()
        allu = {**{e.lower(): x for e, x in by_email.items()}, **created_users}
        for p in plan:
            if p["email"] in mgr_of:
                created_users[p["email"]].manager_id = allu[mgr_of[p["email"]]].id
        audit(ctx, "users.import", "user", None, f"created {created}, updated {updated}, skipped {len(errors)}")
        db.commit()
    else:
        created = sum(1 for p in plan if not p["user"])
        updated = len(plan) - created
    for p in plan:
        result_rows.append({"row": p["row"], "email": p["email"], "action": "update" if p["user"] else "create", "manager": mgr_of.get(p["email"], "")})
    return {"committed": commit, "created": created, "updated": updated, "skipped": len(errors), "errors": sorted(errors, key=lambda e: e["row"]),
            "rows": result_rows[:200], "note": "New people have no password: give each an app PIN (Team page) or send a password-setting invite."}
