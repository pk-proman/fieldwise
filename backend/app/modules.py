"""The product's switchable modules. A client's administrator turns them on or off in the setup wizard (and later in Settings);
the navigation shows only enabled modules. All on by default."""
MODULES = [
    ("field_force", "Field force", "Attendance, live GPS tracking, customer visits, beat plans, tasks, meetings, work hours"),
    ("crm", "CRM", "Leads, follow-ups, customers, contacts, notes, renewals, trade fairs, product catalogue"),
    ("sales_docs", "Quotes, orders and invoices", "Quotations, sales orders, invoices, payment collections"),
    ("people", "People operations", "Leave, expenses, incentives, shift roster"),
    ("purchase_support", "Purchase and support", "Vendors, purchase orders, dealers, helpdesk tickets"),
    ("calls", "Calls and dialer", "Call logs, auto-dialer campaigns"),
    ("erp", "ERP integration", "Two-way sync with ERPNext (customers, products, sales team, invoices)"),
    ("ai", "AI assistants", "AI lead qualifier and other AI helpers"),
]
KEYS = [m[0] for m in MODULES]


def defaults() -> dict:
    return {k: True for k in KEYS}


def enabled(tenant, key: str) -> bool:
    return ((tenant.settings or {}).get("modules") or {}).get(key, True) is not False
