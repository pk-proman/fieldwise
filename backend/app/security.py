import secrets
from datetime import datetime, timedelta, timezone
import bcrypt
import jwt
from .config import SECRET_KEY, TOKEN_TTL_HOURS


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode()[:72], bcrypt.gensalt()).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode()[:72], hashed.encode())
    except ValueError:
        return False


def new_session_id() -> str:
    return secrets.token_hex(16)


def make_token(user_id: int, tenant_id: int, session_id: str, ttl_hours: float | None = None, impersonated_by: int | None = None, device_id: int | None = None) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=ttl_hours or TOKEN_TTL_HOURS)
    claims = {"sub": str(user_id), "tid": tenant_id, "sid": session_id, "exp": exp}
    if impersonated_by:
        claims["imp"] = impersonated_by
    if device_id:
        claims["did"] = device_id   # activated phone: valid while that device row is active (no daily sign-in)
    return jwt.encode(claims, SECRET_KEY, algorithm="HS256")


def decode_token(token: str) -> dict:
    return jwt.decode(token, SECRET_KEY, algorithms=["HS256"])


def make_otp() -> str:
    return f"{secrets.randbelow(10**6):06d}"


# ---- TOTP (RFC 6238) for optional multi-factor sign-in; compatible with Google/Microsoft Authenticator
import base64 as _b64
import hashlib as _h
import hmac as _hmac
import struct as _st
import time as _t


def new_totp_secret() -> str:
    return _b64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _hotp(secret: str, counter: int) -> str:
    key = _b64.b32decode(secret + "=" * (-len(secret) % 8))
    mac = _hmac.new(key, _st.pack(">Q", counter), _h.sha1).digest()
    o = mac[-1] & 0x0F
    return f"{(_st.unpack('>I', mac[o:o + 4])[0] & 0x7FFFFFFF) % 10**6:06d}"


def verify_totp(secret: str, code: str, window: int = 1) -> bool:
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != 6:
        return False
    now = int(_t.time() // 30)
    return any(_hmac.compare_digest(_hotp(secret, now + d), code) for d in range(-window, window + 1))


def otpauth_uri(secret: str, email: str, issuer: str = "Fieldwise") -> str:
    from urllib.parse import quote
    return f"otpauth://totp/{quote(issuer)}:{quote(email)}?secret={secret}&issuer={quote(issuer)}&digits=6&period=30"


_COMMON = {"password", "password1", "password123", "12345678", "123456789", "1234567890", "qwerty123", "qwertyuiop", "iloveyou", "admin123", "welcome1", "letmein123", "abc12345", "fieldwise", "fieldwise1", "changeme", "changeme1"}


def check_password(pw: str, email: str = "") -> str | None:
    """Returns a plain-language problem, or None if the password is acceptable."""
    if len(pw) < 8:
        return "Use at least 8 characters."
    if len(pw.encode()) > 72:
        return "Use at most 72 characters."
    if not any(c.isalpha() for c in pw) or not any(c.isdigit() for c in pw):
        return "Mix letters and numbers."
    if pw.lower() in _COMMON or (email and pw.lower() == email.split("@")[0].lower()):
        return "That password is too easy to guess."
    return None


# ---- signed one-time password-reset tokens
import json as _json
import hmac as _hm2
import hashlib as _hl2
import base64 as _b2
from . import config as _cfg


def make_reset_token(user_id: int, tenant_id: int, password_hash: str, ttl_min: int = 60) -> str:
    body = _b2.urlsafe_b64encode(_json.dumps({"u": user_id, "t": tenant_id, "e": int(_t.time()) + ttl_min * 60, "p": _hl2.sha256(password_hash.encode()).hexdigest()[:12]}).encode()).decode().rstrip("=")
    sig = _hm2.new(_cfg.SECRET_KEY.encode(), ("reset|" + body).encode(), _hl2.sha256).hexdigest()[:32]
    return f"{body}.{sig}"


def read_reset_token(token: str) -> dict | None:
    try:
        body, sig = token.split(".", 1)
        if not _hm2.compare_digest(sig, _hm2.new(_cfg.SECRET_KEY.encode(), ("reset|" + body).encode(), _hl2.sha256).hexdigest()[:32]):
            return None
        d = _json.loads(_b2.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        return d if d["e"] >= _t.time() else None
    except Exception:
        return None


def reset_token_matches(d: dict, password_hash: str) -> bool:
    return _hm2.compare_digest(d["p"], _hl2.sha256(password_hash.encode()).hexdigest()[:12])  # single use: the hash changes after a reset


def hash_secret(value: str) -> str:
    """Keyed hash for device secrets and PINs (high-entropy or attempt-limited, so a fast hash is fine)."""
    import hashlib, hmac
    return hmac.new(SECRET_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()
