"""PDF generation for quotations, proforma and tax invoices (ReportLab)."""
import io
from datetime import date
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, KeepTogether
from . import config

_ONES = "Zero One Two Three Four Five Six Seven Eight Nine Ten Eleven Twelve Thirteen Fourteen Fifteen Sixteen Seventeen Eighteen Nineteen".split()
_TENS = "_ _ Twenty Thirty Forty Fifty Sixty Seventy Eighty Ninety".split()


def _w(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else "")
    if n < 1000:
        return _ONES[n // 100] + " Hundred" + (" " + _w(n % 100) if n % 100 else "")
    for div, name in ((10_000_000, "Crore"), (100_000, "Lakh"), (1000, "Thousand")):
        if n >= div:
            return _w(n // div) + f" {name}" + (" " + _w(n % div) if n % div else "")
    return ""


def amount_words(amount: float, cur: str = "INR") -> str:
    whole, paise = int(amount), round((amount - int(amount)) * 100)
    unit, sub = {"INR": ("Rupees", "Paise"), "AED": ("Dirhams", "Fils"), "SAR": ("Riyals", "Halalas")}.get(cur, (cur, "Cents"))
    txt = f"{unit} {_w(whole) if whole else 'Zero'}" + (f" and {_w(paise)} {sub}" if paise else "") + " Only"
    return txt


def money(n: float, cur: str = "INR") -> str:
    sym = {"INR": "Rs. "}.get(cur, cur + " ")
    s = f"{abs(n):,.2f}"
    if cur == "INR":  # Indian digit grouping
        whole, dec = f"{abs(n):.2f}".split(".")
        head, tail = whole[:-3], whole[-3:]
        head = ",".join([head[max(i - 2, 0):i] for i in range(len(head), 0, -2)][::-1]) if head else ""
        s = (head + "," if head else "") + tail + "." + dec
    return ("-" if n < 0 else "") + sym + s


def build_pdf(*, title: str, number: str, date_: date, valid_till: date | None, company: dict, party: dict, items: list, charges: list, subtotal: float, discount: float, tax_pct: float,
              total: float, currency: str, terms: str = "", notes: str = "", extra_rows: list[tuple[str, str]] | None = None, reference: str = "", payment_terms: str = "") -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm, bottomMargin=16 * mm, title=f"{title} {number}")
    ss = getSampleStyleSheet()
    base = ParagraphStyle("b", parent=ss["Normal"], fontName="Helvetica", fontSize=9, leading=12)
    small = ParagraphStyle("s", parent=base, fontSize=8, leading=10.5, textColor=colors.HexColor("#555555"))
    h = ParagraphStyle("h", parent=base, fontName="Helvetica-Bold", fontSize=16, leading=20, textColor=colors.HexColor("#0f766e"))
    bold = ParagraphStyle("bd", parent=base, fontName="Helvetica-Bold")
    P = lambda t, st=base: Paragraph(str(t).replace("&", "&amp;").replace("<", "&lt;").replace("\n", "<br/>"), st)
    story = []
    head = []
    logo = company.get("logo_path")
    if logo and (config.UPLOAD_DIR / logo).exists():
        try:
            head.append(Image(str(config.UPLOAD_DIR / logo), width=30 * mm, height=18 * mm, kind="proportional"))
        except Exception:
            head.append("")
    else:
        head.append("")
    addr = ", ".join(x for x in [company.get("address"), company.get("city"), company.get("state"), company.get("pin")] if x)
    ids = "  ".join(f"{k}: {company[v]}" for k, v in (("GSTIN", "gstin"), ("PAN", "pan"), ("CIN", "cin")) if company.get(v))
    contact = "  ".join(x for x in [company.get("phone"), company.get("email"), company.get("website")] if x)
    head.append([P(company.get("name", ""), ParagraphStyle("cn", parent=bold, fontSize=12, leading=15)), P(addr, small), P(ids, small), P(contact, small)])
    head.append([P(title.upper(), h)])
    t = Table([head], colWidths=[34 * mm, 100 * mm, 46 * mm])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("ALIGN", (2, 0), (2, 0), "RIGHT"), ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("LINEBELOW", (0, 0), (-1, 0), 1.2, colors.HexColor("#0f766e"))]))
    story += [t, Spacer(1, 5 * mm)]
    meta = [[P("<b>To</b>"), P(party.get("name", ""), bold), P("<b>No.</b>"), P(number)], ["", P("\n".join(x for x in [party.get("contact"), party.get("address"), party.get("phone"), party.get("email"), (f"GSTIN: {party['gstin']}" if party.get("gstin") else "")] if x), small),
            P("<b>Date</b>"), P(date_.strftime("%d %b %Y"))]]
    if valid_till:
        meta.append(["", "", P("<b>Valid till</b>"), P(valid_till.strftime("%d %b %Y"))])
    if reference:
        meta.append(["", "", P("<b>Ref.</b>"), P(reference)])
    if payment_terms:
        meta.append(["", "", P("<b>Payment</b>"), P(payment_terms)])
    mt = Table(meta, colWidths=[12 * mm, 90 * mm, 22 * mm, 56 * mm])
    mt.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    story += [mt, Spacer(1, 4 * mm)]
    rows = [["#", "Description", "Qty", "Unit", "Rate", "Disc %", "Amount"]]
    for i, it in enumerate(items, 1):
        desc = it.get("name", "") + (f"\n{it['code']}" if it.get("code") else "") + (f" · HSN {it['hsn']}" if it.get("hsn") else "")
        rows.append([str(i), P(desc, small), f"{it['qty']:g}", it.get("uom", "Nos"), money(it["rate"], currency), f"{it.get('discount_pct', 0):g}", money(it["amount"], currency)])
    it_t = Table(rows, colWidths=[8 * mm, 66 * mm, 15 * mm, 14 * mm, 28 * mm, 14 * mm, 35 * mm], repeatRows=1)
    it_t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f766e")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                              ("ALIGN", (2, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cccccc")), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f7f9")])]))
    story += [it_t, Spacer(1, 3 * mm)]
    net = subtotal - discount
    ch_total = sum(c["amount"] for c in charges)
    tax = round((net + ch_total) * tax_pct / 100, 2)
    tot = [["Subtotal", money(subtotal, currency)]]
    if discount:
        tot.append(["Discount", "- " + money(discount, currency)])
    for c in charges:
        tot.append([c["name"], money(c["amount"], currency)])
    tot.append([f"Tax ({tax_pct:g}%)", money(tax, currency)])
    for k, v in extra_rows or []:
        tot.append([k, v])
    tot.append(["TOTAL", money(total, currency)])
    tt = Table(tot, colWidths=[40 * mm, 38 * mm], hAlign="RIGHT")
    tt.setStyle(TableStyle([("ALIGN", (1, 0), (1, -1), "RIGHT"), ("FONTSIZE", (0, 0), (-1, -1), 9), ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"), ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.black), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    story += [tt, Spacer(1, 2 * mm), P("Amount in words: " + amount_words(total, currency), small), Spacer(1, 4 * mm)]
    if terms:
        story += [KeepTogether([P("Terms & Conditions", bold), Spacer(1, 1 * mm), P(terms, small)]), Spacer(1, 3 * mm)]
    if notes:
        story += [P("Notes", bold), P(notes, small), Spacer(1, 3 * mm)]
    bank = company.get("bank") or {}
    if any(bank.values()):
        story += [KeepTogether([P("Bank details", bold), P(f"{bank.get('name', '')}  A/c: {bank.get('account', '')}  IFSC: {bank.get('ifsc', '')}  {bank.get('branch', '')}", small)]), Spacer(1, 6 * mm)]
    sig = Table([["", P(f"For {company.get('name', '')}", bold)], ["", ""], ["", P("Authorised signatory", small)]], colWidths=[110 * mm, 70 * mm], rowHeights=[6 * mm, 14 * mm, 6 * mm])
    story += [KeepTogether([sig])]
    doc.build(story)
    return buf.getvalue()
