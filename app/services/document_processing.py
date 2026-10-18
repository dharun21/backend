import re
from datetime import date, datetime
from pathlib import Path

from pypdf import PdfReader

AMOUNT_PATTERN = re.compile(r"(?:[$€£₹]\s*)?(-?\d[\d,]*(?:\.\d{1,2})?)")
INVOICE_NUMBER_PATTERN = re.compile(
    r"\b(?:invoice|bill)\s*(?:number|no\.?|#|id)\s*[:#-]?\s*([A-Z0-9][A-Z0-9/_-]{1,})",
    re.IGNORECASE,
)


def _date_from_label(text: str, label_pattern: str) -> date | None:
    match = re.search(
        rf"(?:{label_pattern})\s*[:#-]?\s*(\d{{4}}[-/]\d{{1,2}}[-/]\d{{1,2}}|\d{{1,2}}[-/]\d{{1,2}}[-/]\d{{2,4}}|[A-Za-z]{{3,9}}\s+\d{{1,2}},?\s+\d{{4}}|\d{{1,2}}\s+[A-Za-z]{{3,9}}\s+\d{{4}})",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None

    value = match.group(1).replace(",", "")
    for date_format in (
        "%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y", "%d-%m-%Y",
        "%b %d %Y", "%B %d %Y", "%d %b %Y", "%d %B %Y",
    ):
        try:
            return datetime.strptime(value, date_format).date()
        except ValueError:
            continue
    return None


def extract_invoice_fields(pdf_path: Path) -> tuple[dict, list[str]]:
    reader = PdfReader(str(pdf_path), strict=False)
    if reader.is_encrypted:
        raise ValueError("Encrypted PDFs cannot be processed yet.")
    if len(reader.pages) > 100:
        raise ValueError("PDFs over 100 pages need to be split before processing.")

    text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    if not text:
        return {}, ["No selectable text was found. This may be a scanned PDF; OCR is not configured yet."]

    invoice_match = INVOICE_NUMBER_PATTERN.search(text)
    due_date = _date_from_label(text, r"due date|payment due")
    issue_date = _date_from_label(text, r"invoice date|bill date|date of invoice|issued on")

    total_amount = None
    for line in text.splitlines():
        if re.search(r"\b(grand total|total due|amount due|invoice total|total amount|total)\b", line, re.IGNORECASE) and not re.search(r"\b(subtotal|tax|paid|quantity)\b", line, re.IGNORECASE):
            amounts = AMOUNT_PATTERN.findall(line)
            if amounts:
                try:
                    total_amount = float(amounts[-1].replace(",", ""))
                except ValueError:
                    pass
                if total_amount is not None:
                    break

    currency = None
    code_match = re.search(r"\b(USD|INR|EUR|GBP|CAD|AUD)\b", text, re.IGNORECASE)
    if code_match:
        currency = code_match.group(1).upper()
    else:
        for symbol, code in (("₹", "INR"), ("€", "EUR"), ("£", "GBP"), ("$", "USD")):
            if symbol in text:
                currency = code
                break

    ignored_line = re.compile(r"^(invoice|bill|tax invoice|date|due date|page\s+\d+|www\.|http)", re.IGNORECASE)
    counterparty = next(
        (line.strip()[:200] for line in text.splitlines() if len(line.strip()) > 2 and not ignored_line.search(line.strip())),
        None,
    )
    fields = {
        "counterparty": counterparty,
        "reference_number": invoice_match.group(1) if invoice_match else None,
        "issue_date": issue_date,
        "due_date": due_date,
        "currency": currency,
        "total_amount": total_amount,
    }

    notes = ["Extracted values are suggestions. Review them against the original document before relying on them."]
    if not fields["reference_number"]:
        notes.append("Invoice or bill number was not confidently identified.")
    if not issue_date:
        notes.append("Issue date was not confidently identified.")
    if total_amount is None:
        notes.append("Total amount was not confidently identified.")
    if not counterparty:
        notes.append("Supplier or customer name was not confidently identified.")
    if currency is None:
        notes.append("Currency was not identified; set it during review.")

    confidence = 0.25
    confidence += 0.15 if counterparty else 0
    confidence += 0.20 if fields["reference_number"] else 0
    confidence += 0.15 if issue_date else 0
    confidence += 0.25 if total_amount is not None else 0
    return {**fields, "extraction_confidence": round(confidence, 2)}, notes
