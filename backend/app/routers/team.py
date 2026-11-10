"""Sales team: the people a lead can be owned by (ERPNext Sales Persons, each tied to an employee)."""
import secrets
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from ..deps import Ctx, current, require
from ..models import SalesPerson, User, Lead
from ..common import to_dict, get_or_404
from ..refs import own
from ..security import hash_password

router = APIRouter(prefix="/sales-team", tags=["sales-team"])


def _row(sp: SalesPerson, users: dict, counts: dict) -> dict:
    u = users.get(sp.user_id)
    return {**to_dict(sp), "has_login": bool(u), "login_email": u.email if u else "", "active_login": bool(u and u.active), "open_leads": counts.get(sp.id, 0)}


@router.get("")
def list_team(all: bool = False, ctx: Ctx = Depends(current)):
    q = select(SalesPerson).where(SalesPerson.tenant_id == ctx.tid, SalesPerson.deleted_at.is_(None)).order_by(SalesPerson.name)
    if not all:
        q = q.where(SalesPerson.enabled.is_(True))
    rows = ctx.db.scalars(q).all()
    users = {u.id: u for u in ctx.db.scalars(select(User).where(User.tenant_id == ctx.tid, User.id.in_([r.user_id for r in rows if r.user_id] or [0])))}
    counts = dict(ctx.db.execute(select(Lead.sales_person_id, func.count()).where(Lead.tenant_id == ctx.tid, Lead.deleted_at.is_(None), Lead.sales_person_id.is_not(None)).group_by(Lead.sales_person_id)).all())
    return [_row(r, users, counts) for r in rows]


@router.post("/import")
def import_team(body: dict, ctx: Ctx = Depends(require("admin"))):
    """Bulk upsert (used by the one-off loader and for CSV-style pastes). Prefer the ERPNext sync."""
    from ..integrations.erpnext import upsert_sales_person
    rows = body.get("rows") or []
    if len(rows) > 2000:
        raise HTTPException(413, "At most 2000 rows.")
    n = 0
    for r in rows:
        if not r.get("erp_name") and not r.get("name"):
            continue
        r = {**r, "erp_name": r.get("erp_name") or r["name"]}
        upsert_sales_person(ctx.db, ctx.tenant, r)
        n += 1
    ctx.db.commit()
    return {"imported": n}


@router.patch("/{sid}")
def update_member(sid: int, body: dict, ctx: Ctx = Depends(require("admin"))):
    sp = get_or_404(ctx.db, SalesPerson, sid, ctx.tid)
    if "user_id" in body:
        if body["user_id"]:
            own(ctx, User, body["user_id"], "User")
        sp.user_id = body["user_id"] or None
        # leads already given to this person now belong to that login
        for l in ctx.db.scalars(select(Lead).where(Lead.tenant_id == ctx.tid, Lead.sales_person_id == sp.id, Lead.deleted_at.is_(None))):
            l.owner_id = sp.user_id
    for k in ("enabled", "email", "phone", "designation", "department", "branch"):
        if k in body:
            setattr(sp, k, body[k] if k == "enabled" else str(body[k] or "")[:200])
    ctx.db.commit()
    return to_dict(sp)


@router.post("/create-logins")
def create_logins(ctx: Ctx = Depends(require("admin"))):
    """Creates a Fieldwise login for every enabled team member who has an email and no login yet. They set their password with 'Forgot your password?'."""
    made, skipped = [], []
    used = ctx.db.scalar(select(func.count()).select_from(User).where(User.tenant_id == ctx.tid, User.active.is_(True), User.deleted_at.is_(None))) or 0
    for sp in ctx.db.scalars(select(SalesPerson).where(SalesPerson.tenant_id == ctx.tid, SalesPerson.deleted_at.is_(None), SalesPerson.enabled.is_(True), SalesPerson.user_id.is_(None)).order_by(SalesPerson.name)):
        if not sp.email:
            skipped.append({"name": sp.name, "why": "no email in ERPNext"})
            continue
        existing = ctx.db.scalar(select(User).where(User.tenant_id == ctx.tid, User.email == sp.email.lower(), User.deleted_at.is_(None)))
        if existing:
            sp.user_id = existing.id
            continue
        if used >= ctx.tenant.seat_limit:
            skipped.append({"name": sp.name, "why": "seat limit reached"})
            continue
        role = "manager" if sp.is_group else "employee"
        u = User(tenant_id=ctx.tid, email=sp.email.lower(), name=sp.name, role=role, phone=sp.phone, erp_employee=sp.employee, employee_code=sp.employee,
                 password_hash=hash_password(secrets.token_urlsafe(16)))
        ctx.db.add(u)
        ctx.db.flush()
        sp.user_id = u.id
        used += 1
        made.append(sp.name)
    for l in ctx.db.scalars(select(Lead).where(Lead.tenant_id == ctx.tid, Lead.sales_person_id.is_not(None), Lead.owner_id.is_(None), Lead.deleted_at.is_(None))):
        sp = ctx.db.get(SalesPerson, l.sales_person_id)
        if sp and sp.user_id:
            l.owner_id = sp.user_id
    ctx.db.commit()
    return {"created": made, "skipped": skipped}
