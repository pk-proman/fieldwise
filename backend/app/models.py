"""Multi-tenant data model. Every business table carries tenant_id; all queries are tenant-scoped."""
from datetime import datetime, date, timezone
from sqlalchemy import (String, Integer, Float, Boolean, DateTime, Date, Text, JSON, ForeignKey, Index)
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TenantMixin:
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)  # recycle bin


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(60), unique=True)
    plan: Mapped[str] = mapped_column(String(30), default="trial")
    seat_limit: Mapped[int] = mapped_column(Integer, default=10)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # {fuel_rate_per_km, fuel_daily_cap, currency, timezone, work_start, work_end, require_face, require_visit_otp,
    #  geofence_default_m, labels:{check_in,check_out}, map_style, erpnext:{url,api_key,api_secret,enabled,...}}
    settings: Mapped[dict] = mapped_column(JSON, default=dict)


class User(TenantMixin, Base):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(200), index=True)
    name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str] = mapped_column(String(30), default="")
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20), default="employee")  # owner|admin|manager|employee
    manager_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    branch_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("branches.id"), nullable=True)
    territory: Mapped[str] = mapped_column(String(100), default="")
    employee_code: Mapped[str] = mapped_column(String(50), default="")
    erp_employee: Mapped[str] = mapped_column(String(100), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # single-device lock
    device_label: Mapped[str] = mapped_column(String(200), default="")
    face_ref: Mapped[str | None] = mapped_column(String(300), nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    department_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    designation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mfa_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    shift_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    consent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # accepted the location-tracking notice
    consent_version: Mapped[str] = mapped_column(String(20), default="")
    __table_args__ = (Index("ux_user_tenant_email", "tenant_id", "email", unique=True),)


class Branch(TenantMixin, Base):
    __tablename__ = "branches"
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str] = mapped_column(String(300), default="")
    country: Mapped[str] = mapped_column(String(50), default="India")
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    radius_m: Mapped[int] = mapped_column(Integer, default=150)


class Attendance(TenantMixin, Base):
    __tablename__ = "attendance"
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    check_in_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    check_in_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    check_in_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    check_in_branch_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    check_in_dist_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    check_in_method: Mapped[str] = mapped_column(String(30), default="")
    check_in_selfie: Mapped[str | None] = mapped_column(String(300), nullable=True)
    check_out_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    check_out_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    check_out_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_distance_km: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(20), default="present")  # present|late|half_day|on_leave
    flagged: Mapped[str] = mapped_column(String(200), default="")  # mock location, outside geofence, etc.
    erp_synced: Mapped[bool] = mapped_column(Boolean, default=False)


class LocationPing(TenantMixin, Base):
    __tablename__ = "location_pings"
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    battery: Mapped[int | None] = mapped_column(Integer, nullable=True)
    network: Mapped[str] = mapped_column(String(20), default="online")
    gps_on: Mapped[bool] = mapped_column(Boolean, default=True)
    mock: Mapped[bool] = mapped_column(Boolean, default=False)


class LeaveType(TenantMixin, Base):
    __tablename__ = "leave_types"
    name: Mapped[str] = mapped_column(String(100))
    days_per_year: Mapped[float] = mapped_column(Float, default=12)
    paid: Mapped[bool] = mapped_column(Boolean, default=True)


class Holiday(TenantMixin, Base):
    __tablename__ = "holidays"
    day: Mapped[date] = mapped_column(Date)
    name: Mapped[str] = mapped_column(String(200))
    country: Mapped[str] = mapped_column(String(50), default="All")


class LeaveRequest(TenantMixin, Base):
    __tablename__ = "leave_requests"
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    leave_type_id: Mapped[int] = mapped_column(Integer, ForeignKey("leave_types.id"))
    from_day: Mapped[date] = mapped_column(Date)
    to_day: Mapped[date] = mapped_column(Date)
    days: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|approved|rejected|cancelled
    approver_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decision_note: Mapped[str] = mapped_column(String(300), default="")


class ExpenseClaim(TenantMixin, Base):
    __tablename__ = "expense_claims"
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    day: Mapped[date] = mapped_column(Date)
    category: Mapped[str] = mapped_column(String(50))  # fuel|travel|food|lodging|other
    amount: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(5), default="INR")
    description: Mapped[str] = mapped_column(String(500), default="")
    receipt: Mapped[str | None] = mapped_column(String(300), nullable=True)
    distance_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|approved|rejected|paid
    approver_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decision_note: Mapped[str] = mapped_column(String(300), default="")
    erp_ref: Mapped[str] = mapped_column(String(100), default="")
    level: Mapped[int] = mapped_column(Integer, default=0)  # approvals completed
    levels_required: Mapped[int] = mapped_column(Integer, default=1)
    approvals: Mapped[list] = mapped_column(JSON, default=list)  # [{by, name, at, note}]


class Customer(TenantMixin, Base):
    __tablename__ = "customers"
    name: Mapped[str] = mapped_column(String(200), index=True)
    industry: Mapped[str] = mapped_column(String(100), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    address: Mapped[str] = mapped_column(String(400), default="")
    city: Mapped[str] = mapped_column(String(100), default="")
    country: Mapped[str] = mapped_column(String(50), default="India")
    gstin: Mapped[str] = mapped_column(String(30), default="")
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    owner_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    custom: Mapped[dict] = mapped_column(JSON, default=dict)
    erp_name: Mapped[str] = mapped_column(String(140), default="")
    credit_limit: Mapped[float] = mapped_column(Float, default=0)  # 0 = no limit
    credit_days: Mapped[int] = mapped_column(Integer, default=30)
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)  # parent company
    business_type: Mapped[str] = mapped_column(String(60), default="")
    phone_key: Mapped[str] = mapped_column(String(15), default="", index=True)


class Contact(TenantMixin, Base):
    __tablename__ = "contacts"
    name: Mapped[str] = mapped_column(String(200), index=True)
    designation: Mapped[str] = mapped_column(String(100), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    customer_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("customers.id"), nullable=True)
    owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    custom: Mapped[dict] = mapped_column(JSON, default=dict)


class Lead(TenantMixin, Base):
    __tablename__ = "leads"
    name: Mapped[str] = mapped_column(String(200), index=True)
    company: Mapped[str] = mapped_column(String(200), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    source: Mapped[str] = mapped_column(String(60), default="")
    status: Mapped[str] = mapped_column(String(30), default="new")  # new|contacted|qualified|proposal|won|lost
    value: Mapped[float] = mapped_column(Float, default=0)
    owner_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    city: Mapped[str] = mapped_column(String(100), default="")
    country: Mapped[str] = mapped_column(String(50), default="India")
    notes: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    custom: Mapped[dict] = mapped_column(JSON, default=dict)
    shared_with: Mapped[list] = mapped_column(JSON, default=list)  # user ids
    converted_customer_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    erp_name: Mapped[str] = mapped_column(String(140), default="")
    product_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("products.id"), nullable=True, index=True)  # mandatory via API
    qualification: Mapped[dict] = mapped_column(JSON, default=dict)  # BANT scores + notes
    qualified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    lost_reason: Mapped[str] = mapped_column(String(200), default="")
    expected_close: Mapped[date | None] = mapped_column(Date, nullable=True)
    stage_changed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    temperature: Mapped[str] = mapped_column(String(10), default="warm")  # hot|warm|cold
    starred: Mapped[bool] = mapped_column(Boolean, default=False)
    repeat_count: Mapped[int] = mapped_column(Integer, default=1)  # times the same buyer enquired
    observers: Mapped[list] = mapped_column(JSON, default=list)  # user ids who can watch the lead
    products: Mapped[list] = mapped_column(JSON, default=list)  # extra products: [{product_id, qty}]
    region: Mapped[str] = mapped_column(String(60), default="")
    event_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    in_pool: Mapped[bool] = mapped_column(Boolean, default=False)  # common lead pool: unassigned, anyone can claim
    source_ref: Mapped[str] = mapped_column(String(100), default="", index=True)  # id at the portal it came from
    phone_key: Mapped[str] = mapped_column(String(15), default="", index=True)  # last 10 digits, for duplicate check
    sales_person_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("sales_team.id"), nullable=True, index=True)  # owner as chosen from the sales team
    ai_qualification: Mapped[dict] = mapped_column(JSON, default=dict)  # AI Lead Qualifier verdict: {verdict, score, confidence, checks[], at, engine}


class SalesPerson(TenantMixin, Base):
    """The sales team: ERPNext 'Sales Person' records, each tied to an employee. A lead's owner is picked from this list."""
    __tablename__ = "sales_team"
    name: Mapped[str] = mapped_column(String(200))
    erp_name: Mapped[str] = mapped_column(String(140), default="", index=True)  # ERPNext Sales Person id
    employee: Mapped[str] = mapped_column(String(60), default="")  # ERPNext Employee id
    parent_name: Mapped[str] = mapped_column(String(140), default="")  # the team head's Sales Person (tree)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_group: Mapped[bool] = mapped_column(Boolean, default=False)
    designation: Mapped[str] = mapped_column(String(120), default="")
    department: Mapped[str] = mapped_column(String(140), default="")
    branch: Mapped[str] = mapped_column(String(100), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, index=True)  # their Fieldwise login, if any


class Product(TenantMixin, Base):
    __tablename__ = "products"
    code: Mapped[str] = mapped_column(String(100), index=True)
    name: Mapped[str] = mapped_column(String(300))
    group: Mapped[str] = mapped_column(String(100), default="")
    uom: Mapped[str] = mapped_column(String(30), default="Nos")
    price: Mapped[float] = mapped_column(Float, default=0)
    erp_name: Mapped[str] = mapped_column(String(140), default="")
    category: Mapped[str] = mapped_column(String(100), default="")
    hsn: Mapped[str] = mapped_column(String(20), default="")
    gst_pct: Mapped[float] = mapped_column(Float, default=18)
    photos: Mapped[list] = mapped_column(JSON, default=list)
    brochure: Mapped[str | None] = mapped_column(String(300), nullable=True)
    video_url: Mapped[str] = mapped_column(String(300), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    moq: Mapped[float] = mapped_column(Float, default=0)
    vendor_ids: Mapped[list] = mapped_column(JSON, default=list)
    amc_charge: Mapped[float] = mapped_column(Float, default=0)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    kind: Mapped[str] = mapped_column(String(10), default="product")  # product | part
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # a part belongs to a product


class Task(TenantMixin, Base):
    __tablename__ = "tasks"
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    assignee_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), index=True, nullable=True)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    customer_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("customers.id"), nullable=True)
    priority: Mapped[str] = mapped_column(String(10), default="medium")
    status: Mapped[str] = mapped_column(String(30), default="open")
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    repeat: Mapped[str] = mapped_column(String(20), default="")  # ''|daily|weekly|monthly
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    geofence_m: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reminded: Mapped[bool] = mapped_column(Boolean, default=False)
    custom: Mapped[dict] = mapped_column(JSON, default=dict)
    lead_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    meeting_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)


class Visit(TenantMixin, Base):
    __tablename__ = "visits"
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    customer_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("customers.id"), nullable=True)
    task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    otp_hash: Mapped[str | None] = mapped_column(String(100), nullable=True)
    otp_expires: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    otp_attempts: Mapped[int] = mapped_column(Integer, default=0)
    otp_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    mom: Mapped[str] = mapped_column(Text, default="")  # minutes of meeting
    mom_emailed_to: Mapped[str] = mapped_column(String(300), default="")
    photo: Mapped[str | None] = mapped_column(String(300), nullable=True)
    waiting_minutes: Mapped[int] = mapped_column(Integer, default=0)


class Activity(TenantMixin, Base):
    """Timeline of record updates for leads/customers/contacts."""
    __tablename__ = "activities"
    entity: Mapped[str] = mapped_column(String(30), index=True)
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text: Mapped[str] = mapped_column(Text)


class Notification(TenantMixin, Base):
    __tablename__ = "notifications"
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30), default="info")
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(String(600), default="")
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    dedupe_key: Mapped[str] = mapped_column(String(120), default="", index=True)


class CustomField(TenantMixin, Base):
    __tablename__ = "custom_fields"
    entity: Mapped[str] = mapped_column(String(30))  # lead|customer|contact|task
    key: Mapped[str] = mapped_column(String(60))
    label: Mapped[str] = mapped_column(String(100))
    type: Mapped[str] = mapped_column(String(20), default="text")  # text|number|date|select|transport|route|card
    options: Mapped[list] = mapped_column(JSON, default=list)
    required: Mapped[bool] = mapped_column(Boolean, default=False)


class SavedView(TenantMixin, Base):
    __tablename__ = "saved_views"
    user_id: Mapped[int] = mapped_column(Integer)
    entity: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(100))
    filters: Mapped[dict] = mapped_column(JSON, default=dict)
    shared: Mapped[bool] = mapped_column(Boolean, default=False)


class SyncLog(TenantMixin, Base):
    __tablename__ = "sync_logs"
    domain: Mapped[str] = mapped_column(String(40))
    direction: Mapped[str] = mapped_column(String(10))
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(String(600), default="")


class Quotation(TenantMixin, Base):
    __tablename__ = "quotations"
    number: Mapped[str] = mapped_column(String(30))  # local reference, e.g. QTN-0007
    lead_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("leads.id"), nullable=True, index=True)
    customer_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("customers.id"), nullable=True, index=True)
    created_by: Mapped[int] = mapped_column(Integer)
    items: Mapped[list] = mapped_column(JSON, default=list)  # [{product_id, code, name, qty, rate, discount_pct, amount}]
    subtotal: Mapped[float] = mapped_column(Float, default=0)
    discount_total: Mapped[float] = mapped_column(Float, default=0)
    tax_pct: Mapped[float] = mapped_column(Float, default=18)
    total: Mapped[float] = mapped_column(Float, default=0)
    valid_till: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="draft")  # pending_approval|draft|sent|accepted|rejected
    approver_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    erp_name: Mapped[str] = mapped_column(String(140), default="")  # ERPNext quotation number, e.g. SAL-QTN-2026-00012
    erp_status: Mapped[str] = mapped_column(String(40), default="")
    erp_total: Mapped[float | None] = mapped_column(Float, nullable=True)
    erp_error: Mapped[str] = mapped_column(String(500), default="")
    terms: Mapped[str] = mapped_column(Text, default="")
    charges: Mapped[list] = mapped_column(JSON, default=list)  # additional charges [{name, amount}]
    charges_total: Mapped[float] = mapped_column(Float, default=0)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sent_to: Mapped[str] = mapped_column(String(300), default="")
    custom_pdf: Mapped[str | None] = mapped_column(String(300), nullable=True)  # user-uploaded PDF instead of generated


class Followup(TenantMixin, Base):
    __tablename__ = "followups"
    lead_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("leads.id"), nullable=True, index=True)
    customer_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("customers.id"), nullable=True, index=True)
    owner_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="call")  # call|visit|email|whatsapp|meeting
    due_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    notes: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|done|cancelled
    outcome: Mapped[str] = mapped_column(String(40), default="")
    outcome_notes: Mapped[str] = mapped_column(String(500), default="")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    repeat: Mapped[str] = mapped_column(String(20), default="")  # recurring visit plan (PJP): daily|weekly|monthly
    reminded: Mapped[bool] = mapped_column(Boolean, default=False)


class Meeting(TenantMixin, Base):
    __tablename__ = "meetings"
    title: Mapped[str] = mapped_column(String(200))
    owner_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    customer_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("customers.id"), nullable=True, index=True)
    lead_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("leads.id"), nullable=True, index=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    duration_min: Mapped[int] = mapped_column(Integer, default=45)
    mode: Mapped[str] = mapped_column(String(20), default="in_person")  # in_person|call|video
    location: Mapped[str] = mapped_column(String(300), default="")
    attendees: Mapped[str] = mapped_column(String(500), default="")
    agenda: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="scheduled")  # scheduled|in_progress|completed|cancelled
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    start_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    start_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    outcome: Mapped[str] = mapped_column(String(40), default="")
    mom: Mapped[str] = mapped_column(Text, default="")
    signature: Mapped[str | None] = mapped_column(String(300), nullable=True)
    photo: Mapped[str | None] = mapped_column(String(300), nullable=True)
    reminded: Mapped[bool] = mapped_column(Boolean, default=False)


class Target(TenantMixin, Base):
    __tablename__ = "targets"
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    month: Mapped[str] = mapped_column(String(7))  # YYYY-MM
    revenue: Mapped[float] = mapped_column(Float, default=0)
    visits: Mapped[int] = mapped_column(Integer, default=0)
    new_leads: Mapped[int] = mapped_column(Integer, default=0)


class SalesOrder(TenantMixin, Base):
    __tablename__ = "sales_orders"
    number: Mapped[str] = mapped_column(String(30))
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    quotation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[int] = mapped_column(Integer, index=True)
    items: Mapped[list] = mapped_column(JSON, default=list)
    subtotal: Mapped[float] = mapped_column(Float, default=0)
    tax_pct: Mapped[float] = mapped_column(Float, default=18)
    total: Mapped[float] = mapped_column(Float, default=0)
    delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    po_number: Mapped[str] = mapped_column(String(60), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="confirmed")  # pending_approval|confirmed|delivered|cancelled
    approver_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    credit_note: Mapped[str] = mapped_column(String(200), default="")
    po_file: Mapped[str | None] = mapped_column(String(300), nullable=True)  # scanned customer PO
    po_attached: Mapped[bool] = mapped_column(Boolean, default=False)  # attached to the ERPNext draft
    payment_terms: Mapped[str] = mapped_column(String(200), default="")
    sales_type: Mapped[str] = mapped_column(String(10), default="new")  # new | repeat
    erp_name: Mapped[str] = mapped_column(String(140), default="")
    erp_error: Mapped[str] = mapped_column(String(500), default="")


class Invoice(TenantMixin, Base):
    __tablename__ = "invoices"
    number: Mapped[str] = mapped_column(String(60), index=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    invoice_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date)
    total: Mapped[float] = mapped_column(Float, default=0)
    outstanding: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|partly_paid|paid
    erp_name: Mapped[str] = mapped_column(String(140), default="")
    kind: Mapped[str] = mapped_column(String(10), default="tax")  # tax | proforma (proforma never counts as outstanding)
    order_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    payment_terms: Mapped[str] = mapped_column(String(200), default="")
    tds_pct: Mapped[float] = mapped_column(Float, default=0)


class Payment(TenantMixin, Base):
    __tablename__ = "payments"
    receipt_no: Mapped[str] = mapped_column(String(30))
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    invoice_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    day: Mapped[date] = mapped_column(Date)
    amount: Mapped[float] = mapped_column(Float)
    mode: Mapped[str] = mapped_column(String(10))  # cash|cheque|upi|bank
    reference: Mapped[str] = mapped_column(String(100), default="")
    cheque_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    note: Mapped[str] = mapped_column(String(300), default="")
    photo: Mapped[str | None] = mapped_column(String(300), nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending|verified|rejected
    verified_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decision_note: Mapped[str] = mapped_column(String(300), default="")
    erp_ref: Mapped[str] = mapped_column(String(140), default="")
    erp_error: Mapped[str] = mapped_column(String(500), default="")
    tds_amount: Mapped[float] = mapped_column(Float, default=0)  # tax deducted at source by the customer


class Beat(TenantMixin, Base):
    """A recurring visit plan (PJP): an ordered list of customers a rep covers on set weekdays."""
    __tablename__ = "beats"
    name: Mapped[str] = mapped_column(String(120))
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    days: Mapped[list] = mapped_column(JSON, default=list)  # weekdays, Monday=0
    every_weeks: Mapped[int] = mapped_column(Integer, default=1)  # 1 = weekly, 2 = fortnightly...
    start_date: Mapped[date] = mapped_column(Date)
    customer_ids: Mapped[list] = mapped_column(JSON, default=list)  # ordered
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    territory: Mapped[str] = mapped_column(String(100), default="")
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)


class BeatVisit(TenantMixin, Base):
    __tablename__ = "beat_visits"
    beat_id: Mapped[int] = mapped_column(Integer, ForeignKey("beats.id"), index=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"))
    day: Mapped[date] = mapped_column(Date, index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(12), default="planned")  # planned|in_progress|visited|skipped|missed
    visit_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    skip_reason: Mapped[str] = mapped_column(String(200), default="")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    __table_args__ = (Index("ux_beat_visit", "beat_id", "customer_id", "day", unique=True),)


class Master(TenantMixin, Base):
    """Admin-managed pick lists: lead_source, region, regret_reason, product_group, product_category, unit, business_type, doc_type, ticket_type."""
    __tablename__ = "masters"
    kind: Mapped[str] = mapped_column(String(30), index=True)
    name: Mapped[str] = mapped_column(String(120))
    sort: Mapped[int] = mapped_column(Integer, default=0)


class Event(TenantMixin, Base):
    __tablename__ = "events"
    name: Mapped[str] = mapped_column(String(200))
    venue: Mapped[str] = mapped_column(String(200), default="")
    stall_no: Mapped[str] = mapped_column(String(40), default="")
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    budget: Mapped[float] = mapped_column(Float, default=0)
    owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")


class Renewal(TenantMixin, Base):
    """Re-order / AMC / renewal reminders."""
    __tablename__ = "renewals"
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), index=True)
    product_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    amount: Mapped[float] = mapped_column(Float, default=0)
    renewal_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(15), default="due")  # due|contacted|renewed|lost
    assignee_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lead_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_reminded: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str] = mapped_column(String(300), default="")


class Comment(TenantMixin, Base):
    """Notes with replies on leads / customers; unread tracking per user."""
    __tablename__ = "comments"
    entity: Mapped[str] = mapped_column(String(20), index=True)
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    mentions: Mapped[list] = mapped_column(JSON, default=list)
    read_by: Mapped[list] = mapped_column(JSON, default=list)


class CallLog(TenantMixin, Base):
    __tablename__ = "call_logs"
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    number: Mapped[str] = mapped_column(String(30), index=True)
    phone_key: Mapped[str] = mapped_column(String(15), default="", index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    direction: Mapped[str] = mapped_column(String(10), default="incoming")  # incoming|outgoing|missed
    started_at: Mapped[datetime] = mapped_column(DateTime)
    duration_sec: Mapped[int] = mapped_column(Integer, default=0)
    lead_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    customer_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disposition: Mapped[str] = mapped_column(String(30), default="")
    notes: Mapped[str] = mapped_column(String(500), default="")
    source: Mapped[str] = mapped_column(String(20), default="manual")  # manual|webhook|app|dialer


class Campaign(TenantMixin, Base):
    """Auto-dialer campaign: a list of numbers distributed to callers."""
    __tablename__ = "campaigns"
    name: Mapped[str] = mapped_column(String(200))
    mode: Mapped[str] = mapped_column(String(10), default="auto")  # auto (round-robin) | manual
    created_by: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(10), default="active")


class CampaignContact(TenantMixin, Base):
    __tablename__ = "campaign_contacts"
    campaign_id: Mapped[int] = mapped_column(Integer, ForeignKey("campaigns.id"), index=True)
    number: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(200), default="")
    company: Mapped[str] = mapped_column(String(200), default="")
    assigned_to: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending|disposed
    disposition: Mapped[str] = mapped_column(String(30), default="")  # connected|not_picked|call_back|not_interested|wrong_number|lead_created
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lead_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    callback_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str] = mapped_column(String(300), default="")


class Department(TenantMixin, Base):
    __tablename__ = "departments"
    name: Mapped[str] = mapped_column(String(100))
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Designation(TenantMixin, Base):
    __tablename__ = "designations"
    name: Mapped[str] = mapped_column(String(100))


class IncentivePlan(TenantMixin, Base):
    __tablename__ = "incentive_plans"
    name: Mapped[str] = mapped_column(String(120))
    metric: Mapped[str] = mapped_column(String(15), default="revenue")  # revenue|orders|collections|visits
    slabs: Mapped[list] = mapped_column(JSON, default=list)  # [{from, to, pct, flat}]
    user_ids: Mapped[list] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class LoginAttempt(TenantMixin, Base):
    __tablename__ = "login_attempts"
    email: Mapped[str] = mapped_column(String(200))
    ip: Mapped[str] = mapped_column(String(60), default="")
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str] = mapped_column(String(100), default="")


class Vendor(TenantMixin, Base):
    __tablename__ = "vendors"
    name: Mapped[str] = mapped_column(String(200))
    contact_person: Mapped[str] = mapped_column(String(200), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    gstin: Mapped[str] = mapped_column(String(30), default="")
    city: Mapped[str] = mapped_column(String(100), default="")
    country: Mapped[str] = mapped_column(String(50), default="India")
    status: Mapped[str] = mapped_column(String(10), default="active")


class PurchaseOrder(TenantMixin, Base):
    __tablename__ = "purchase_orders"
    number: Mapped[str] = mapped_column(String(30))
    vendor_id: Mapped[int] = mapped_column(Integer, ForeignKey("vendors.id"), index=True)
    created_by: Mapped[int] = mapped_column(Integer)
    items: Mapped[list] = mapped_column(JSON, default=list)
    total: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(12), default="draft")  # draft|issued|received|cancelled
    expected_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")


class Dealer(TenantMixin, Base):
    __tablename__ = "dealers"
    name: Mapped[str] = mapped_column(String(200))
    ceo_name: Mapped[str] = mapped_column(String(200), default="")
    contact_person: Mapped[str] = mapped_column(String(200), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    territory: Mapped[str] = mapped_column(String(100), default="")
    services: Mapped[list] = mapped_column(JSON, default=list)  # tagged services / product groups


class Ticket(TenantMixin, Base):
    """Helpdesk tickets raised by staff to the workspace admins."""
    __tablename__ = "tickets"
    number: Mapped[str] = mapped_column(String(20))
    kind: Mapped[str] = mapped_column(String(60), default="General")
    subject: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    raised_by: Mapped[int] = mapped_column(Integer, index=True)
    assigned_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="open")  # open|in_progress|closed
    priority: Mapped[str] = mapped_column(String(10), default="medium")


class TicketComment(TenantMixin, Base):
    __tablename__ = "ticket_comments"
    ticket_id: Mapped[int] = mapped_column(Integer, ForeignKey("tickets.id"), index=True)
    user_id: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)


class MessageTemplate(TenantMixin, Base):
    """Email / WhatsApp / SMS templates with {placeholders}."""
    __tablename__ = "message_templates"
    key: Mapped[str] = mapped_column(String(40))  # quotation_send | lead_welcome | followup_reminder | payment_reminder | custom
    name: Mapped[str] = mapped_column(String(120))
    channel: Mapped[str] = mapped_column(String(10), default="email")  # email|whatsapp|sms
    subject: Mapped[str] = mapped_column(String(200), default="")
    body: Mapped[str] = mapped_column(Text, default="")


class IntakeLog(TenantMixin, Base):
    __tablename__ = "intake_logs"
    source: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(15))  # created|repeat|duplicate|rejected|error
    lead_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    message: Mapped[str] = mapped_column(String(300), default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class Territory(TenantMixin, Base):
    """A work area drawn as a polygon; assigned executives are alerted on when they leave it during duty."""
    __tablename__ = "territories"
    name: Mapped[str] = mapped_column(String(120))
    polygon: Mapped[list] = mapped_column(JSON, default=list)  # [[lat, lng], ...]
    user_ids: Mapped[list] = mapped_column(JSON, default=list)
    alert: Mapped[bool] = mapped_column(Boolean, default=True)


class GeoEvent(TenantMixin, Base):
    __tablename__ = "geo_events"
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(20))  # arrive | depart | territory_exit | territory_enter
    ref_type: Mapped[str] = mapped_column(String(12))  # customer | territory
    ref_id: Mapped[int] = mapped_column(Integer, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)  # on a depart event: time spent on site


class Shift(TenantMixin, Base):
    __tablename__ = "shifts"
    name: Mapped[str] = mapped_column(String(80))
    start: Mapped[str] = mapped_column(String(5), default="09:30")
    end: Mapped[str] = mapped_column(String(5), default="18:30")
    grace_min: Mapped[int] = mapped_column(Integer, default=15)
    break_min: Mapped[int] = mapped_column(Integer, default=60)
    weekly_off: Mapped[list] = mapped_column(JSON, default=list)


class ShiftRoster(TenantMixin, Base):
    """Per-day override: a specific shift, or shift_id null = day off."""
    __tablename__ = "shift_roster"
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    shift_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ShiftSwap(TenantMixin, Base):
    __tablename__ = "shift_swaps"
    from_user: Mapped[int] = mapped_column(Integer, index=True)
    to_user: Mapped[int] = mapped_column(Integer, index=True)
    day: Mapped[date] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(15), default="requested")  # requested|accepted|approved|declined|rejected


class FormTemplate(TenantMixin, Base):
    """Configurable checklists / audit forms / surveys attached to visits, meetings or tasks."""
    __tablename__ = "form_templates"
    name: Mapped[str] = mapped_column(String(120))
    applies_to: Mapped[str] = mapped_column(String(10), default="visit")  # visit|meeting|task|general
    fields: Mapped[list] = mapped_column(JSON, default=list)  # [{key,label,type,options,required}]
    mandatory: Mapped[bool] = mapped_column(Boolean, default=False)  # must be filled before the visit can be ended
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class FormResponse(TenantMixin, Base):
    __tablename__ = "form_responses"
    template_id: Mapped[int] = mapped_column(Integer, index=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    visit_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    meeting_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    customer_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class Announcement(TenantMixin, Base):
    __tablename__ = "announcements"
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    audience: Mapped[str] = mapped_column(String(10), default="all")  # all | team | users
    user_ids: Mapped[list] = mapped_column(JSON, default=list)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    read_by: Mapped[list] = mapped_column(JSON, default=list)


class ReportSchedule(TenantMixin, Base):
    """Emails a CSV report on a schedule."""
    __tablename__ = "report_schedules"
    name: Mapped[str] = mapped_column(String(120))
    report: Mapped[str] = mapped_column(String(20))  # attendance|expenses|dcr|productivity|beats
    frequency: Mapped[str] = mapped_column(String(10), default="daily")  # daily|weekly|monthly
    hour: Mapped[int] = mapped_column(Integer, default=8)
    recipients: Mapped[str] = mapped_column(String(500), default="")
    created_by: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_sent: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[str] = mapped_column(String(200), default="")


class AuditLog(TenantMixin, Base):
    """Who changed what: sign-ins aside (see LoginAttempt), security-relevant admin actions."""
    __tablename__ = "audit_logs"
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(60), index=True)
    entity: Mapped[str] = mapped_column(String(40), default="")
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[str] = mapped_column(String(500), default="")
    ip: Mapped[str] = mapped_column(String(60), default="")


class JobLock(Base):
    """Lets exactly one app instance run the background scheduler."""
    __tablename__ = "job_locks"
    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    holder: Mapped[str] = mapped_column(String(120), default="")
    until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
