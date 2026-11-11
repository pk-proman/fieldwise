from datetime import datetime
from fastapi import Depends, HTTPException, Header, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
import jwt
from .db import get_db
from .models import User, Tenant, AppDevice, utcnow
from .security import decode_token

ROLE_RANK = {"employee": 1, "manager": 2, "admin": 3, "owner": 4, "superadmin": 5}


class Ctx:
    def __init__(self, user: User, tenant: Tenant, db: Session):
        self.user, self.tenant, self.db = user, tenant, db
        self.impersonated_by = None
        self.device = None   # AppDevice when the request comes from an activated phone

    @property
    def tid(self):
        return self.tenant.id

    @property
    def field_only(self) -> bool:
        """A phone activated with a field-only PIN never gets setup, admin or team tools, whatever the person's role is on the web."""
        return bool(self.device and self.device.scope == "field")

    def is_at_least(self, role: str) -> bool:
        rank = 1 if self.field_only else ROLE_RANK.get(self.user.role, 0)
        return rank >= ROLE_RANK[role]

    def subtree_ids(self, root: int) -> set[int]:
        """root + every (transitive) report. The single source of truth for "who is under this person"."""
        ids, frontier = {root}, {root}
        while frontier:
            rows = self.db.execute(select(User.id).where(User.tenant_id == self.tid, User.deleted_at.is_(None), User.manager_id.in_(frontier))).all()
            frontier = {r[0] for r in rows} - ids
            ids |= frontier
        return ids

    def direct_report_ids(self, root: int) -> list[int]:
        return [u for (u,) in self.db.execute(select(User.id).where(User.tenant_id == self.tid, User.deleted_at.is_(None), User.manager_id == root))]

    def team_ids(self) -> list[int]:
        """Self + all (transitive) reports. Admin/owner see everyone."""
        if self.field_only:
            return [self.user.id]
        if self.is_at_least("admin"):
            return [u for (u,) in self.db.execute(select(User.id).where(User.tenant_id == self.tid, User.deleted_at.is_(None)))]
        return list(self.subtree_ids(self.user.id))

    def setting(self, key, default=None):
        return (self.tenant.settings or {}).get(key, default)


def _auth(db: Session, token: str) -> Ctx:
    try:
        claims = decode_token(token)
    except jwt.PyJWTError:
        raise HTTPException(401, "Session expired. Please sign in again.")
    user = db.get(User, int(claims["sub"]))
    if not user or user.deleted_at or not user.active or user.tenant_id != claims["tid"]:
        raise HTTPException(401, "Account not available.")
    device = None
    if claims.get("did"):
        device = db.get(AppDevice, int(claims["did"]))
        if not device or device.status != "active" or device.user_id != user.id:
            raise HTTPException(401, "DEVICE_REVOKED: This phone is no longer registered. Ask your administrator for a new PIN.")
        device.last_seen = utcnow()
    elif user.session_id != claims["sid"]:
        raise HTTPException(401, "Signed in on another device. Please sign in again.")
    tenant = db.get(Tenant, user.tenant_id)
    if not tenant or not tenant.active:
        raise HTTPException(403, "This workspace is suspended. Contact support.")
    # Mandatory first step: until the client's administrator finishes the setup wizard, only administrators can use the workspace.
    if tenant.kind == "client" and tenant.setup_completed_at is None and user.role not in ("owner", "admin"):
        raise HTTPException(403, "SETUP_PENDING: Your company's setup is not finished yet. Please ask your administrator to complete it.")
    user.last_seen = utcnow()
    ctx = Ctx(user, tenant, db)
    ctx.device = device
    ctx.impersonated_by = claims.get("imp")   # set when the vendor's super-admin is acting as this client's administrator
    return ctx


def current(authorization: str = Header(default=""), db: Session = Depends(get_db)) -> Ctx:
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Sign in required.")
    ctx = _auth(db, authorization[7:])
    db.commit()
    return ctx


def require_platform(ctx: Ctx = Depends(current)) -> Ctx:
    """Vendor console only: a super-admin signed in to the platform workspace."""
    if ctx.user.role != "superadmin" or ctx.tenant.kind != "platform":
        raise HTTPException(403, "Platform administrators only.")
    return ctx


def require(role: str):
    def dep(ctx: Ctx = Depends(current)) -> Ctx:
        if not ctx.is_at_least(role):
            raise HTTPException(403, f"This action needs {role} access.")
        return ctx
    return dep
