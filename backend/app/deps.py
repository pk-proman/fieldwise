from datetime import datetime
from fastapi import Depends, HTTPException, Header, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
import jwt
from .db import get_db
from .models import User, Tenant, utcnow
from .security import decode_token

ROLE_RANK = {"employee": 1, "manager": 2, "admin": 3, "owner": 4, "superadmin": 5}


class Ctx:
    def __init__(self, user: User, tenant: Tenant, db: Session):
        self.user, self.tenant, self.db = user, tenant, db
        self.impersonated_by = None

    @property
    def tid(self):
        return self.tenant.id

    def is_at_least(self, role: str) -> bool:
        return ROLE_RANK.get(self.user.role, 0) >= ROLE_RANK[role]

    def team_ids(self) -> list[int]:
        """Self + all (transitive) reports. Admin/owner see everyone."""
        if self.is_at_least("admin"):
            return [u for (u,) in self.db.execute(select(User.id).where(User.tenant_id == self.tid, User.deleted_at.is_(None)))]
        ids, frontier = {self.user.id}, {self.user.id}
        while frontier:
            rows = self.db.execute(select(User.id).where(User.tenant_id == self.tid, User.manager_id.in_(frontier))).all()
            frontier = {r[0] for r in rows} - ids
            ids |= frontier
        return list(ids)

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
    if user.session_id != claims["sid"]:
        raise HTTPException(401, "Signed in on another device. Please sign in again.")
    tenant = db.get(Tenant, user.tenant_id)
    if not tenant or not tenant.active:
        raise HTTPException(403, "This workspace is suspended. Contact support.")
    # Mandatory first step: until the client's administrator finishes the setup wizard, only administrators can use the workspace.
    if tenant.kind == "client" and tenant.setup_completed_at is None and user.role not in ("owner", "admin"):
        raise HTTPException(403, "SETUP_PENDING: Your company's setup is not finished yet. Please ask your administrator to complete it.")
    user.last_seen = utcnow()
    ctx = Ctx(user, tenant, db)
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
