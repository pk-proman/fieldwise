import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'fieldwise.db'}")
ENV = os.getenv("ENV", "development")  # set ENV=production on the live server
_DEV_KEY = "dev-only-change-me-in-production-please-32b"
SECRET_KEY = os.getenv("SECRET_KEY", _DEV_KEY)
if ENV == "production" and (SECRET_KEY == _DEV_KEY or len(SECRET_KEY) < 32):
    raise RuntimeError("ENV=production needs SECRET_KEY set to a random value of at least 32 characters (python -c 'import secrets;print(secrets.token_hex(32))').")
TOKEN_TTL_HOURS = int(os.getenv("TOKEN_TTL_HOURS", "24"))
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", BASE_DIR / "uploads"))
FRONTEND_DIR = Path(os.getenv("FRONTEND_DIR", BASE_DIR.parent / "frontend"))
RECYCLE_DAYS = 90
BIOMETRIC_PROVIDER = os.getenv("BIOMETRIC_PROVIDER", "mock")  # mock | rekognition
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "no-reply@fieldwise.app")
SMS_WEBHOOK_URL = os.getenv("SMS_WEBHOOK_URL", "")  # POST {to, message}; empty = log only
ENABLE_SCHEDULER = os.getenv("ENABLE_SCHEDULER", "1") == "1"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "capacitor://localhost,ionic://localhost,https://localhost,http://localhost").split(",") if o.strip()]

PUBLIC_URL = os.getenv("PUBLIC_URL", "")  # https://fieldwise.yourcompany.com : used in emailed links
PING_RETENTION_DAYS = int(os.getenv("PING_RETENTION_DAYS", "180"))
