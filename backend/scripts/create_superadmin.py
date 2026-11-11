"""Creates the vendor's platform workspace (once) and a super-admin, and prints a one-time link to choose the password.
  docker compose exec app python scripts/create_superadmin.py --email you@yourcompany.com --name "Your Name"
Run it again with another email to add more super-admins. Needs PUBLIC_URL set (the link's base address)."""
import argparse
import os
import secrets
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from sqlalchemy import select  # noqa: E402
from app import config  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.migrate import ensure_schema  # noqa: E402
from app.models import Tenant, User, utcnow  # noqa: E402
from app.security import hash_password, make_reset_token  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--name", required=True)
    a = ap.parse_args()
    ensure_schema()
    with SessionLocal() as db:
        t = db.scalar(select(Tenant).where(Tenant.slug == config.PLATFORM_SLUG))
        if not t:
            t = Tenant(name=f"{config.PRODUCT_NAME} Platform", slug=config.PLATFORM_SLUG, kind="platform", plan="internal", seat_limit=1000, settings={}, setup_completed_at=utcnow())
            db.add(t)
            db.flush()
        u = db.scalar(select(User).where(User.tenant_id == t.id, User.email == a.email.lower()))
        if not u:
            u = User(tenant_id=t.id, email=a.email.lower(), name=a.name, role="superadmin", password_hash=hash_password(secrets.token_urlsafe(24)))
            db.add(u)
            db.flush()
            print("Created super-admin", u.email)
        else:
            print("Super-admin exists; issuing a new link", u.email)
        link = f"{(config.PUBLIC_URL or 'https://YOUR-DOMAIN').rstrip('/')}/#/reset?token={make_reset_token(u.id, t.id, u.password_hash, 1440)}&workspace={config.PLATFORM_SLUG}"
        db.commit()
    print(f"\nChoose the password here (valid 24 hours):\n{link}\n\nThen sign in with workspace code: {config.PLATFORM_SLUG}")


if __name__ == "__main__":
    main()
