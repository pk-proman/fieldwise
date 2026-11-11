"""Prints every data entity (table) and its attributes as Markdown, straight from the models, so the product spec can never drift from the code.
   PYTHONPATH=. python scripts/dump_entities.py > ../docs/ENTITY_REFERENCE.md"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.db import Base  # noqa: E402
from app import models  # noqa: E402,F401

GROUPS = {
    "Platform and tenancy": ["tenants", "users", "audit_logs", "login_attempts", "job_locks", "sync_logs"],
    "Organisation structure": ["branches", "departments", "designations", "territories", "shifts", "shift_roster", "shift_swaps", "holidays"],
    "Field force": ["attendance", "location_pings", "device_health", "geo_events", "visits", "beats", "beat_visits", "tasks", "meetings", "announcements"],
    "People operations": ["leave_types", "leave_requests", "expense_claims", "incentive_plans", "targets"],
    "CRM": ["leads", "sales_team", "customers", "contacts", "activities", "followups", "comments", "events", "renewals", "campaigns", "campaign_contacts", "call_logs", "intake_logs"],
    "Catalogue and sales documents": ["products", "quotations", "sales_orders", "invoices", "payments"],
    "Purchase and support": ["vendors", "purchase_orders", "dealers", "tickets", "ticket_comments"],
    "Configuration and masters": ["masters", "custom_fields", "form_templates", "form_responses", "message_templates", "saved_views", "report_schedules", "notifications"],
}
seen = set()
print("# Entity reference (generated)\n\nEvery table the product stores, with its attributes. Every row carries `id`, `tenant_id`, `created_at` and `deleted_at` (soft delete) unless noted.\n")
for g, names in GROUPS.items():
    print(f"## {g}\n")
    for n in names:
        t = Base.metadata.tables.get(n)
        if t is None:
            continue
        seen.add(n)
        print(f"### `{n}`\n\n| Attribute | Type | Notes |\n|---|---|---|")
        for c in t.columns:
            if c.name in ("id", "tenant_id", "created_at", "deleted_at") and n != "tenants":
                continue
            note = []
            if c.primary_key: note.append("key")
            if c.foreign_keys: note.append("-> " + list(c.foreign_keys)[0].target_fullname.split(".")[0])
            if not c.nullable and not c.primary_key: note.append("required")
            if c.unique: note.append("unique")
            print(f"| `{c.name}` | {str(c.type).lower().split('(')[0]} | {', '.join(note)} |")
        print()
rest = [n for n in Base.metadata.tables if n not in seen]
if rest:
    print("## Other\n")
    for n in rest:
        print(f"- `{n}`: " + ", ".join(c.name for c in Base.metadata.tables[n].columns))
