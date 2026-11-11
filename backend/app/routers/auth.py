import re
import time
from collections import defaultdict
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..db import get_db
from ..deps import Ctx, current
from ..models import Tenant, User, LeaveType, LoginAttempt, utcnow
from ..security import hash_password, verify_password, make_token, new_session_id, new_totp_secret, verify_totp, otpauth_uri, check_password, make_reset_token, read_reset_token, reset_token_matches
from ..common import to_dict, decode_data_url, save_upload
from .. import config, svc

router = APIRouter(prefix="/auth", tags=["auth"])
_fails: dict[str, list[float]] = defaultdict(list)

DEFAULT_SETTINGS = {
    "currency": "INR", "timezone": "Asia/Kolkata", "work_start": "09:30", "work_end": "18:30", "late_grace_min": 15,
    "weekly_off": [6], "fuel_rate_per_km": 6.0, "fuel_daily_cap": 800.0, "geofence_default_m": 150,
    "enforce_geofence": True, "checkout_geofence": True, "block_mock_location": True, "require_face": False,
    "require_visit_otp": True, "map_style": "streets", "labels": {"check_in": "Check In", "check_out": "Check Out"},
    "task_statuses": ["open", "in_progress", "done"], "waiting_alert_min": 20, "gps_off_alert_min": 15,
    "mom_recipients_default": "", "page_headers": {}, "discount_approval_pct": 10, "qualify_min_score": 5, "auto_followup_days": 1, "dcr_digest": True,
}


DEFAULT_POLICY = ("While you are checked in for work, this app records your location at intervals, your battery level and network status, to verify attendance and visits, calculate distance travelled and keep you safe. "
                  "It stops recording when you check out. Your manager and the administrators of your company can see this data. Records are kept for the period your company has set and can be exported or erased on request. "
                  "You can ask your administrator about how your data is used.")


def consent_needed(ctx) -> bool:
    s = ctx.tenant.settings or {}
    return bool(s.get("require_tracking_consent", True)) and ctx.user.role not in ("owner", "superadmin") and (ctx.user.consent_at is None or ctx.user.consent_version != str(s.get("tracking_policy_version", "1")))


class SignupIn(BaseModel):
    company: str = Field(min_length=2, max_length=200)
    name: str = Field(min_length=2, max_length=200)
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    country: str = "India"


class LoginIn(BaseModel):
    workspace: str
    email: str
    password: str
    device: str = ""
    otp: str = ""


def _slug(name: str, db: Session) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "workspace"
    slug, i = base, 1
    while db.scalar(select(Tenant.id).where(Tenant.slug == slug)):
        i += 1
        slug = f"{base}-{i}"
    return slug


def issue(db: Session, user: User, device: str):
    user.session_id = new_session_id()  # revokes any token held by a previous device
    user.device_label = device[:200]
    db.commit()
    t = db.get(Tenant, user.tenant_id)
    hrs = float((t.settings or {}).get("session_hours") or 0) or None
    mode = (t.settings or {}).get("mfa_mode", "off")
    return {"token": make_token(user.id, user.tenant_id, user.session_id, hrs), "user": to_dict(user), "must_enroll_mfa": mode == "required" and not user.mfa_enabled}


def bootstrap_tenant(db: Session, company, owner_name, email, password, country="India", plan="trial"):
    settings = dict(DEFAULT_SETTINGS)
    if country.lower() not in ("india", "in"):  # Middle East defaults
        settings.update({"currency": "AED", "timezone": "Asia/Dubai", "weekly_off": [4, 5], "fuel_rate_per_km": 0.6, "fuel_daily_cap": 80.0})
    t = Tenant(name=company, slug=_slug(company, db), plan=plan, settings=settings)
    db.add(t)
    db.flush()
    owner = User(tenant_id=t.id, email=email.lower(), name=owner_name, password_hash=hash_password(password), role="owner")
    db.add(owner)
    for n, d in (("Casual Leave", 12), ("Sick Leave", 8), ("Earned Leave", 15)):
        db.add(LeaveType(tenant_id=t.id, name=n, days_per_year=d))
    db.flush()
    return t, owner


@router.post("/signup")
def signup(body: SignupIn, db: Session = Depends(get_db)):
    if not config.ALLOW_SIGNUP:
        raise HTTPException(403, f"Sign-up is by invitation. Please contact {config.VENDOR_NAME or config.PRODUCT_NAME} to get a workspace.")
    problem = check_password(body.password, body.email)
    if problem:
        raise HTTPException(422, problem)
    t, owner = bootstrap_tenant(db, body.company, body.name, body.email, body.password, body.country)
    t.setup_completed_at = utcnow()   # self-serve trial: usable at once (provisioned clients go through the mandatory setup wizard)
    out = issue(db, owner, "signup")
    out["workspace"] = t.slug
    return out


@router.post("/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    key = f"{body.workspace}|{body.email.lower()}|{request.client.host if request.client else ''}"
    now = time.time()
    _fails[key] = [t for t in _fails[key] if now - t < 900]
    if len(_fails[key]) >= 8:
        raise HTTPException(429, "Too many attempts. Try again in 15 minutes.")
    t = db.scalar(select(Tenant).where(Tenant.slug == body.workspace.strip().lower()))
    u = db.scalar(select(User).where(User.tenant_id == t.id, User.email == body.email.strip().lower(), User.deleted_at.is_(None))) if t else None
    ip = request.client.host if request.client else ""
    if not t or not u or not verify_password(body.password, u.password_hash):
        _fails[key].append(now)
        if t:
            db.add(LoginAttempt(tenant_id=t.id, email=body.email.strip().lower()[:200], ip=ip, ok=False, reason="wrong password" if u else "unknown user"))
            db.commit()
        raise HTTPException(401, "Workspace, email or password is incorrect.")
    if not u.active or not t.active:
        raise HTTPException(403, "This account is disabled. Contact your administrator.")
    if u.mfa_enabled:
        if not body.otp:
            raise HTTPException(428, "MFA_REQUIRED")
        if not verify_totp(u.mfa_secret or "", body.otp):
            _fails[key].append(now)
            db.add(LoginAttempt(tenant_id=t.id, email=u.email, ip=ip, ok=False, reason="wrong MFA code"))
            db.commit()
            raise HTTPException(401, "That authenticator code is not right.")
    db.add(LoginAttempt(tenant_id=t.id, email=u.email, ip=ip, ok=True, reason=""))
    out = issue(db, u, body.device or request.headers.get("user-agent", ""))
    out["workspace"] = t.slug
    return out


@router.post("/logout")
def logout(ctx: Ctx = Depends(current)):
    ctx.user.session_id = None
    ctx.db.commit()
    return {"ok": True}


@router.get("/public-config")
def public_config():
    """Before sign-in: what the login screen needs to know."""
    return {"product": config.PRODUCT_NAME, "vendor": config.VENDOR_NAME, "signup_enabled": config.ALLOW_SIGNUP, "support_email": config.SUPPORT_EMAIL}


@router.get("/me")
def me(ctx: Ctx = Depends(current)):
    return {"kind": ctx.tenant.kind, "setup_complete": ctx.tenant.kind != "client" or ctx.tenant.setup_completed_at is not None, "impersonated": bool(ctx.impersonated_by),"user": to_dict(ctx.user), "tenant": {"id": ctx.tenant.id, "name": ctx.tenant.name, "slug": ctx.tenant.slug,
            "plan": ctx.tenant.plan, "settings": {**{k: v for k, v in (ctx.tenant.settings or {}).items() if k not in ("erpnext", "secrets", "smtp", "gateways", "intake", "ai", "assignment")}, "stages": svc.stages_of(ctx.tenant)}},
            "face_enrolled": bool(ctx.user.face_ref), "consent_required": consent_needed(ctx),
            "tracking_policy": (ctx.tenant.settings or {}).get("tracking_policy") or DEFAULT_POLICY}


class PwIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=72)


@router.post("/change-password")
def change_password(body: PwIn, ctx: Ctx = Depends(current)):
    if ctx.impersonated_by:
        raise HTTPException(403, "Not available while signed in as the client's administrator.")
    if not verify_password(body.current_password, ctx.user.password_hash):
        raise HTTPException(400, "Current password is incorrect.")
    problem = check_password(body.new_password, ctx.user.email)
    if problem:
        raise HTTPException(422, problem)
    from ..audit import audit
    audit(ctx, "password.change", "user", ctx.user.id)
    ctx.user.session_id = None  # other devices must sign in again
    ctx.user.password_hash = hash_password(body.new_password)
    ctx.db.commit()
    return {"ok": True}


class FaceIn(BaseModel):
    image: str


@router.post("/face-enroll")
def face_enroll(body: FaceIn, ctx: Ctx = Depends(current)):
    raw, ext = decode_data_url(body.image)
    ctx.user.face_ref = save_upload(ctx.tid, raw, ext)
    ctx.db.commit()
    return {"ok": True}


@router.post("/mfa/setup")
def mfa_setup(ctx: Ctx = Depends(current)):
    ctx.user.mfa_secret = new_totp_secret()
    ctx.user.mfa_enabled = False
    ctx.db.commit()
    return {"secret": ctx.user.mfa_secret, "uri": otpauth_uri(ctx.user.mfa_secret, ctx.user.email)}


class CodeIn(BaseModel):
    code: str
    password: str = ""


@router.post("/mfa/enable")
def mfa_enable(body: CodeIn, ctx: Ctx = Depends(current)):
    if not ctx.user.mfa_secret or not verify_totp(ctx.user.mfa_secret, body.code):
        raise HTTPException(400, "That code is not right. Check the time on your phone and try again.")
    ctx.user.mfa_enabled = True
    ctx.db.commit()
    return {"ok": True}


@router.post("/mfa/disable")
def mfa_disable(body: CodeIn, ctx: Ctx = Depends(current)):
    if (ctx.tenant.settings or {}).get("mfa_mode") == "required":
        raise HTTPException(403, "Your administrator requires MFA.")
    if not verify_password(body.password, ctx.user.password_hash) or not verify_totp(ctx.user.mfa_secret or "", body.code):
        raise HTTPException(400, "Password or code is incorrect.")
    ctx.user.mfa_enabled, ctx.user.mfa_secret = False, None
    ctx.db.commit()
    return {"ok": True}


class ForgotIn(BaseModel):
    workspace: str
    email: str


@router.post("/forgot")
def forgot(body: ForgotIn, request: Request, db: Session = Depends(get_db)):
    """Always answers the same way so it can't be used to discover who has an account."""
    t = db.scalar(select(Tenant).where(Tenant.slug == body.workspace.strip().lower()))
    u = db.scalar(select(User).where(User.tenant_id == t.id, User.email == body.email.strip().lower(), User.active.is_(True), User.deleted_at.is_(None))) if t else None
    if u:
        from .. import svc
        base = (config.PUBLIC_URL or str(request.base_url)).rstrip("/")
        token = make_reset_token(u.id, t.id, u.password_hash)
        svc.send_email_t(t, u.email, "Reset your Fieldwise password", f"Hello {u.name},\n\nUse this link within 60 minutes to choose a new password:\n{base}/#/reset?token={token}\n\nIf you didn't ask for this, ignore this email: your password stays as it is.\n")
    return {"ok": True, "message": "If that account exists, a reset link has been emailed."}


class ResetIn(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=72)


@router.post("/reset")
def reset(body: ResetIn, db: Session = Depends(get_db)):
    d = read_reset_token(body.token)
    u = db.get(User, d["u"]) if d else None
    if not d or not u or u.tenant_id != d["t"] or not reset_token_matches(d, u.password_hash):
        raise HTTPException(400, "This reset link is invalid or has expired. Ask for a new one.")
    problem = check_password(body.new_password, u.email)
    if problem:
        raise HTTPException(422, problem)
    u.password_hash, u.session_id = hash_password(body.new_password), None
    from ..models import AuditLog
    db.add(AuditLog(tenant_id=u.tenant_id, user_id=u.id, action="password.reset", entity="user", entity_id=u.id))
    db.commit()
    return {"ok": True}


@router.post("/consent")
def accept_consent(ctx: Ctx = Depends(current)):
    ctx.user.consent_at, ctx.user.consent_version = utcnow(), str((ctx.tenant.settings or {}).get("tracking_policy_version", "1"))
    from ..audit import audit
    audit(ctx, "consent.accept", "user", ctx.user.id, f"tracking notice v{ctx.user.consent_version}")
    ctx.db.commit()
    return {"ok": True}
