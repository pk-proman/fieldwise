"""Guards against cross-workspace references: any id a client sends must belong to the caller's own workspace."""
from fastapi import HTTPException


def own(ctx, model, id_, label: str | None = None):
    try:
        obj = ctx.db.get(model, int(id_))
    except (TypeError, ValueError):
        raise HTTPException(422, f"{label or model.__name__} is not valid.")
    if not obj or obj.tenant_id != ctx.tid or getattr(obj, "deleted_at", None):
        raise HTTPException(422, f"{label or model.__name__} was not found in your workspace.")
    return obj


def own_all(ctx, model, ids, label: str | None = None):
    for i in ids or []:
        own(ctx, model, i, label)
