"""ERPNext connector (REST + token auth).

Domain            Direction            Entities
employees         ERPNext -> App       Employee -> User (role, manager, branch/territory)
crm               bi-directional       Lead/Customer <-> Lead/Customer (create + update; pull by `modified`)
products          ERPNext -> App       Item -> Product
attendance        App -> ERPNext       Employee Checkin (IN/OUT with lat/lng)
expenses          App -> ERPNext       Expense Claim (on approval)

Settings live in tenant.settings["erpnext"] = {enabled, url, api_key, api_secret, company, payable_account, expense_approver, webhook_secret, last_sync:{...}}.
Every call is isolated per tenant and logged to SyncLog.
"""
import logging
import secrets
from datetime import datetime, timedelta
import httpx
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified
from ..db import SessionLocal
from ..models import (Tenant, User, Lead, Customer, Product, Attendance, ExpenseClaim, SyncLog, utcnow)
from ..security import hash_password

log = logging.getLogger("fieldwise.erpnext")
EXPENSE_TYPE_MAP = {"fuel": "Travel", "travel": "Travel", "food": "Food", "lodging": "Travel", "other": "Others"}
LEAD_STATUS_OUT = {"new": "Lead", "contacted": "Open", "qualified": "Interested", "proposal": "Quotation", "negotiation": "Quotation", "won": "Converted", "lost": "Do Not Contact"}
LEAD_STATUS_IN = {"Lead": "new", "Open": "contacted", "Replied": "contacted", "Interested": "qualified", "Opportunity": "qualified",
                  "Quotation": "proposal", "Converted": "won", "Lost Quotation": "lost", "Do Not Contact": "lost"}


class ERPError(Exception):
    pass


class ERPNextClient:
    def __init__(self, cfg: dict, transport=None):
        if not cfg.get("url") or not cfg.get("api_key") or not cfg.get("api_secret"):
            raise ERPError("ERPNext URL, API key and API secret are required.")
        if transport is None:  # real network calls: re-check the address every time (DNS can change)
            from ..netguard import check_url
            try:
                check_url(cfg["url"])
            except ValueError as e:
                raise ERPError(f"ERPNext address not allowed: {e}")
        self.base = cfg["url"].rstrip("/")
        self.http = httpx.Client(base_url=self.base, timeout=20, transport=transport,
                                 headers={"Authorization": f"token {cfg['api_key']}:{cfg['api_secret']}", "Accept": "application/json"})
        self.cfg = cfg

    def _check(self, r: httpx.Response):
        if r.status_code >= 400:
            try:
                msg = r.json().get("exception") or r.json().get("message") or r.text
            except Exception:
                msg = r.text
            raise ERPError(f"ERPNext {r.status_code}: {str(msg)[:300]}")
        return r.json()

    def ping(self):
        return self._check(self.http.get("/api/method/frappe.auth.get_logged_user")).get("message")

    def list(self, doctype, fields, filters=None, limit=500):
        import json
        out, start = [], 0
        while True:
            r = self._check(self.http.get(f"/api/resource/{doctype}", params={
                "fields": json.dumps(fields), "filters": json.dumps(filters or []), "limit_start": start, "limit_page_length": limit}))
            rows = r.get("data", [])
            out += rows
            if len(rows) < limit or len(out) >= 20000:
                return out
            start += limit

    def create(self, doctype, doc):
        return self._check(self.http.post(f"/api/resource/{doctype}", json=doc))["data"]

    def upload_file(self, doctype, docname, filename, content: bytes, private=True):
        r = self.http.post("/api/method/upload_file", data={"doctype": doctype, "docname": docname, "is_private": "1" if private else "0", "filename": filename},
                           files={"file": (filename, content)})
        return self._check(r).get("message", {})

    def get_doc(self, doctype, name):
        return self._check(self.http.get(f"/api/resource/{doctype}/{name}"))["data"]

    def update(self, doctype, name, doc):
        return self._check(self.http.put(f"/api/resource/{doctype}/{name}", json=doc))["data"]


def _cfg(t: Tenant) -> dict:
    """The workspace's ERPNext settings with the API secret decrypted (it is stored encrypted, outside this dict)."""
    from .. import svc
    c = dict((t.settings or {}).get("erpnext") or {})
    c["api_secret"] = svc.get_secret(t, "erp_api_secret") or c.get("api_secret", "")  # fallback: legacy plain-text value
    return c


def _log(db, tid, domain, direction, ok, count, msg=""):
    db.add(SyncLog(tenant_id=tid, domain=domain, direction=direction, ok=ok, count=count, message=msg[:600]))


def _stamp(db, t: Tenant, domain):
    s = dict(t.settings)
    e = dict(s.get("erpnext") or {})
    e.setdefault("last_sync", {})[domain] = utcnow().isoformat()
    s["erpnext"] = e
    t.settings = s
    flag_modified(t, "settings")


# ----------------------------------------- pull -----------------------------------------
def pull_employees(db, t: Tenant, client) -> int:
    rows = client.list("Employee", ["name", "employee_name", "company_email", "personal_email", "user_id", "cell_number", "reports_to",
                                    "branch", "designation", "status", "department"], [["status", "=", "Active"]])
    n, by_erp = 0, {}
    for r in rows:
        email = (r.get("user_id") or r.get("company_email") or r.get("personal_email") or "").lower()
        if not email:
            continue
        u = db.scalar(select(User).where(User.tenant_id == t.id, (User.erp_employee == r["name"]) | (User.email == email)))
        role = "manager" if any(w in (r.get("designation") or "").lower() for w in ("manager", "head", "director", "lead")) else "employee"
        if not u:
            used = db.scalar(select(User.id).where(User.tenant_id == t.id, User.active.is_(True), User.deleted_at.is_(None)).limit(t.seat_limit))
            count = len(db.scalars(select(User.id).where(User.tenant_id == t.id, User.active.is_(True), User.deleted_at.is_(None))).all())
            if count >= t.seat_limit:
                break
            u = User(tenant_id=t.id, email=email, name=r["employee_name"], role=role, password_hash=hash_password(secrets.token_urlsafe(12)))
            db.add(u)
        elif u.role not in ("owner", "admin"):
            u.role = role
        u.name, u.phone, u.erp_employee = r["employee_name"], r.get("cell_number") or u.phone, r["name"]
        u.territory = r.get("branch") or r.get("department") or u.territory
        u.employee_code = r["name"]
        by_erp[r["name"]] = (u, r.get("reports_to"))
        n += 1
    db.flush()
    for name, (u, boss) in by_erp.items():  # second pass so managers exist
        if boss and boss in by_erp and by_erp[boss][0].id != u.id:
            u.manager_id = by_erp[boss][0].id
    return n


def pull_products(db, t: Tenant, client) -> int:
    """Item master -> Product catalogue. Only the item group set in Settings -> ERPNext (default: every group) is pulled; disabled items are kept but flagged."""
    group = (_cfg(t).get("item_group") or "").strip()
    flt = [["item_group", "=", group]] if group else []
    n = 0
    for r in client.list("Item", ["name", "item_code", "item_name", "item_group", "stock_uom", "standard_rate", "disabled", "gst_hsn_code", "description"], flt):
        p = db.scalar(select(Product).where(Product.tenant_id == t.id, Product.erp_name == r["name"]))
        if not p:
            p = Product(tenant_id=t.id, erp_name=r["name"], code=r["item_code"], name=(r.get("item_name") or r["item_code"]).strip())
            db.add(p)
        p.code, p.name, p.group, p.uom = r["item_code"], (r.get("item_name") or r["item_code"]).strip(), r.get("item_group") or "", r.get("stock_uom") or "Nos"
        p.price = r.get("standard_rate") or p.price or 0
        p.hsn = r.get("gst_hsn_code") or p.hsn
        p.description = _plain(r.get("description")) or p.description
        p.disabled = bool(r.get("disabled"))
        p.deleted_at = None
        n += 1
    return n


def _plain(s) -> str:
    import html as _h, re as _re
    s = _re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", s or "")
    s = _h.unescape(_re.sub(r"<[^>]+>", "", s)).replace("\xa0", " ")
    return _re.sub(r"[ \t]+", " ", _re.sub(r"\n\s*\n+", "\n", s)).strip()


def upsert_sales_person(db, t: Tenant, row: dict):
    """One sales-team member (ERPNext Sales Person + their Employee record). Links to the matching Fieldwise login by ERPNext employee id or email."""
    from ..models import SalesPerson
    sp = db.scalar(select(SalesPerson).where(SalesPerson.tenant_id == t.id, SalesPerson.erp_name == row["erp_name"]))
    if not sp:
        sp = SalesPerson(tenant_id=t.id, erp_name=row["erp_name"], name=row.get("employee_name") or row["erp_name"])
        db.add(sp)
    sp.name = (row.get("employee_name") or row["erp_name"]).strip()
    for k in ("employee", "parent_name", "designation", "department", "branch", "email", "phone"):
        if row.get(k) is not None:
            setattr(sp, k, row[k] or "")
    sp.enabled, sp.is_group = bool(row.get("enabled", True)), bool(row.get("is_group"))
    sp.deleted_at = None
    if not sp.user_id:
        conds = []
        if sp.employee:
            conds.append(User.erp_employee == sp.employee)
        if sp.email:
            conds.append(User.email == sp.email.lower())
        if conds:
            from sqlalchemy import or_
            u = db.scalar(select(User).where(User.tenant_id == t.id, User.deleted_at.is_(None), or_(*conds)))
            sp.user_id = u.id if u else None
    return sp


def pull_sales_team(db, t: Tenant, client) -> int:
    """ERPNext Sales Person tree (+ Employee details) -> the Fieldwise sales team that lead owners are chosen from."""
    people = client.list("Sales Person", ["name", "employee", "parent_sales_person", "enabled", "is_group"], [["name", "!=", "Sales Team"]])
    emp_ids = [p["employee"] for p in people if p.get("employee")]
    emps = {}
    if emp_ids:
        for e in client.list("Employee", ["name", "employee_name", "designation", "department", "branch", "user_id", "company_email", "personal_email", "cell_number"], [["name", "in", emp_ids]]):
            emps[e["name"]] = e
    n = 0
    for p in people:
        e = emps.get(p.get("employee") or "", {})
        email = (e.get("user_id") or e.get("company_email") or e.get("personal_email") or "").strip().lower()
        upsert_sales_person(db, t, {"erp_name": p["name"], "employee": p.get("employee") or "", "employee_name": e.get("employee_name") or p["name"],
                                    "parent_name": "" if p.get("parent_sales_person") in (None, "", "Sales Team") else p["parent_sales_person"], "enabled": p.get("enabled", 1),
                                    "is_group": p.get("is_group"), "designation": e.get("designation") or "", "department": e.get("department") or "",
                                    "branch": e.get("branch") or "", "email": email, "phone": e.get("cell_number") or ""})
        n += 1
    return n


def pull_crm(db, t: Tenant, client) -> int:
    since = (_cfg(t).get("last_sync") or {}).get("crm_pull")
    f = [["modified", ">", since.replace("T", " ")[:19]]] if since else []
    n = 0
    for r in client.list("Customer", ["name", "customer_name", "customer_type", "territory", "email_id", "mobile_no", "tax_id", "modified"], f):
        c = db.scalar(select(Customer).where(Customer.tenant_id == t.id, Customer.erp_name == r["name"]))
        if not c:
            c = Customer(tenant_id=t.id, erp_name=r["name"], name=r["customer_name"])
            db.add(c)
        c.name, c.email, c.phone, c.gstin = r["customer_name"], r.get("email_id") or c.email, r.get("mobile_no") or c.phone, r.get("tax_id") or c.gstin
        n += 1
    for r in client.list("Lead", ["name", "lead_name", "company_name", "email_id", "mobile_no", "status", "source", "city", "modified"], f):
        l = db.scalar(select(Lead).where(Lead.tenant_id == t.id, Lead.erp_name == r["name"]))
        if not l:
            l = Lead(tenant_id=t.id, erp_name=r["name"], name=r.get("lead_name") or r["name"])
            db.add(l)
        l.name, l.company, l.email, l.phone = r.get("lead_name") or l.name, r.get("company_name") or "", r.get("email_id") or "", r.get("mobile_no") or ""
        l.status, l.source, l.city = LEAD_STATUS_IN.get(r.get("status"), l.status), r.get("source") or "", r.get("city") or ""
        n += 1
    return n


# ----------------------------------------- push -----------------------------------------
def push_crm(db, t: Tenant, client, only: tuple[str, int] | None = None) -> int:
    n = 0
    leads = db.scalars(select(Lead).where(Lead.tenant_id == t.id, Lead.deleted_at.is_(None), *( [Lead.id == only[1]] if only and only[0] == "leads" else [] if only else [Lead.erp_name == ""])))
    for l in leads:
        doc = {"lead_name": l.name, "company_name": l.company, "email_id": l.email, "mobile_no": l.phone, "status": LEAD_STATUS_OUT.get(l.status, "Lead"),
               "source": l.source or None, "city": l.city}
        doc = {k: v for k, v in doc.items() if v not in (None, "")}
        if l.erp_name:
            client.update("Lead", l.erp_name, doc)
        else:
            l.erp_name = client.create("Lead", doc)["name"]
        n += 1
    custs = db.scalars(select(Customer).where(Customer.tenant_id == t.id, Customer.deleted_at.is_(None), *([Customer.id == only[1]] if only and only[0] == "customers" else [] if only else [Customer.erp_name == ""])))
    for c in custs:
        doc = {"customer_name": c.name, "customer_type": "Company", "email_id": c.email, "mobile_no": c.phone, "tax_id": c.gstin}
        doc = {k: v for k, v in doc.items() if v}
        if c.erp_name:
            client.update("Customer", c.erp_name, doc)
        else:
            c.erp_name = client.create("Customer", doc)["name"]
        n += 1
    return n


def push_quotation(db, t: Tenant, client, q) -> str:
    """Create the Quotation in ERPNext and store the number ERPNext assigns (e.g. SAL-QTN-2026-00012)."""
    from ..models import Lead, Customer, Product
    cfg = _cfg(t)
    if q.lead_id and not q.customer_id:
        party_type, party = "Lead", db.get(Lead, q.lead_id)
    else:
        party_type, party = "Customer", db.get(Customer, q.customer_id)
    if not party:
        raise ERPError("The quotation has no lead or customer.")
    if not party.erp_name:  # make sure the party exists in ERPNext first
        push_crm(db, t, client, ("leads" if party_type == "Lead" else "customers", party.id))
        db.flush()
    if not party.erp_name:
        raise ERPError(f"Could not create the {party_type.lower()} in ERPNext.")
    items = []
    for it in q.items:
        pr = db.get(Product, it["product_id"]) if it.get("product_id") else None
        items.append({"item_code": (pr.erp_name or pr.code) if pr else it["code"], "qty": it["qty"], "rate": it["rate"], "discount_percentage": it.get("discount_pct", 0)})
    doc = {"quotation_to": party_type, "party_name": party.erp_name, "transaction_date": q.created_at.date().isoformat(),
           "valid_till": q.valid_till.isoformat() if q.valid_till else None, "order_type": "Sales", "company": cfg.get("company"), "items": items,
           "terms": ((q.terms or "") + ("\n\nAdditional charges: " + ", ".join(f"{c['name']} {c['amount']:g}" for c in (q.charges or [])) if q.charges else "")) or q.notes or None}
    res = client.create("Quotation", {k: v for k, v in doc.items() if v not in (None, "")})
    q.erp_name, q.erp_error = res["name"], ""
    try:
        q.erp_total, q.erp_status = float(res.get("grand_total") or 0) or None, res.get("status") or "Draft"
    except (TypeError, ValueError):
        pass
    return q.erp_name


def refresh_quotation(db, t: Tenant, client, q) -> None:
    d = client.get_doc("Quotation", q.erp_name)
    q.erp_status, q.erp_total = d.get("status") or q.erp_status, d.get("grand_total") or q.erp_total


def push_sales_order(db, t: Tenant, client, o) -> str:
    from ..models import Customer, Product, Quotation
    cfg = _cfg(t)
    cust = db.get(Customer, o.customer_id)
    if not cust.erp_name:
        push_crm(db, t, client, ("customers", cust.id))
        db.flush()
    if not cust.erp_name:
        raise ERPError("Could not create the customer in ERPNext.")
    qname = db.get(Quotation, o.quotation_id).erp_name if o.quotation_id else ""
    items = []
    for it in o.items:
        pr = db.get(Product, it["product_id"]) if it.get("product_id") else None
        row = {"item_code": (pr.erp_name or pr.code) if pr else it["code"], "qty": it["qty"], "rate": it["rate"], "discount_percentage": it.get("discount_pct", 0),
               "delivery_date": o.delivery_date.isoformat() if o.delivery_date else None}
        if qname:
            row["prevdoc_docname"] = qname
        items.append({k: v for k, v in row.items() if v not in (None, "")})
    doc = {"docstatus": 0, "customer": cust.erp_name, "transaction_date": o.created_at.date().isoformat(), "delivery_date": o.delivery_date.isoformat() if o.delivery_date else None,
           "po_no": o.po_number or None, "company": cfg.get("company"), "order_type": "Sales", "items": items}
    res = client.create("Sales Order", {k: v for k, v in doc.items() if v not in (None, "") or k == "docstatus"})  # stays a Draft; ERPNext users review and submit
    o.erp_name, o.erp_error = res["name"], ""
    attach_po(db, t, client, o)
    return o.erp_name


def attach_po(db, t: Tenant, client, o) -> None:
    """Attach the scanned customer PO to the ERPNext Sales Order draft. Failure keeps the draft and records a retryable error."""
    from .. import config
    if o.po_attached or not o.po_file or not o.erp_name:
        return
    try:
        path = config.UPLOAD_DIR / o.po_file
        ext = path.suffix or ".pdf"
        client.upload_file("Sales Order", o.erp_name, f"Customer-PO-{o.po_number or o.number}{ext}".replace(" ", "_"), path.read_bytes())
        o.po_attached = True
    except Exception as e:  # noqa
        o.erp_error = f"Draft {o.erp_name} created, but attaching the customer PO failed: {str(e)[:300]}"


MODE_MAP = {"cash": "Cash", "cheque": "Cheque", "upi": "Wire Transfer", "bank": "Wire Transfer"}


def push_payment(db, t: Tenant, client, p) -> str:
    """Create a Payment Entry (Receive) allocated to the Sales Invoice."""
    from ..models import Customer, Invoice
    cfg = _cfg(t)
    cust = db.get(Customer, p.customer_id)
    if not cust.erp_name:
        raise ERPError("Customer is not in ERPNext yet.")
    doc = {"payment_type": "Receive", "party_type": "Customer", "party": cust.erp_name, "posting_date": p.day.isoformat(), "company": cfg.get("company"),
           "mode_of_payment": (cfg.get("mode_map") or {}).get(p.mode, MODE_MAP[p.mode]), "paid_amount": p.amount, "received_amount": p.amount,
           "reference_no": p.reference or p.receipt_no, "reference_date": (p.cheque_date or p.day).isoformat(), "remarks": f"Collected by Fieldwise {p.receipt_no}"}
    inv = db.get(Invoice, p.invoice_id) if p.invoice_id else None
    if inv and inv.erp_name:
        doc["references"] = [{"reference_doctype": "Sales Invoice", "reference_name": inv.erp_name, "allocated_amount": p.amount}]
    res = client.create("Payment Entry", {k: v for k, v in doc.items() if v not in (None, "")})
    p.erp_ref, p.erp_error = res["name"], ""
    return p.erp_ref


def pull_invoices(db, t: Tenant, client) -> int:
    from ..models import Customer, Invoice
    from datetime import date
    custs = {c.erp_name: c.id for c in db.scalars(select(Customer).where(Customer.tenant_id == t.id, Customer.erp_name != ""))}
    rows = client.list("Sales Invoice", ["name", "customer", "posting_date", "due_date", "grand_total", "outstanding_amount", "status"], [["docstatus", "=", 1], ["outstanding_amount", ">", 0]])
    seen, n = set(), 0
    for r in rows:
        cid = custs.get(r["customer"])
        if not cid:
            continue
        seen.add(r["name"])
        inv = db.scalar(select(Invoice).where(Invoice.tenant_id == t.id, Invoice.erp_name == r["name"]))
        if not inv:
            inv = Invoice(tenant_id=t.id, erp_name=r["name"], number=r["name"], customer_id=cid, invoice_date=date.today(), due_date=date.today())
            db.add(inv)
        inv.invoice_date = date.fromisoformat(r["posting_date"]); inv.due_date = date.fromisoformat(r.get("due_date") or r["posting_date"])
        inv.total, inv.outstanding = r["grand_total"], r["outstanding_amount"]
        inv.status = "open" if inv.outstanding >= inv.total else "partly_paid"
        n += 1
    for inv in db.scalars(select(Invoice).where(Invoice.tenant_id == t.id, Invoice.erp_name != "", Invoice.status != "paid")):
        if inv.erp_name not in seen:  # no longer outstanding in ERPNext (paid or cancelled)
            inv.outstanding, inv.status = 0, "paid"
    return n


def push_sales_invoice(db, t: Tenant, client, inv, order) -> str:
    """Create a draft Sales Invoice in ERPNext against the Sales Order; the ERPNext number is stored on the invoice."""
    from ..models import Customer, Product
    cfg = _cfg(t)
    cust = db.get(Customer, inv.customer_id)
    if not cust.erp_name:
        push_crm(db, t, client, ("customers", cust.id))
        db.flush()
    if not cust.erp_name:
        raise ERPError("Could not create the customer in ERPNext.")
    items = []
    for it in order.items:
        pr = db.get(Product, it["product_id"]) if it.get("product_id") else None
        row = {"item_code": (pr.erp_name or pr.code) if pr else it["code"], "qty": it["qty"], "rate": it["rate"], "discount_percentage": it.get("discount_pct", 0)}
        if order.erp_name:
            row["sales_order"] = order.erp_name
        items.append(row)
    doc = {"docstatus": 0, "customer": cust.erp_name, "posting_date": inv.invoice_date.isoformat(), "due_date": inv.due_date.isoformat(), "company": cfg.get("company"), "po_no": order.po_number or None, "items": items}
    res = client.create("Sales Invoice", {k: v for k, v in doc.items() if v not in (None, "") or k == "docstatus"})
    inv.erp_name = res["name"]
    return inv.erp_name


def push_attendance(db, t: Tenant, client) -> int:
    n = 0
    rows = db.execute(select(Attendance, User.erp_employee).join(User, User.id == Attendance.user_id).where(
        Attendance.tenant_id == t.id, Attendance.erp_synced.is_(False), Attendance.check_in_at.is_not(None),
        Attendance.check_out_at.is_not(None))).all()
    for a, emp in rows:
        if not emp:
            continue
        fmt = lambda d: d.strftime("%Y-%m-%d %H:%M:%S")
        for log_type, when, la, lo in (("IN", a.check_in_at, a.check_in_lat, a.check_in_lng), ("OUT", a.check_out_at, a.check_out_lat, a.check_out_lng)):
            doc = {"employee": emp, "log_type": log_type, "time": fmt(when), "latitude": la, "longitude": lo, "device_id": "fieldwise"}
            client.create("Employee Checkin", doc)
        a.erp_synced = True
        n += 1
    return n


def push_expenses(db, t: Tenant, client, ids=None) -> int:
    cfg, n = _cfg(t), 0
    q = select(ExpenseClaim, User.erp_employee, User.name).join(User, User.id == ExpenseClaim.user_id).where(
        ExpenseClaim.tenant_id == t.id, ExpenseClaim.status.in_(["approved", "paid"]), ExpenseClaim.erp_ref == "")
    if ids is not None:
        q = q.where(ExpenseClaim.id.in_(ids or [0]))
    for e, emp, _ in db.execute(q).all():
        if not emp:
            continue
        doc = {"employee": emp, "posting_date": e.day.isoformat(), "company": cfg.get("company"), "expense_approver": cfg.get("expense_approver"),
               "payable_account": cfg.get("payable_account"),
               "expenses": [{"expense_date": e.day.isoformat(), "expense_type": EXPENSE_TYPE_MAP.get(e.category, "Others"),
                             "description": e.description or e.category, "amount": e.amount, "sanctioned_amount": e.amount}]}
        doc = {k: v for k, v in doc.items() if v}
        e.erp_ref = client.create("Expense Claim", doc)["name"]
        n += 1
    return n


# ----------------------------------------- orchestration -----------------------------------------
DOMAINS = {"invoices": ("in", pull_invoices), "employees": ("in", pull_employees), "products": ("in", pull_products), "sales_team": ("in", pull_sales_team), "crm_pull": ("in", pull_crm),
           "crm_push": ("out", push_crm), "attendance": ("out", push_attendance), "expenses": ("out", push_expenses)}


def run_sync(tenant_id: int, domain: str, transport=None) -> dict:
    db = SessionLocal()
    try:
        t = db.get(Tenant, tenant_id)
        cfg = _cfg(t)
        if not cfg.get("enabled"):
            raise ERPError("ERPNext integration is turned off.")
        client = ERPNextClient(cfg, transport)
        if domain == "crm":
            res = {d: run_inner(db, t, client, d) for d in ("crm_pull", "crm_push")}
            db.commit()
            return res
        res = {domain: run_inner(db, t, client, domain)}
        db.commit()
        return res
    except Exception as e:
        db.rollback()
        _log(db, tenant_id, domain, "-", False, 0, str(e))
        db.commit()
        raise
    finally:
        db.close()


def run_inner(db, t, client, domain):
    if domain not in DOMAINS:
        raise ERPError(f"Unknown sync domain '{domain}'.")
    direction, fn = DOMAINS[domain]
    n = fn(db, t, client)
    _log(db, t.id, domain, direction, True, n)
    _stamp(db, t, domain)
    return n


def push_entity_task(tenant_id: int, entity: str, rid: int):
    """Background: real-time push of a single lead/customer after create/update."""
    db = SessionLocal()
    try:
        t = db.get(Tenant, tenant_id)
        client = ERPNextClient(_cfg(t))
        n = push_crm(db, t, client, (entity, rid))
        _log(db, tenant_id, "crm_push", "out", True, n)
        db.commit()
    except Exception as e:  # never break the user's request for a sync failure
        db.rollback()
        _log(db, tenant_id, "crm_push", "out", False, 0, str(e))
        db.commit()
    finally:
        db.close()


def push_expenses_task(tenant_id: int, ids: list[int]):
    db = SessionLocal()
    try:
        t = db.get(Tenant, tenant_id)
        n = push_expenses(db, t, ERPNextClient(_cfg(t)), ids)
        _log(db, tenant_id, "expenses", "out", True, n)
        db.commit()
    except Exception as e:
        db.rollback()
        _log(db, tenant_id, "expenses", "out", False, 0, str(e))
        db.commit()
    finally:
        db.close()
