"""Notifications, email (MOM) and SMS (visit OTP). Falls back to logging when no provider is configured."""
import logging
import smtplib
from email.message import EmailMessage
import httpx
from sqlalchemy import select
from . import config
from .models import Notification

log = logging.getLogger("fieldwise.notify")


def notify(db, tenant_id, user_id, title, body="", kind="info", dedupe_key=""):
    if dedupe_key:
        exists = db.scalar(select(Notification.id).where(Notification.tenant_id == tenant_id, Notification.user_id == user_id,
                                                         Notification.dedupe_key == dedupe_key))
        if exists:
            return None
    n = Notification(tenant_id=tenant_id, user_id=user_id, title=title, body=body, kind=kind, dedupe_key=dedupe_key)
    db.add(n)
    return n


def send_email(to: str, subject: str, body: str) -> bool:
    if not config.SMTP_HOST or not to:
        log.info("EMAIL (not sent, SMTP unset) to=%s subject=%s", to, subject)
        return False
    try:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = config.SMTP_FROM, to, subject
        msg.set_content(body)
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as s:
            s.starttls()
            if config.SMTP_USER:
                s.login(config.SMTP_USER, config.SMTP_PASSWORD)
            s.send_message(msg)
        return True
    except Exception as e:  # noqa
        log.warning("email failed: %s", e)
        return False


def send_sms(to: str, message: str) -> bool:
    if not config.SMS_WEBHOOK_URL or not to:
        log.info("SMS (not sent, SMS_WEBHOOK_URL unset) to=%s msg=%s", to, message)
        return False
    try:
        return httpx.post(config.SMS_WEBHOOK_URL, json={"to": to, "message": message}, timeout=10).is_success
    except Exception as e:  # noqa
        log.warning("sms failed: %s", e)
        return False
