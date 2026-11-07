import base64
import csv
import io
import re
import uuid
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from sqlalchemy import select
from . import config
from .models import Activity, utcnow

SECRET_COLS = {"password_hash", "session_id", "otp_hash", "face_ref", "mfa_secret"}


def to_dict(obj, exclude=()):
    out = {}
    for c in obj.__table__.columns:
        if c.name in SECRET_COLS or c.name in exclude:
            continue
        v = getattr(obj, c.name)
        if isinstance(v, (datetime, date)):
            v = v.isoformat() + ("Z" if isinstance(v, datetime) else "")
        out[c.name] = v
    return out


def tz_of(tenant) -> ZoneInfo:
    try:
        return ZoneInfo((tenant.settings or {}).get("timezone", "Asia/Kolkata"))
    except Exception:
        return ZoneInfo("Asia/Kolkata")


def local_now(tenant) -> datetime:
    return datetime.now(tz_of(tenant))


def local_today(tenant) -> date:
    return local_now(tenant).date()


def local_day_bounds_utc(tenant, day: date):
    tz = tz_of(tenant)
    s = datetime(day.year, day.month, day.day, tzinfo=tz)
    e = s + timedelta(days=1)
    to_utc = lambda d: d.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    return to_utc(s), to_utc(e)


def parse_day(s: str | None, default: date) -> date:
    if not s:
        return default
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise HTTPException(422, "Dates must be YYYY-MM-DD.")


def log_activity(db, tenant_id, entity, entity_id, user_id, text):
    db.add(Activity(tenant_id=tenant_id, entity=entity, entity_id=entity_id, user_id=user_id, text=text))


def get_or_404(db, model, id_, tenant_id):
    obj = db.get(model, id_)
    if not obj or obj.tenant_id != tenant_id or getattr(obj, "deleted_at", None):
        raise HTTPException(404, f"{model.__name__} not found.")
    return obj


def csv_response_text(rows: list[dict]) -> str:
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if v is None else v) for k, v in r.items()})
    return buf.getvalue()


def save_upload(tenant_id: int, data: bytes, ext: str = "jpg", watermark: str | None = None) -> str:
    """Stores a file under uploads/<tenant>/. Images get an optional timestamp+geotag watermark."""
    ext = re.sub(r"[^a-z0-9]", "", ext.lower())[:5]
    if ext not in ("jpg", "jpeg", "png", "webp", "gif", "pdf"):
        raise HTTPException(422, "Only JPG, PNG, WebP, GIF or PDF files can be uploaded.")
    if watermark and ext in ("jpg", "jpeg", "png", "webp"):
        try:
            from PIL import Image, ImageDraw
            img = Image.open(io.BytesIO(data)).convert("RGB")
            img.thumbnail((1600, 1600))
            d = ImageDraw.Draw(img)
            w, h = img.size
            bar = max(22, h // 22)
            d.rectangle([0, h - bar, w, h], fill=(0, 0, 0))
            d.text((8, h - bar + bar // 4), watermark, fill=(255, 255, 255))
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=82)
            data, ext = buf.getvalue(), "jpg"
        except Exception:
            pass
    folder = config.UPLOAD_DIR / str(tenant_id)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.{ext}"
    (folder / name).write_bytes(data)
    return f"{tenant_id}/{name}"


def decode_data_url(s: str) -> tuple[bytes, str]:
    m = re.match(r"data:(image/(?:jpeg|jpg|png|webp|gif));base64,(.*)", s, re.S)
    if not m:
        raise HTTPException(422, "The image must be a JPG, PNG, WebP or GIF.")
    raw = base64.b64decode(m.group(2))
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(413, "Image is larger than 8 MB.")
    return raw, m.group(1).split("/")[1].replace("jpeg", "jpg")


def watermark_text(user_name: str, lat, lng, tenant) -> str:
    ts = local_now(tenant).strftime("%d %b %Y %H:%M:%S")
    loc = f"{lat:.5f},{lng:.5f}" if lat is not None and lng is not None else "no-gps"
    return f"{ts} | {loc} | {user_name}"


def decode_doc_url(s: str, max_mb: int = 10) -> tuple[bytes, str]:
    """Scanned documents: images or PDF as a base64 data URL."""
    m = re.match(r"data:(image/(?:jpeg|png|webp)|application/pdf);base64,(.*)", s or "", re.S)
    if not m:
        raise HTTPException(422, "Attach the scan as a JPG, PNG or PDF.")
    raw = base64.b64decode(m.group(2))
    if len(raw) > max_mb * 1024 * 1024:
        raise HTTPException(413, f"The file is larger than {max_mb} MB.")
    kind = m.group(1)
    if kind == "application/pdf" and not raw.startswith(b"%PDF"):
        raise HTTPException(422, "That doesn't look like a valid PDF.")
    return raw, "pdf" if kind == "application/pdf" else kind.split("/")[1].replace("jpeg", "jpg")
