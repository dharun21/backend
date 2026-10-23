"""Second finance knowledge layer: due-date analysis of confirmed open records.

These are management views and conditional scenarios, not financial statements,
cash forecasts, credit-loss estimates, or model-weight training.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select

from app.models.finance import FinanceWorkspace
from app.models.records import FinanceRecord
from app.services.finance_learning import FinanceLearningAnswer, IFRS_SME_AGE_ANALYSIS

_MONEY = Decimal("0.01")
_BUCKETS = ("not_due", "due_today", "1_30", "31_60", "61_90", "91_plus", "no_due_date")
_BUCKET_LABELS = {
    "not_due": "not due yet", "due_today": "due today", "1_30": "1-30 days overdue",
    "31_60": "31-60 days overdue", "61_90": "61-90 days overdue",
    "91_plus": "91+ days overdue", "no_due_date": "due date missing",
}


def _money(value: Decimal, currency: str) -> str:
    return f"{currency} {value.quantize(_MONEY, rounding=ROUND_HALF_UP):,.2f}"


def _scope(question: str) -> tuple[str, str] | None:
    invoice = bool(re.search(r"\b(?:invoices?|receivables?|customers?|collect(?:ions?)?)\b", question))
    bill = bool(re.search(r"\b(?:bills?|payables?|suppliers?|vendors?)\b", question))
    if invoice and bill:
        return "both", "customer invoices and supplier bills"
    if not invoice and not bill:
        return None
    return ("invoice", "customer invoices") if invoice else ("expense", "supplier bills")


def _open_records(db, user_id: str, record_type: str, workspace_currency: str) -> list[tuple[FinanceRecord, str, Decimal]]:
    records = db.scalars(
        select(FinanceRecord)
        .join(FinanceWorkspace, FinanceRecord.workspace_id == FinanceWorkspace.id)
        .where(
            FinanceWorkspace.owner_id == user_id,
            FinanceRecord.status == "confirmed",
            FinanceRecord.record_type == record_type,
        )
        .order_by(FinanceRecord.due_date.asc(), FinanceRecord.id.asc())
    ).all()
    return [
        (record, (record.currency or workspace_currency).upper(), record.balance_due)
        for record in records
        if record.balance_due is not None and record.balance_due > 0
    ]


def _reference(record: FinanceRecord) -> str:
    return record.reference_number or record.counterparty or record.id


def _bucket(due_date: date | None, as_of: date) -> str:
    if due_date is None:
        return "no_due_date"
    days = (as_of - due_date).days
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


def _aging(rows: list[tuple[FinanceRecord, str, Decimal]], label: str, as_of: date) -> FinanceLearningAnswer:
    if not rows:
        return FinanceLearningAnswer(f"I found no confirmed {label} with a remaining balance to age.", intent="finance_aging")
    amounts: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: Decimal(0)))
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for record, currency, balance in rows:
        bucket = _bucket(record.due_date, as_of)
        amounts[currency][bucket] += balance
        counts[currency][bucket] += 1
    parts = []
    figures = []
    for currency in sorted(amounts):
        sections = []
        for bucket in _BUCKETS:
            if counts[currency][bucket]:
                value = _money(amounts[currency][bucket], currency)
                sections.append(f"{_BUCKET_LABELS[bucket]}: {value} ({counts[currency][bucket]} record(s))")
                figures.append({"label": f"{currency} {_BUCKET_LABELS[bucket]}", "value": value, "source": "confirmed remaining balances, grouped by due date"})
        parts.append(f"{currency}: " + "; ".join(sections))
    record_figures = [
        {"label": _reference(record), "value": _money(balance, currency),
         "source": f"confirmed record {record.id}; due {record.due_date.isoformat() if record.due_date else 'date missing'}; {_BUCKET_LABELS[_bucket(record.due_date, as_of)]}"}
        for record, currency, balance in rows[:20]
    ]
    return FinanceLearningAnswer(
        f"Due-date aging of {label} as of {as_of.isoformat()}: " + " | ".join(parts) + ". "
        "This groups remaining balances, not original invoice or bill amounts. A missing due date is kept separate.",
        intent="finance_aging", figures=tuple(figures + record_figures), sources=(IFRS_SME_AGE_ANALYSIS,),
        assumptions=("Buckets use calendar days past the saved due date; today is not overdue. Records without a confirmed total are excluded. This is a management view, not a credit-loss estimate or a formal financial statement.",
                     f"The totals include all {len(rows)} eligible record(s); supporting figures list the first {min(len(rows), 20)} individual record(s)."),
    )


def _due_horizon(rows: list[tuple[FinanceRecord, str, Decimal]], label: str, as_of: date, days: int) -> FinanceLearningAnswer:
    if not 1 <= days <= 365:
        return FinanceLearningAnswer("Choose a due-date window from 1 to 365 days.", intent="finance_due_horizon", needs_more_info=True)
    through = as_of + timedelta(days=days)
    selected = [(record, currency, balance) for record, currency, balance in rows if record.due_date and as_of <= record.due_date <= through]
    if not selected:
        return FinanceLearningAnswer(
            f"I found no confirmed {label} with a remaining balance due from {as_of.isoformat()} through {through.isoformat()}. "
            "Overdue and undated records are outside this window.", intent="finance_due_horizon",
        )
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    for _, currency, balance in selected:
        totals[currency] += balance
    figures = tuple({
        "label": _reference(record), "value": _money(balance, currency),
        "source": f"confirmed record {record.id}; due {record.due_date.isoformat()}"
    } for record, currency, balance in selected[:20])
    summary = ", ".join(_money(amount, currency) for currency, amount in sorted(totals.items()))
    return FinanceLearningAnswer(
        f"{len(selected)} confirmed {label} with a remaining balance are due from {as_of.isoformat()} through {through.isoformat()}: {summary}. "
        "Overdue and undated records are excluded. Due dates do not guarantee payment dates.",
        intent="finance_due_horizon", figures=figures,
        assumptions=("The window includes today and its ending date. Records without a confirmed total are excluded. Amounts reflect user-recorded cumulative payments and are not bank reconciled.",
                     f"The total includes all {len(selected)} eligible record(s); supporting figures list the first {min(len(selected), 20)} individual record(s)."),
    )


def _concentration(rows: list[tuple[FinanceRecord, str, Decimal]], label: str) -> FinanceLearningAnswer:
    grouped: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: Decimal(0)))
    names: dict[tuple[str, str], str] = {}
    record_ids: dict[tuple[str, str], list[str]] = defaultdict(list)
    skipped = 0
    for record, currency, balance in rows:
        name = (record.counterparty or "").strip()
        if not name:
            skipped += 1
            continue
        key = name.casefold()
        grouped[currency][key] += balance
        names[(currency, key)] = name
        record_ids[(currency, key)].append(record.id)
    if not grouped:
        return FinanceLearningAnswer(f"I found no confirmed open {label} with an identified counterparty.", intent="finance_concentration")
    parts = []
    figures = []
    for currency in sorted(grouped):
        total = sum(grouped[currency].values(), Decimal(0))
        leaders = sorted(grouped[currency].items(), key=lambda row: (-row[1], row[0]))[:3]
        formatted = []
        for key, balance in leaders:
            share = (balance / total * 100).quantize(_MONEY, rounding=ROUND_HALF_UP)
            name = names[(currency, key)]
            formatted.append(f"{name}: {_money(balance, currency)} ({share}% of identified open balance)")
            ids = record_ids[(currency, key)]
            more = f"; {len(ids) - 20} more" if len(ids) > 20 else ""
            figures.append({"label": name, "value": _money(balance, currency),
                            "source": f"confirmed open {label}; {currency}; share {share}%; record IDs: {', '.join(ids[:20])}{more}"})
        parts.append(f"{currency}: " + "; ".join(formatted))
    return FinanceLearningAnswer(
        f"Largest open {label} by counterparty: " + " | ".join(parts) + ". "
        f"{skipped} open record(s) without a counterparty were excluded from the shares. "
        "Concentration describes exposure, not the chance of collection or default.",
        intent="finance_concentration", figures=tuple(figures),
        assumptions=("Shares use identified counterparties and remaining balances within each currency; names are matched case-insensitively. Records without a confirmed total are excluded.",),
    )


def _percentage_scenario(rows: list[tuple[FinanceRecord, str, Decimal]], label: str, record_type: str,
                         as_of: date, percentage: Decimal, overdue_only: bool) -> FinanceLearningAnswer:
    if percentage < 0 or percentage > 100:
        return FinanceLearningAnswer("Give a percentage from 0% to 100% for this scenario.", intent="finance_scenario", needs_more_info=True)
    selected = [(record, currency, balance) for record, currency, balance in rows
                if not overdue_only or (record.due_date is not None and record.due_date < as_of)]
    if not selected:
        group = "overdue" if overdue_only else "open"
        return FinanceLearningAnswer(f"I found no confirmed {group} {label} with a remaining balance for this scenario.", intent="finance_scenario")
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    figures = []
    for record, currency, balance in selected:
        scenario_amount = (balance * percentage / 100).quantize(_MONEY, rounding=ROUND_HALF_UP)
        totals[currency] += scenario_amount
        if len(figures) < 20:
            figures.append({"label": _reference(record), "value": _money(scenario_amount, currency),
                            "source": f"{percentage}% of remaining {_money(balance, currency)} from confirmed record {record.id}"})
    direction = "possible customer receipts" if record_type == "invoice" else "possible supplier payments"
    group = "overdue" if overdue_only else "open"
    summary = ", ".join(_money(amount, currency) for currency, amount in sorted(totals.items()))
    return FinanceLearningAnswer(
        f"If {percentage}% of the remaining balances on {len(selected)} confirmed {group} {label} are paid next month, "
        f"{direction} would be {summary}. This is a conditional scenario, not a forecast or bank cash balance.",
        intent="finance_scenario", figures=tuple(figures),
        assumptions=("The same percentage is applied to each selected remaining balance, rounded to cents per record. "
                     "Records without a confirmed total are excluded. Actual payment timing, disputes, fees, and other cash movements are unknown.",
                     f"The scenario includes all {len(selected)} eligible record(s); supporting figures list the first {min(len(selected), 20)} individual record(s)."),
    )


def answer_advanced_finance_workspace(db, user_id: str, question: str, currency: str,
                                      as_of: date | None = None) -> FinanceLearningAnswer | None:
    """Answer explicit advanced questions from this user's confirmed records only."""
    lower = question.lower()
    scope = _scope(lower)
    if scope is None:
        return None
    if re.match(r"^(?:what is|what are|define|explain)\b", lower.strip()) and not re.search(r"\b(?:my|our|workspace|records)\b", lower):
        return None
    record_type, label = scope
    scenario = re.search(r"\b(\d{1,3}(?:\.\d+)?)\s*%", lower)
    scenario_requested = bool(scenario and re.search(r"\b(?:what if|scenario|if)\b", lower)
                              and re.search(r"\b(?:paid|pay|collected|collect|received|receive)\b", lower)
                              and "next month" in lower)
    aging_requested = bool(re.search(r"\b(?:age|aging|ageing|aged|age buckets?)\b", lower))
    concentration_requested = bool(re.search(r"\bconcentration\b", lower) or (
        re.search(r"\b(?:largest|biggest|top customers?|top suppliers?|top vendors?|exposure)\b", lower)
        and re.search(r"\b(?:open|outstanding|unpaid|overdue|balance|receivables?|payables?)\b", lower)
    ))
    horizon = re.search(r"\b(?:next|within)\s+(\d{1,3})\s+days?\b", lower)
    horizon_requested = bool(horizon and re.search(r"\bdue\b", lower))
    if not any((scenario_requested, aging_requested, concentration_requested, horizon_requested)):
        return None
    today = as_of or date.today()
    if record_type == "both":
        if aging_requested:
            invoices = _aging(_open_records(db, user_id, "invoice", currency), "customer invoices", today)
            bills = _aging(_open_records(db, user_id, "expense", currency), "supplier bills", today)
            return FinanceLearningAnswer(
                invoices.reply + "\n" + bills.reply,
                intent="finance_aging", figures=invoices.figures + bills.figures,
                sources=(IFRS_SME_AGE_ANALYSIS,),
                assumptions=invoices.assumptions or bills.assumptions,
            )
        return FinanceLearningAnswer(
            "Ask about customer invoices and supplier bills separately for this view so I can label collections and obligations clearly.",
            intent="finance_data_limit", needs_more_info=True,
        )
    rows = _open_records(db, user_id, record_type, currency)
    if scenario_requested and scenario:
        if not re.search(r"\b(?:all|every|open|outstanding|unpaid|overdue)\b", lower):
            return FinanceLearningAnswer(
                "Which confirmed records should this percentage cover? Say 'all open invoices', 'overdue invoices', 'all open bills', or 'overdue bills'.",
                intent="finance_scenario", needs_more_info=True,
            )
        return _percentage_scenario(rows, label, record_type, today, Decimal(scenario.group(1)), "overdue" in lower)
    if aging_requested:
        return _aging(rows, label, today)
    if concentration_requested:
        return _concentration(rows, label)
    if horizon_requested and horizon:
        return _due_horizon(rows, label, today, int(horizon.group(1)))
    return None
