"""Lightweight forward-only schema sync: create missing tables, add missing columns.
Fine for a single-instance start; adopt Alembic before running multiple instances."""
from sqlalchemy import inspect, text
from .db import Base, engine


def ensure_schema():
    import os
    if os.getenv("AUTO_MIGRATE", "1") == "0":  # production: schema is managed with `alembic upgrade head`
        return
    Base.metadata.create_all(engine)
    insp = inspect(engine)
    # The setup-wizard column is new: workspaces that already exist are in use, so they count as set up (only on the run that adds the column).
    legacy_setup = "tenants" in insp.get_table_names() and "setup_completed_at" not in {c["name"] for c in insp.get_columns("tenants")}
    with engine.begin() as cx:
        for table in Base.metadata.sorted_tables:
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl = col.type.compile(engine.dialect)
                default = ""
                d = col.default.arg if col.default is not None and col.default.is_scalar else None
                if isinstance(d, bool):
                    default = f" DEFAULT {int(d)}"
                elif isinstance(d, (int, float)):
                    default = f" DEFAULT {d}"
                elif isinstance(d, str):
                    default = " DEFAULT '" + d.replace("'", "''") + "'"
                cx.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {ddl}{default}'))

    if legacy_setup:
        with engine.begin() as cx:
            cx.execute(text("UPDATE tenants SET setup_completed_at = created_at WHERE setup_completed_at IS NULL"))


def backfill():
    """Fills phone_key on older leads / customers so duplicate detection works for them too."""
    from sqlalchemy import select
    from .db import SessionLocal
    from .models import Lead, Customer
    from .svc import phone_key
    db = SessionLocal()
    try:
        for M in (Lead, Customer):
            for r in db.scalars(select(M).where(M.phone != "", (M.phone_key == "") | (M.phone_key.is_(None)))):
                r.phone_key = phone_key(r.phone)
        from sqlalchemy.orm.attributes import flag_modified
        from .models import Tenant
        from .svc import set_secret
        for t in db.scalars(select(Tenant)):  # older installs kept the ERPNext secret in plain text: encrypt it
            e = (t.settings or {}).get("erpnext") or {}
            if e.get("api_secret"):
                set_secret(t, "erp_api_secret", e["api_secret"])
                s = dict(t.settings); e2 = dict(s["erpnext"]); e2.pop("api_secret", None); s["erpnext"] = e2; t.settings = s
                flag_modified(t, "settings")
        db.commit()
    finally:
        db.close()
