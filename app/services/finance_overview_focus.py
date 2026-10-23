"""Confirmed-record overview data shared by retail and corporate workspaces."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from app.models.records import FinanceRecord

BUCKETS = ("not_due", "due_today", "1_30", "31_60", "61_90", "91_plus", "no_due_date")
BUCKET_LABELS = {
    "not_due": "Not due", "due_today": "Due today", "1_30": "1–30 days overdue",
    "31_60": "31–60 days overdue", "61_90": "61–90 days overdue",
    "91_plus": "91+ days overdue", "no_due_date": "No due date",
}

RETAIL_GAPS = (
    {"title": "Stock and product margins", "detail": "Needs SKU sales, inventory at cost, and cost of goods sold. Supplier bills alone cannot establish product margin."},
    {"title": "POS and card settlements", "detail": "Needs daily POS totals, processor fees, settlement dates, and reconciled bank receipts."},
    {"title": "Returns and discounts", "detail": "Needs linked original sales, credit notes, refunds, discounts, and stock returns."},
)
CORPORATE_GAPS = (
    {"title": "Budget by department", "detail": "Needs approved budgets and department-tagged actual transactions for the same period."},
    {"title": "Entity and group reporting", "detail": "Needs entity ledgers, ownership and control details, and intercompany eliminations."},
    {"title": "Procurement and close", "detail": "Needs purchase orders, goods receipts, approvals, journals, and reconciliations."},
)


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _bucket(due: date | None, today: date) -> str:
    if due is None:
        return "no_due_date"
    days = (today - due).days
    if days < 0:
        return "not_due"
    if days == 0:
        return "due_today"
    if days <= 30:
        return "1_30"
    if days <= 60:
        return "31_60"
    if days <= 90:
        return "61_90"
    return "91_plus"


def build_finance_focus(records: list[FinanceRecord], currency: str, business_type: str | None,
                        as_of: date | None = None) -> dict:
    """Group open confirmed balances; keep every currency and missing date separate."""
    today = as_of or date.today()
    selected = (business_type or "").strip().lower()
    kind = selected if selected in {"retail", "corporate"} else "general"
    grouped: dict[str, dict[str, list[tuple[FinanceRecord, Decimal]]]] = defaultdict(lambda: defaultdict(list))
    aging: dict[str, dict[str, dict[str, list[tuple[FinanceRecord, Decimal]]]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    counterparties: dict[str, dict[str, dict[str, dict]]] = defaultdict(lambda: defaultdict(dict))
    missing_names: dict[str, int] = defaultdict(int)
    for record in records:
        if record.status != "confirmed" or record.record_type not in {"invoice", "expense"}:
            continue
        balance = record.balance_due
        if balance is None or balance <= 0:
            continue
        record_currency = (record.currency or currency).upper()
        scope = "invoices" if record.record_type == "invoice" else "bills"
        grouped[scope][record_currency].append((record, balance))
        if record.due_date and record.due_date < today:
            grouped[f"overdue_{scope}"][record_currency].append((record, balance))
        if record.due_date and today <= record.due_date <= today + timedelta(days=7):
            grouped[f"due_soon_{scope}"][record_currency].append((record, balance))
        aging[scope][record_currency][_bucket(record.due_date, today)].append((record, balance))
        name = (record.counterparty or "").strip()
        if not name:
            missing_names[scope] += 1
            continue
        key = name.casefold()
        entry = counterparties[scope][record_currency].setdefault(key, {"name": name, "amount": Decimal(0), "count": 0})
        entry["amount"] += balance
        entry["count"] += 1

    balances = {}
    for scope in ("invoices", "bills", "overdue_invoices", "overdue_bills", "due_soon_invoices", "due_soon_bills"):
        balances[scope] = [
            {"currency": code, "amount": _money(sum((amount for _, amount in rows), Decimal(0))), "count": len(rows)}
            for code, rows in sorted(grouped[scope].items())
        ]

    aging_result = {}
    for scope in ("invoices", "bills"):
        aging_result[scope] = [
            {"currency": code, "bucket": bucket, "label": BUCKET_LABELS[bucket],
             "amount": _money(sum((amount for _, amount in rows), Decimal(0))), "count": len(rows)}
            for code, buckets in sorted(aging[scope].items())
            for bucket in BUCKETS if (rows := buckets.get(bucket))
        ]

    concentration = {}
    for scope, label in (("invoices", "customers"), ("bills", "suppliers")):
        concentration[label] = []
        for code, entries in sorted(counterparties[scope].items()):
            denominator = sum((entry["amount"] for entry in entries.values()), Decimal(0))
            for entry in sorted(entries.values(), key=lambda item: (-item["amount"], item["name"].casefold()))[:3]:
                concentration[label].append({
                    "name": entry["name"], "currency": code, "amount": _money(entry["amount"]),
                    "share_percent": _money(entry["amount"] / denominator * 100), "record_count": entry["count"],
                })

    return {
        "business_type": kind,
        "as_of": today.isoformat(),
        "balances": balances,
        "aging": aging_result,
        "concentration": concentration,
        "missing_counterparty_counts": {"invoices": missing_names["invoices"], "bills": missing_names["bills"]},
        "missing_data": list(RETAIL_GAPS if kind == "retail" else CORPORATE_GAPS if kind == "corporate" else ()),
        "basis_note": "Open balances use confirmed invoices and bills after user-recorded cumulative payments. Due-date groups use saved due dates; missing dates stay separate. A missing record currency uses the workspace currency; different currencies are never combined. Counterparty shares exclude records with no identified name. No result is bank cash, recognized profit, or a payment forecast.",
    }
