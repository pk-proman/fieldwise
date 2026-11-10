"""AI Lead Qualifier: scores every new lead, decides QUALIFIED or REJECTED, and writes the reasoning into the lead as a remark.

How it decides (rules-v1, always on, instant, explainable - no data leaves the server):
  A transparent 100-point scorecard over seven areas, plus "hard reject" checks for junk and non-buyers.
    Contactability 20 | Buyer identity 10 | Product fit 25 | Intent & specificity 20 | Source quality 10 | Geography 10 | Relationship 5
  Hard rejects (any one rejects the lead, whatever the score): junk/fake phone, throw-away email, placeholder name with no company,
  or text showing the sender is selling to us / job hunting / soliciting rather than buying.
  Existing customers are never rejected.  Otherwise: score >= threshold (default 45) = QUALIFIED, below = REJECTED.
  Confidence says how far the score is from the line; 'low' means a person should take a look.
Optionally (Settings -> AI) a language model adds a written second opinion as a further remark. It never changes the verdict.
Rejection is a flag by default (tag + remark + cold). Turn on 'auto-regret' to also move the lead to the Lost stage.
"""
import logging
import re
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified
from .models import Lead, Product, Customer, Activity, utcnow

log = logging.getLogger("fieldwise.qualifier")
ENGINE = "rules-v1"

DEFAULTS = {
    "enabled": True, "threshold": 45, "auto_regret": False,
    "countries": ["India", "UAE", "Saudi Arabia", "Qatar", "Kuwait", "Oman", "Bahrain", "Jordan", "Iraq", "Egypt", "Nepal", "Bangladesh", "Sri Lanka", "Bhutan"],
    "blocked_domains": [], "junk_words": [], "extra_keywords": [],
}
COUNTRY_ALIAS = {"uae": "uae", "united arab emirates": "uae", "ksa": "saudi arabia", "saudi": "saudi arabia", "kingdom of saudi arabia": "saudi arabia", "bharat": "india"}
FREE_MAIL = {"gmail.com", "yahoo.com", "yahoo.in", "yahoo.co.in", "outlook.com", "hotmail.com", "live.com", "rediffmail.com", "icloud.com", "proton.me", "protonmail.com", "aol.com", "ymail.com", "msn.com"}
DISPOSABLE = {"mailinator.com", "tempmail.com", "10minutemail.com", "guerrillamail.com", "yopmail.com", "trashmail.com", "sharklasers.com", "throwawaymail.com", "dispostable.com", "getnada.com", "maildrop.cc"}
JUNK_NAMES = {"test", "testing", "asdf", "asdfgh", "qwerty", "xxx", "abc", "abcd", "demo", "dummy", "none", "na", "n/a", "nil", "unknown", "name", "sample", "aaa", "zzz", "fake"}
NOT_A_BUYER = [  # (pattern, label)
    (r"\b(job|jobs|vacanc(?:y|ies)|hiring|recruit\w*|resume|cv|internship|career)\b", "asks about a job / hiring rather than buying"),
    (r"\b(seo|backlinks?|web ?site design|web ?development|digital marketing|social media marketing|lead generation services?|app development)\b", "offers marketing / IT services"),
    (r"\b(loan|insurance|credit card|forex|crypto|investment scheme)\b", "financial solicitation"),
    (r"\b(we (?:are|r) (?:a |the )?(?:leading |reputed )?(?:manufacturer|supplier|exporter|trader)s?|we (?:offer|provide|supply|sell)|our (?:products|services) (?:include|are)|looking for (?:buyers|distributors|agents|dealers)|supplier of|selling)\b", "is selling to us, not buying"),
    (r"\b(casino|betting|lottery|prize|winner|click here|http[s]?://\S{25,})\b", "spam / scam wording"),
]
BUY_WORDS = r"\b(quote|quotation|price|pricing|rate|cost|budget|order|purchase|buy|buying|require[sd]?|requirement|need|needed|want|looking for|enquiry|inquiry|supply|delivery|tender|project|plant|installation|specification|catalog(?:ue)?|brochure)\b"
TIME_WORDS = r"\b(urgent|urgently|immediately|asap|this (?:week|month|quarter)|next (?:week|month)|within \d+ ?(?:days?|weeks?|months?)|by (?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*|today|tomorrow)\b"
SPEC_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:tph|tpd|mtph|t/h|ton(?:s|ne|nes)?|mt|mm|cm|m3|hp|kw|kva|deck|decks|nos|units?|pcs|sets?|machines?)\b|\b\d+(?:\.\d+)?\s*[x×]\s*\d+(?:\.\d+)?\b", re.I)
STOP = {"proman", "complete", "assembly", "with", "without", "machine", "machines", "type", "the", "and", "for", "unit", "nos", "new", "old", "set", "pro", "test"}
SOURCE_SCORE = [("referral", 10), ("exhibition", 10), ("event", 10), ("walk", 10), ("existing", 10), ("customer", 10), ("indiamart", 7), ("tradeindia", 7), ("justdial", 7), ("website", 7),
                ("web", 7), ("inbound", 7), ("call", 7), ("whatsapp", 6), ("email", 6), ("facebook", 5), ("instagram", 5), ("linkedin", 5), ("google", 6), ("ads", 5), ("import", 4), ("cold", 3)]


def settings_of(tenant) -> dict:
    return {**DEFAULTS, **((tenant.settings or {}).get("ai_qualifier") or {})}


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def _phone_check(phone: str):
    """-> (points 0..12, note, junk?)"""
    raw, d = (phone or "").strip(), _digits(phone)
    if not d:
        return 0, "no phone number", False
    local = d[-10:] if len(d) >= 10 else d
    if len(set(local)) <= 2 and len(local) >= 8 or local in ("1234567890", "0123456789", "9876543210", "0000000000") or re.fullmatch(r"(\d)\1{7,}", local) or d.startswith("00000"):
        return 0, f"phone {raw} looks fake (repeating / sequential digits)", True
    if len(d) == 10 and d[0] in "6789":
        return 12, "valid Indian mobile number", False
    if raw.startswith("+") and 8 <= len(d) <= 15:
        return 11 if not (d.startswith("91") and len(d) == 12 and d[2] not in "6789") else 4, "valid international number", False
    if len(d) == 12 and d.startswith("91") and d[2] in "6789":
        return 12, "valid Indian mobile number (with 91)", False
    if 8 <= len(d) <= 15:
        return 8, "phone number has a plausible length but unusual format", False
    return 2, f"phone number looks incomplete ({len(d)} digits)", False


def _email_check(email: str, blocked: set):
    """-> (points 0..8, note, disposable?)"""
    e = (email or "").strip().lower()
    if not e:
        return 0, "no email", False
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", e):
        return 0, "email address is malformed", False
    dom = e.split("@")[1]
    if dom in DISPOSABLE or dom in blocked:
        return 0, f"email domain {dom} is a throw-away / blocked domain", True
    if dom in FREE_MAIL:
        return 4, "personal mailbox (gmail-type)", False
    return 8, f"company email domain ({dom})", False


def _norm_country(c: str) -> str:
    c = (c or "").strip().lower()
    return COUNTRY_ALIAS.get(c, c)


def _keywords(db, tenant_id: int, extra) -> set:
    words = set()
    for (n,) in db.execute(select(Product.name).where(Product.tenant_id == tenant_id, Product.deleted_at.is_(None), Product.disabled.is_(False), Product.code != "UNSPEC")):
        for w in re.findall(r"[a-zA-Z]{3,}", n or ""):
            if w.lower() not in STOP:
                words.add(w.lower())
    return words | {str(x).lower() for x in (extra or [])}


def evaluate(db, tenant, lead: Lead, product_text: str = "") -> dict:
    """Pure scoring of one lead. Does not write anything."""
    cfg = settings_of(tenant)
    text = " ".join(filter(None, [lead.notes or "", product_text or "", lead.company or "", lead.name or ""])).lower()
    notes = (lead.notes or "") + " " + (product_text or "")
    checks, flags, score = [], [], 0

    def add(area, pts, mx, note):
        nonlocal score
        pts = max(0, min(pts, mx))
        score += pts
        checks.append({"area": area, "points": pts, "max": mx, "note": note, "ok": pts >= mx * 0.6})

    # 1. contactability (20)
    pp, pn, pjunk = _phone_check(lead.phone)
    ep, en, edis = _email_check(lead.email, {d.lower() for d in cfg.get("blocked_domains") or []})
    ep = min(ep, 8)
    add("Contactability", pp + ep, 20, f"{pn}; {en}")
    if pjunk:
        flags.append("fake-looking phone number")
    if edis:
        flags.append("throw-away email domain")
    if not lead.phone and not lead.email:
        flags.append("no way to contact the buyer")

    # 2. identity (10)
    nm, co = (lead.name or "").strip(), (lead.company or "").strip()
    name_junk = (nm.lower() in JUNK_NAMES) or len(nm) < 2 or (nm.replace(" ", "").isdigit())
    co_junk = co.lower() in JUNK_NAMES or len(co) < 3
    ip = (6 if co and not co_junk else 0) + (4 if nm and not name_junk and len(nm) >= 3 else 0)
    add("Buyer identity", ip, 10, ("company named" if co and not co_junk else "no company name") + "; " + ("real-looking contact name" if not name_junk and nm else "contact name missing or a placeholder"))
    if name_junk and (not co or co_junk):
        flags.append("placeholder name and no company")

    # 3. product fit (25)
    pr = db.get(Product, lead.product_id) if lead.product_id else None
    specified = bool(pr and pr.code != "UNSPEC")
    kws = _keywords(db, tenant.id, cfg.get("extra_keywords"))
    hits = sorted({k for k in kws if re.search(rf"\b{re.escape(k)}\b", text)})
    fp = (15 if specified else 0) + min(10, 2 * len(hits))
    add("Product fit", fp, 25, (f"matches catalogue product '{pr.name}'" if specified else "no catalogue product identified") + (f"; mentions {', '.join(hits[:4])}" if hits else "; no product keywords in the enquiry"))

    # 4. intent & specificity (20)
    spec = SPEC_RE.findall(notes)
    buy = set(re.findall(BUY_WORDS, text, re.I))
    tw = re.search(TIME_WORDS, text, re.I)
    ip2 = (6 if spec else 0) + min(8, 2 * len(buy)) + (3 if tw else 0) + (3 if len((lead.notes or "").strip()) > 40 else 0) + (3 if (lead.value or 0) > 0 else 0)
    bits = ([f"gives specifics ({spec[0].strip()})"] if spec else []) + ([f"buying language ({', '.join(sorted(buy)[:3])})"] if buy else []) + (["states a timeline"] if tw else []) + (["has an expected value"] if (lead.value or 0) > 0 else [])
    add("Intent & specificity", ip2, 20, "; ".join(bits) or "no quantity, capacity, timeline or buying language")

    # 5. source (10)
    src = (lead.source or "").lower()
    sp = next((v for k, v in SOURCE_SCORE if k in src), 4)
    add("Source quality", sp, 10, f"source '{lead.source or 'unknown'}'")

    # 6. geography (10)
    served = {_norm_country(c) for c in cfg.get("countries") or []}
    ctry = _norm_country(lead.country)
    add("Geography", 10 if (not served or ctry in served) else 3, 10, f"{lead.country or 'unknown country'} is {'in' if (not served or ctry in served) else 'outside'} the served markets")

    # 7. relationship (5)
    known = None
    pk = lead.phone_key or _digits(lead.phone)[-10:]
    if pk or lead.email:
        conds = []
        if pk:
            conds.append(Customer.phone_key == pk)
        if lead.email:
            conds.append(Customer.email.ilike(lead.email.strip()))
        from sqlalchemy import or_
        known = db.scalar(select(Customer).where(Customer.tenant_id == tenant.id, Customer.deleted_at.is_(None), or_(*conds)).limit(1))
    rp = (5 if known else 0) + (3 if (lead.repeat_count or 1) > 1 else 0)
    add("Relationship", rp, 5, ("existing customer " + known.name) if known else ("repeat enquiry" if (lead.repeat_count or 1) > 1 else "new to us"))

    # hard rejects: not a buyer
    for pat, label in NOT_A_BUYER:
        if re.search(pat, text, re.I):
            flags.append(label)
            break
    for w in cfg.get("junk_words") or []:
        if w and re.search(rf"\b{re.escape(str(w).lower())}\b", text):
            flags.append(f"contains blocked word '{w}'")
            break

    threshold = int(cfg.get("threshold", 45))
    if known:  # an existing customer is never rejected
        verdict, confidence, flags = "qualified", "high", []
    elif flags:
        verdict, confidence = "rejected", "high"
    else:
        verdict = "qualified" if score >= threshold else "rejected"
        gap = abs(score - threshold)
        confidence = "high" if gap >= 15 else "medium" if gap >= 7 else "low"
    if known and verdict == "qualified":
        score = max(score, threshold + 15)

    missing = []
    if not spec:
        missing.append("quantity / capacity")
    if not tw:
        missing.append("timeline")
    if not specified:
        missing.append("which product")
    if not co or co_junk:
        missing.append("company name")
    steps = ([f"Call within the hour; confirm {', '.join(missing[:3])}." if missing else "Call within the hour and book a visit / send a quotation."] if verdict == "qualified"
             else ["Verify by one quick call or WhatsApp before discarding (borderline)." if confidence == "low" else "Do not spend field time; park or regret with the reason shown."])
    summary = (f"{'QUALIFIED' if verdict == 'qualified' else 'REJECTED'} - score {score}/100 (line {threshold}), {confidence} confidence."
               + (f" Rejected because: {'; '.join(flags)}." if verdict == "rejected" and flags else ""))
    return {"verdict": verdict, "score": score, "threshold": threshold, "confidence": confidence, "engine": ENGINE, "at": utcnow().isoformat(), "checks": checks,
            "flags": flags, "next_steps": steps, "summary": summary}


def remark_text(r: dict) -> str:
    lines = [f"AI Lead Qualifier: {r['summary']}"]
    lines += [f"{'+' if c['ok'] else '-'} {c['area']} {c['points']}/{c['max']}: {c['note']}" for c in r["checks"]]
    if r["flags"]:
        lines.append("Red flags: " + "; ".join(r["flags"]))
    lines.append("Next: " + " ".join(r["next_steps"]))
    return "\n".join(lines)


def apply(db, tenant, lead: Lead, result: dict, *, first_time: bool = True, auto_regret: bool | None = None):
    """Stores the verdict, writes the remark (timeline + a one-line note), tags, and sets temperature on a new lead."""
    from .common import log_activity
    from . import svc
    cfg = settings_of(tenant)
    lead.ai_qualification = result
    flag_modified(lead, "ai_qualification")
    q = result["verdict"] == "qualified"
    tags = [t for t in (lead.tags or []) if t not in ("ai-qualified", "ai-rejected")]
    lead.tags = tags + ["ai-qualified" if q else "ai-rejected"]
    if first_time and lead.temperature == "warm":
        lead.temperature = "hot" if (q and result["score"] >= 75) else "cold" if not q else "warm"
    line = f"[AI qualifier {utcnow():%d-%b}] {'Qualified' if q else 'Rejected'} {result['score']}/100" + ("" if q or not result["flags"] else f" ({'; '.join(result['flags'])[:120]})")
    if line[:30] not in (lead.notes or ""):
        lead.notes = ((lead.notes or "").rstrip() + ("\n" if lead.notes else "") + line)[:2000]
    log_activity(db, tenant.id, "lead", lead.id, None, remark_text(result))
    do_regret = cfg.get("auto_regret") if auto_regret is None else auto_regret
    if not q and do_regret and result["flags"]:
        lost = next((s["key"] for s in svc.stages_of(tenant) if s["kind"] == "lost"), None)
        if lost:
            lead.status, lead.stage_changed_at, lead.lost_reason = lost, utcnow(), ("AI qualifier: " + "; ".join(result["flags"]))[:200]
            log_activity(db, tenant.id, "lead", lead.id, None, "AI Lead Qualifier moved this lead to Lost (auto-regret is on).")


def qualify_new_lead(db, tenant, lead: Lead, product_text: str = ""):
    """Hook called when a lead is created. Never raises: a qualifier problem must not block lead capture."""
    try:
        if not settings_of(tenant).get("enabled", True):
            return None
        r = evaluate(db, tenant, lead, product_text)
        apply(db, tenant, lead, r)
        _maybe_second_opinion(tenant, lead, r)
        return r
    except Exception:  # noqa
        log.exception("AI qualifier failed for lead %s", getattr(lead, "id", "?"))
        return None


# ---------------------------------------------------------------- optional language-model second opinion
SYSTEM = ("You review a sales-lead scorecard for an industrial equipment maker (vibrating screens, crushers, feeders, sizers). In at most 3 short sentences say whether you agree "
          "with the verdict and why, and the single best question the salesperson should ask on the first call. Plain text, no markdown.")


def _maybe_second_opinion(tenant, lead, result):
    from . import svc
    c = (tenant.settings or {}).get("ai") or {}
    if not (c.get("enabled") and svc.has_secret(tenant, "ai_key")):
        return
    import threading
    payload = {"lead": {"name": lead.name, "company": lead.company, "city": lead.city, "country": lead.country, "source": lead.source, "notes": (lead.notes or "")[:600]},
               "verdict": result["verdict"], "score": result["score"], "checks": [f"{x['area']} {x['points']}/{x['max']}: {x['note']}" for x in result["checks"]], "flags": result["flags"]}
    threading.Thread(target=second_opinion, args=(tenant.id, lead.id, payload), daemon=True).start()


def second_opinion(tenant_id: int, lead_id: int, payload: dict, client=None):
    import json
    from .db import SessionLocal
    from .models import Tenant
    from .routers import ai as ai_router
    from .common import log_activity
    db = SessionLocal()
    try:
        t = db.get(Tenant, tenant_id)
        raw = ai_router._call_provider(t, SYSTEM, json.dumps(payload), client)
        text = re.sub(r"\s+", " ", raw).strip()[:600]
        if text:
            log_activity(db, tenant_id, "lead", lead_id, None, f"AI second opinion: {text}")
            db.commit()
    except Exception as e:  # noqa
        log.warning("second opinion failed: %s", e)
        db.rollback()
    finally:
        db.close()
