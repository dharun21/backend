import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from pypdf import PdfReader

MAX_SECTIONS = 50
INVOICE_NUMBER_PATTERN = re.compile(
    r"\b(?:(?:tax|sales|purchase)\s+)?(?:invoice|inv|bill)"
    r"(?:\s*/\s*(?:tax\s*)?(?:invoice|inv))?\s*"
    r"(?:number|no\.?|#|id)\s*[:#-]?\s*"
    r"([A-Z0-9][A-Z0-9/_-]{1,})",
    re.IGNORECASE,
)
STATEMENT_PATTERN = re.compile(
    r"\b(?:bank\s+statement|loan\s+statement|account\s+statement|statement\s+of\s+account|"
    r"transaction\s+history|credit\s+card\s+statement)\b",
    re.IGNORECASE,
)
DATE_VALUE = (
    r"\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|"
    r"[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}"
)
TOTAL_LABEL_PATTERN = re.compile(
    r"\b(grand\s+total|gross\s+total|invoice\s+total|total\s+payable|"
    r"balance\s+payable|total\s+due|amount\s+due|total\s+amount|balance\s+due|total)\b\s*[:#-]?\s*"
    r"(?:INR|USD|EUR|GBP|CAD|AUD|₹|€|£|\$)?\s*"
    r"(-?\d[\d,]*(?:\.\d{1,2})?)",
    re.IGNORECASE,
)


@dataclass
class PdfSection:
    reference_hint: str | None
    start_page: int
    end_page: int
    text: str


@dataclass
class SplitResult:
    sections: list[PdfSection] = field(default_factory=list)
    page_count: int = 0
    text_found: bool = True
    is_statement: bool = False
    ambiguous: bool = False
    reason: str | None = None


def _read_pages(pdf_path: Path) -> list[str]:
    reader = PdfReader(str(pdf_path), strict=False)
    if reader.is_encrypted:
        raise ValueError("Encrypted PDFs cannot be processed yet.")
    if len(reader.pages) > 100:
        raise ValueError("PDFs over 100 pages need to be split before processing.")
    return [page.extract_text() or "" for page in reader.pages]


def _date_from_label(text: str, label_pattern: str) -> date | None:
    match = re.search(rf"(?:{label_pattern})\s*[:#-]?\s*({DATE_VALUE})", text, re.IGNORECASE)
    if not match:
        return None
    value = match.group(1).replace(",", "")
    for date_format in (
        "%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y",
        "%d-%m-%Y", "%b %d %Y", "%B %d %Y", "%d %b %Y", "%d %B %Y",
    ):
        try:
            return datetime.strptime(value, date_format).date()
        except ValueError:
            continue
    return None


def _reference_sections(pages: list[str]) -> list[PdfSection]:
    """Split a PDF into invoice/bill records and clearly labeled statement pages."""
    full_text = "\n".join(pages).strip()
    matches = list(INVOICE_NUMBER_PATTERN.finditer(full_text))
    page_starts: list[int] = []
    cursor = 0
    for page in pages:
        page_starts.append(cursor)
        cursor += len(page) + 1

    # Repeated references are commonly page headers for a continued invoice.
    starts: list[tuple[int, str]] = []
    seen: set[str] = set()
    last_reference_page = None
    for match in matches:
        reference = match.group(1).strip()
        normalized = reference.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        page_index = max((i for i, page_start in enumerate(page_starts) if page_start <= match.start()), default=0)
        # Preserve the seller/title/customer header before the first reference
        # on a page. A second invoice on that same page starts at its own label.
        section_start = match.start() if page_index == last_reference_page else page_starts[page_index]
        starts.append((section_start, reference))
        last_reference_page = page_index

    # Keep obvious statements from mixed PDFs as their own reviewable records.
    # A statement page without an invoice/bill reference can then be set aside
    # explicitly and remains excluded from financial totals.
    for page_index, page in enumerate(pages):
        if STATEMENT_PATTERN.search(page) and not INVOICE_NUMBER_PATTERN.search(page):
            starts.append((page_starts[page_index], ""))
    starts.sort(key=lambda item: item[0])
    if not starts:
        return [PdfSection(None, 1, max(len(pages), 1), full_text)] if full_text else []

    sections: list[PdfSection] = []
    section_count = min(len(starts), MAX_SECTIONS)
    for index, (start, reference) in enumerate(starts[:MAX_SECTIONS]):
        end = starts[index + 1][0] if index + 1 < section_count else len(full_text)
        section_text = full_text[start:end].strip()
        start_page = full_text[:start].count("\n") + 1
        end_page = max(start_page, full_text[:end].count("\n") + 1)
        sections.append(PdfSection(reference or None, start_page, end_page, section_text))
    return sections


def split_pdf_sections(pdf_path: Path) -> SplitResult:
    pages = _read_pages(pdf_path)
    full_text = "\n".join(pages).strip()
    if not full_text:
        return SplitResult(page_count=len(pages), text_found=False)
    sections = _reference_sections(pages)
    return SplitResult(sections=sections, page_count=len(pages))


_PARTY_STOP = re.compile(
    r"\b(?:GSTIN|GST\s*(?:NO|NUMBER)|PAN|ADDRESS|INVOICE(?:\s+(?:NO|NUMBER|DATE))?|"
    r"BILL(?:\s+(?:NO|NUMBER|DATE))?|DATE|TOTAL|SUBTOTAL|TAX|AMOUNT|ACCOUNT\s+STATUS|"
    r"PAST\s+DUE|PAYMENT\s+(?:TERMS|DUE)|PHONE|EMAIL|CONTACT)\b",
    re.IGNORECASE,
)
_GENERIC_PARTIES = {
    "bill", "invoice", "customer", "supplier", "vendor", "account status",
    "past due", "customer or supplier", "billed to", "sold to", "bill to",
}


def _counterparty_from_text(text: str, record_type: str | None = None, business_name: str | None = None) -> str | None:
    """Extract a party only from an explicit label; never treat arbitrary fields as names."""
    normalized_text = re.sub(r"\s+", " ", text)
    business = re.sub(r"\s+", " ", (business_name or "")).strip().casefold()
    if record_type == "expense" and business:
        billed_to = re.search(
            r"\bbilled\s+to\b\s*(?:\([^)]*\))?\s*[:#-]?\s*([^,;]{2,160})",
            normalized_text,
            re.IGNORECASE,
        )
        if billed_to:
            billed_party = _PARTY_STOP.split(billed_to.group(1), maxsplit=1)[0]
            billed_party = re.split(r"\s+\d", billed_party, maxsplit=1)[0].strip(" ,:-").casefold()
            if business in billed_party:
                # On a supplier bill, the business is the billed-to recipient;
                # the supplier is the issuer at the top of the document.
                for line in text.splitlines():
                    issuer = re.sub(r"\s+", " ", line).strip(" ,:-")
                    if issuer and issuer.casefold() not in _GENERIC_PARTIES and business not in issuer.casefold():
                        return issuer[:200]
    label_patterns = (
        r"\b(?:billed\s+to|sold\s+to|bill\s+to)\b\s*(?:\([^)]*\))?\s*[:#-]?\s*([^,;]{2,160})",
        r"\b(?:customer|supplier|vendor)(?:\s+name)?\s*[:#-]\s*([^,;]{2,160})",
    )
    for pattern in label_patterns:
        for labeled in re.finditer(pattern, normalized_text, re.IGNORECASE):
            candidate = _PARTY_STOP.split(labeled.group(1), maxsplit=1)[0]
            candidate = re.split(r"\s+\d", candidate, maxsplit=1)[0]
            candidate = re.sub(r"\s+", " ", candidate).strip(" ,:-")
            if candidate.casefold() in _GENERIC_PARTIES or len(candidate) < 3:
                continue
            if re.fullmatch(r"[A-Z0-9-]+", candidate, re.IGNORECASE) and candidate.casefold() in _GENERIC_PARTIES:
                continue
            return candidate[:200]
    return None


def _classification_suggestion(text: str, reference: str | None, business_name: str | None) -> tuple[str, str] | None:
    """Return only high-evidence invoice direction suggestions; otherwise defer to review."""
    normalized = re.sub(r"\s+", " ", text).casefold()
    reference_text = (reference or "").casefold()
    business = re.sub(r"\s+", " ", (business_name or "")).strip().casefold()

    expense_cues = (
        (r"\b(?:supplier|vendor)\s+(?:bill|invoice)\b", "document labels it as a supplier/vendor bill"),
        (r"\b(?:purchase|accounts payable)\s+invoice\b|\bamount payable\b", "document identifies a purchase/payable"),
        (r"\bbill\s+from\s+(?:supplier|vendor)\b", "document says the bill is from a supplier/vendor"),
    )
    invoice_cues = (
        (r"\b(?:customer|sales)\s+invoice\b", "document labels it as a customer/sales invoice"),
        (r"\baccounts receivable\b|\bamount receivable\b", "document identifies an amount receivable"),
    )
    for pattern, reason in expense_cues:
        if re.search(pattern, normalized):
            return "expense", reason
    for pattern, reason in invoice_cues:
        if re.search(pattern, normalized):
            return "invoice", reason

    # A business named as the recipient of a bill is the payer; a business named
    # as the sold-to party is the seller. This is high confidence only on an exact
    # normalized business-name match from the workspace profile.
    if business and len(business) >= 4:
        billed_to = re.search(r"\bbilled\s+to\b(?:\s*\([^)]*\))?\s*[:#-]?\s*([^\r\n]{3,160})", normalized)
        sold_to = re.search(r"\bsold\s+to\b(?:\s*\([^)]*\))?\s*[:#-]?\s*([^\r\n]{3,160})", normalized)
        for match in (billed_to, sold_to):
            if match:
                party = re.split(r"\b(?:gstin|pan|address|invoice|date|total|amount)\b", match.group(1), maxsplit=1)[0]
                party = re.sub(r"\s+", " ", party).strip(" ,:-")
                if business in party:
                    if match is billed_to:
                        return "expense", "the workspace business is named as the billed-to recipient"
                    return "invoice", "the workspace business is named as the sold-by party"

        # On a tax invoice, the workspace business in the document header is
        # the seller; a separately named billed-to party is the customer.
        header = normalized[:400]
        billed_party = re.search(
            r"\bbilled\s+to\b(?:\s*\([^)]*\))?\s*[:#-]?\s*([^\r\n]{3,160})",
            normalized,
        )
        if business in header and re.search(r"\b(?:tax\s+)?invoice\b", header) and billed_party:
            party = re.split(r"\b(?:gstin|pan|address|invoice|date|total|amount)\b", billed_party.group(1), maxsplit=1)[0]
            if business not in re.sub(r"\s+", " ", party).strip(" ,:-"):
                return "invoice", "the workspace business is the invoice issuer and another party is billed"

    # A conventional supplier bill reference is useful evidence only when paired
    # with a bill/invoice document type in the extracted text.
    if re.search(r"\b(?:supplier\s+bill|vendor\s+bill|bill)\b", normalized) and re.match(r"(?:sup|vendor)[-_]", reference_text):
        return "expense", "the reference and document wording identify a supplier bill"
    return None


def _extract_fields(text: str, reference_hint: str | None = None, business_name: str | None = None) -> tuple[dict, list[str]]:
    is_statement = bool(STATEMENT_PATTERN.search(text))
    reference_match = INVOICE_NUMBER_PATTERN.search(text)
    reference = reference_hint or (reference_match.group(1).strip() if reference_match else None)
    due_date = _date_from_label(text, r"due date|payment due")
    issue_date = _date_from_label(text, r"invoice date|bill date|date of invoice|issued on")

    total_amount = None
    for match in TOTAL_LABEL_PATTERN.finditer(text) if not is_statement else []:
        label = match.group(1).casefold()
        if label == "total" and re.search(
            r"\b(?:subtotal|quantity|tax|paid)\b",
            text[max(0, match.start() - 24):match.start()],
            re.IGNORECASE,
        ):
            continue
        try:
            total_amount = float(match.group(2).replace(",", ""))
        except ValueError:
            continue
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

    classification = _classification_suggestion(text, reference, business_name)
    counterparty = _counterparty_from_text(text, classification[0] if classification else None, business_name)
    fields = {
        "counterparty": counterparty,
        "reference_number": reference,
        "issue_date": issue_date,
        "due_date": due_date,
        "currency": currency,
        "total_amount": total_amount,
    }
    notes = ["Extracted values are suggestions. Review them against the original document before relying on them."]
    if not reference:
        notes.append("Invoice or bill number was not confidently identified.")
    if not issue_date:
        notes.append("Issue date was not confidently identified.")
    if total_amount is None:
        notes.append("Total amount was not confidently identified.")
    if not counterparty:
        notes.append("Supplier or customer name was not confidently identified.")
    if not currency:
        notes.append("Currency was not identified; set it during review.")
    if is_statement:
        notes.append("This looks like a bank/account statement, not an invoice or supplier bill. It is excluded from finance totals.")
    elif classification:
        notes.append(f"Suggested as {'supplier bill/expense' if classification[0] == 'expense' else 'customer invoice'} because {classification[1]}; verify before confirming.")
    else:
        notes.append("The document does not clearly show whether this is money owed to you or money you owe; choose the classification during review.")

    confidence = 0.25 + (0.15 if counterparty else 0) + (0.20 if reference else 0)
    confidence += (0.15 if issue_date else 0) + (0.25 if total_amount is not None else 0)
    result = {**fields, "extraction_confidence": round(confidence, 2)}
    if is_statement:
        result["status_suggestion"] = "unsupported"
    elif classification:
        result["record_type"] = classification[0]
    return result, notes


def extract_invoice_records(pdf_path: Path, business_name: str | None = None) -> list[tuple[dict, list[str]]]:
    """Return one independently extracted finance record per distinct invoice reference."""
    pages = _read_pages(pdf_path)
    full_text = "\n".join(pages).strip()
    if not full_text:
        return [({}, ["No selectable text was found. This may be a scanned PDF; OCR is not configured yet."])]

    sections = _reference_sections(pages)
    references = {section.reference_hint.casefold() for section in sections if section.reference_hint}
    if len(sections) > 1:
        return [_extract_fields(section.text, section.reference_hint, business_name) for section in sections[:MAX_SECTIONS]]

    if sections and not references and STATEMENT_PATTERN.search(sections[0].text):
        return [_extract_fields(sections[0].text, business_name=business_name)]

    if not references:
        total_labels = list(TOTAL_LABEL_PATTERN.finditer(full_text))
        if len(total_labels) > 1:
            fields, notes = _extract_fields(full_text, business_name=business_name)
            fields["total_amount"] = None
            fields["extraction_confidence"] = min(fields["extraction_confidence"], 0.5)
            notes.append(
                "Multiple totals were found without distinct invoice numbers. Amount left blank to avoid combining documents; split or review this PDF manually."
            )
            return [(fields, notes)]
        return [_extract_fields(full_text, business_name=business_name)]

    return [_extract_fields(full_text, sections[0].reference_hint, business_name)]


def extract_invoice_fields(pdf_path: Path, business_name: str | None = None) -> tuple[dict, list[str]]:
    """Backward-compatible single-record extractor."""
    records = extract_invoice_records(pdf_path, business_name)
    return records[0] if records else ({}, ["No invoice text could be extracted."])


def _parse_statement_date(value: str) -> date | None:
    value = value.strip().replace(",", "")
    for date_format in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, date_format).date()
        except ValueError:
            continue
    return None


def extract_statement_data(pdf_path: Path) -> dict | None:
    """Extract statement metadata and transaction rows for future use.

    These rows are stored as source_type=statement and must not be included in
    current invoice/expense calculations.
    """
    pages = _read_pages(pdf_path)
    candidates = [page for page in pages if STATEMENT_PATTERN.search(page)]
    if not candidates:
        return None
    text = "\n".join(candidates)
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]

    account_match = re.search(r"\b(?:account\s*(?:no\.?|number)|a/c\s*(?:no\.?|number))\s*[:#-]?\s*([A-Z0-9 -]{5,})", text, re.IGNORECASE)
    account_number = None
    if account_match:
        account_number = re.sub(r"\s+", "", account_match.group(1)).rstrip(".,;")
        account_number = re.split(r"\b(?:statement|period|currency|date)\b", account_number, maxsplit=1, flags=re.IGNORECASE)[0]

    holder = None
    holder_match = re.search(r"ACCOUNT\s+HOLDER(?:\s+DETAILS)?\s*[:\n-]*\s*([^\n]+)", text, re.IGNORECASE)
    if holder_match:
        candidate = holder_match.group(1).strip(" :-")
        if candidate and not re.search(r"\b(?:address|name|gstin|pan)\b", candidate, re.IGNORECASE):
            holder = candidate[:200]
        else:
            after = text[holder_match.end():].splitlines()
            for line in after:
                candidate = line.strip(" :-")
                if candidate and not re.search(r"\b(?:address|gstin|pan)\b", candidate, re.IGNORECASE):
                    holder = candidate[:200]
                    break

    institution = None
    for line in lines:
        if re.search(r"\b(?:bank|credit union|financial institution)\b", line, re.IGNORECASE) and not re.search(r"\b(?:account|statement|division|branch|ifsc|web)\b", line, re.IGNORECASE):
            institution = line[:200]
            break
    if institution is None:
        for line in lines:
            if re.search(r"\b(?:bank|credit union)\b", line, re.IGNORECASE):
                institution = line[:200]
                break

    period_match = re.search(
        r"statement\s+period\s*[:#-]?\s*(\d{1,2}-[A-Za-z]{3,9}-\d{4}|\d{4}-\d{2}-\d{2})\s+(?:to|through|-)\s+(\d{1,2}-[A-Za-z]{3,9}-\d{4}|\d{4}-\d{2}-\d{2})",
        text,
        re.IGNORECASE,
    )
    period_start = _parse_statement_date(period_match.group(1)) if period_match else None
    period_end = _parse_statement_date(period_match.group(2)) if period_match else None
    statement_date = _date_from_label(text, r"statement\s+date") or period_end
    currency_match = re.search(r"\bcurrency\s*[:#-]?\s*([A-Z]{3})\b|\b(INR|USD|EUR|GBP|CAD|AUD)\b", text, re.IGNORECASE)
    currency = next((part.upper() for part in currency_match.groups() if part), None) if currency_match else None
    ref_match = re.search(r"\b(?:doc(?:ument)?\s+ref(?:erence)?\s*(?:id|no\.?)?|reference\s+no\.?)\s*[:#-]?\s*([A-Z0-9/_-]+)", text, re.IGNORECASE)

    normalized = re.sub(r"(\d{1,2}-[A-Za-z]{3,9})-\s*\n\s*(\d{4})", r"\1-\2", text)
    transaction_header = re.search(r"\b(?:TXN|TRANSACTION)\s+DATE\b", normalized, re.IGNORECASE)
    transaction_text = normalized[transaction_header.end():] if transaction_header else ""
    row_dates = list(re.finditer(r"\b(\d{1,2}-[A-Za-z]{3,9}-\d{4}|\d{4}-\d{2}-\d{2})\b", transaction_text))
    amount_pattern = re.compile(r"(?<![A-Za-z0-9])(?:-?\d[\d,]*\.\d{2}|-)(?![A-Za-z0-9])")
    transactions = []
    for index, row_match in enumerate(row_dates):
        row_end = row_dates[index + 1].start() if index + 1 < len(row_dates) else len(transaction_text)
        row = transaction_text[row_match.end():row_end].strip(" \t\r\n|-")
        amounts = list(amount_pattern.finditer(row))
        if not amounts:
            continue
        tail = amounts[-3:]
        description = row[:tail[0].start()].strip(" \t|:-")
        if len(tail) < 3:
            # Incomplete transaction rows are retained with whatever fields were
            # confidently parsed, instead of inventing debit/credit values.
            values = [None] * (3 - len(tail)) + [m.group(0) for m in tail]
        else:
            values = [m.group(0) for m in tail]
        def amount_value(value):
            return None if value in (None, "-") else str(round(float(value.replace(",", "")), 2))
        debit, credit, balance = (amount_value(value) for value in values)
        date_value = _parse_statement_date(row_match.group(1))
        if date_value is None:
            try:
                date_value = datetime.strptime(row_match.group(1), "%Y-%m-%d").date()
            except ValueError:
                pass
        if date_value is None:
            continue
        direction = "opening_balance" if re.search(r"opening\s+balance", description, re.IGNORECASE) else "debit" if debit is not None else "credit" if credit is not None else "unknown"
        transactions.append({
            "source_type": "statement",
            "transaction_type": direction,
            "transaction_date": date_value.isoformat(),
            "description": description[:500],
            "reference": None,
            "debit": debit,
            "credit": credit,
            "balance": balance,
            "currency": currency,
        })
    return {
        "source_type": "statement",
        "institution_name": institution,
        "account_holder": holder,
        "account_number": account_number,
        "statement_date": statement_date,
        "period_start": period_start,
        "period_end": period_end,
        "currency": currency,
        "document_reference": ref_match.group(1)[:120] if ref_match else None,
        "transactions": transactions,
    }
