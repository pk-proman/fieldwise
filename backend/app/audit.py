from .models import AuditLog


def audit(ctx, action: str, entity: str = "", entity_id: int | None = None, detail: str = ""):
    """Records a security-relevant action. Call inside the request's transaction."""
    ctx.db.add(AuditLog(tenant_id=ctx.tid, user_id=ctx.user.id, action=action[:60], entity=entity[:40], entity_id=entity_id, detail=detail[:500]))
