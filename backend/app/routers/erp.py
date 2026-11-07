import hmac
import secrets
from fastapi import APIRouter, Depends, HTTPException, Header, BackgroundTasks, Request
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified
from ..deps import Ctx, require
from ..db import get_db, SessionLocal
from ..models import Tenant, SyncLog
from ..common import to_dict
from ..integrations import erpnext as erp

router = APIRouter(prefix="/erp", tags=["erpnext"])


def _public(cfg: dict) -> dict:
    out = {k: v for k, v in cfg.items() if k not in ("api_secret", "webhook_secret")}
    out["api_secret_set"] = bool(cfg.get("api_secret"))  # (cfg here is the decrypted view; the secret itself is never returned)
    out["webhook_secret"] = cfg.get("webhook_secret", "")
    return out


@router.get("/config")
def get_cfg(ctx: Ctx = Depends(require("admin"))):
    return _public(erp._cfg(ctx.tenant))


@router.put("/config")
def put_cfg(body: dict, ctx: Ctx = Depends(require("admin"))):
    s = dict(ctx.tenant.settings or {})
    cur = dict(s.get("erpnext") or {})
    for k in ("enabled", "url", "api_key", "company", "payable_account", "expense_approver"):
        if k in body:
            cur[k] = body[k]
    cur.pop("api_secret", None)  # never keep the secret in the settings JSON
    if body.get("api_secret"):
        from .. import svc
        ctx.tenant.settings = s
        svc.set_secret(ctx.tenant, "erp_api_secret", body["api_secret"])  # encrypted at rest; blank = keep the existing one
        s = dict(ctx.tenant.settings)
    if cur.get("url"):
        from ..netguard import check_url
        try:
            check_url(str(cur["url"]), allow_http=False)
        except ValueError as e:
            raise HTTPException(422, f"ERPNext address: {e}")
    from ..audit import audit
    audit(ctx, "integration.erp_config", "tenant", ctx.tid, "ERPNext connection changed")
    cur.setdefault("webhook_secret", secrets.token_urlsafe(24))
    s["erpnext"] = cur
    ctx.tenant.settings = s
    flag_modified(ctx.tenant, "settings")
    ctx.db.commit()
    return _public(cur)


@router.post("/test")
def test(ctx: Ctx = Depends(require("admin"))):
    try:
        who = erp.ERPNextClient(erp._cfg(ctx.tenant)).ping()
        return {"ok": True, "user": who}
    except Exception as e:
        raise HTTPException(502, f"Could not reach ERPNext: {e}")


@router.post("/sync/{domain}")
def sync(domain: str, ctx: Ctx = Depends(require("admin"))):
    if domain not in erp.DOMAINS and domain != "crm":
        raise HTTPException(404, "Unknown sync type.")
    try:
        return {"ok": True, "result": erp.run_sync(ctx.tid, domain)}
    except erp.ERPError as e:
        raise HTTPException(502, str(e))
    except Exception as e:
        raise HTTPException(502, f"Sync failed: {e}")


@router.get("/logs")
def logs(ctx: Ctx = Depends(require("admin"))):
    return [to_dict(l) for l in ctx.db.scalars(select(SyncLog).where(SyncLog.tenant_id == ctx.tid).order_by(SyncLog.id.desc()).limit(100))]


def _safe_sync(tid: int, domain: str):
    try:
        erp.run_sync(tid, domain)
    except Exception:  # already recorded in SyncLog
        pass


@router.post("/webhook/{slug}")
async def webhook(slug: str, request: Request, bg: BackgroundTasks, x_webhook_secret: str = Header(default="")):
    """ERPNext Webhook (doc_events on Employee). Header X-Webhook-Secret must match; triggers an employee re-sync."""
    db = SessionLocal()
    try:
        t = db.scalar(select(Tenant).where(Tenant.slug == slug))
        secret = ((t.settings or {}).get("erpnext") or {}).get("webhook_secret", "") if t else ""
        if not t or not secret or not hmac.compare_digest(secret, x_webhook_secret):
            raise HTTPException(401, "Invalid webhook secret.")
        tid = t.id
    finally:
        db.close()
    bg.add_task(_safe_sync, tid, "employees")
    return {"queued": True}
