"""Grounded Finance chat answers from the owner's saved payroll records."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.payroll import Employee, EmployeeLeave, EmployeeReimbursement, GovernmentTax, Payslip
from app.services.finance_learning import FinanceLearningAnswer


def _money_by_currency(rows: list[Payslip] | list[EmployeeReimbursement] | list[GovernmentTax], field: str) -> str:
    amounts: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for row in rows:
        amounts[row.currency] += Decimal(getattr(row, field) or 0)
    return "; ".join(f"{currency} {amount:,.2f}" for currency, amount in sorted(amounts.items())) or "no recorded amount"


def _period(question: str) -> tuple[date, date] | None:
    today = date.today()
    if re.search(r"\bthis month\b", question):
        next_month = date(today.year + 1, 1, 1) if today.month == 12 else date(today.year, today.month + 1, 1)
        return date(today.year, today.month, 1), next_month
    if re.search(r"\blast month\b", question):
        end = date(today.year, today.month, 1)
        previous = date(end.year - 1, 12, 1) if end.month == 1 else date(end.year, end.month - 1, 1)
        return previous, end
    return None


def answer_finance_payroll_workspace(db: Session, workspace_id: str | None, question: str) -> FinanceLearningAnswer | None:
    if not workspace_id:
        return None
    lower = question.lower()
    topical = re.search(r"\b(?:employees?|staff|payroll|payslips?|salar(?:y|ies)|wages?|leave|pto|bonus(?:es)?|commissions?|reimburs\w*|expense claims?|employer contributions?|tax(?:es)?|gst|vat|withholding)\b", lower)
    request = re.search(r"\b(?:my|our|which|show|list|total|how much|how many|who|status|balance|balances|this month|last month|unpaid|paid|pending|approved|owe|due)\b", lower)
    if not topical or not request:
        return None

    employees = list(db.scalars(select(Employee).where(Employee.workspace_id == workspace_id).order_by(Employee.full_name)))
    slips = list(db.scalars(select(Payslip).where(Payslip.workspace_id == workspace_id)))
    claims = list(db.scalars(select(EmployeeReimbursement).where(EmployeeReimbursement.workspace_id == workspace_id)))
    period = _period(lower)
    tax_query = bool(re.search(r"\b(?:tax(?:es)?|gst|vat)\b", lower)) and not bool(re.search(r"\b(?:tax withheld|withholding|employee tax|employer contributions?)\b", lower))
    if tax_query:
        taxes = list(db.scalars(select(GovernmentTax).where(GovernmentTax.workspace_id == workspace_id).order_by(GovernmentTax.due_date, GovernmentTax.created_at)))
        confirmed = [tax for tax in taxes if tax.status == "confirmed"]
        paid = [tax for tax in taxes if tax.status == "paid"]
        period_basis = ""
        if period:
            start, end = period
            confirmed = [tax for tax in confirmed if tax.due_date and start <= tax.due_date < end]
            paid = [tax for tax in paid if tax.paid_at and start <= tax.paid_at < end]
            taxes = [tax for tax in taxes if (tax.status == "paid" and tax.paid_at and start <= tax.paid_at < end) or (tax.status != "paid" and tax.due_date and start <= tax.due_date < end)]
            period_basis = f" For {start.isoformat()} to before {end.isoformat()}, unpaid records use the saved due date and paid records use the entered paid date."
        if re.search(r"\b(?:show|list|which)\b", lower) and taxes:
            entries = [f"{tax.tax_type} ({tax.jurisdiction}), {tax.currency} {tax.amount:,.2f}, {tax.status}, due {tax.due_date.isoformat() if tax.due_date else 'unknown'}" for tax in taxes[:10]]
            return FinanceLearningAnswer(
                "Employer-entered government tax records: " + "; ".join(entries) + (f". Showing 10 of {len(taxes)}." if len(taxes) > 10 else ".") + period_basis + " Drafts are not confirmed obligations; paid status is not bank or authority verified.",
                intent="finance_payroll_records",
            )
        return FinanceLearningAnswer(
            f"Saved government tax records: {len(confirmed)} confirmed and not marked paid ({_money_by_currency(confirmed, 'amount')}); {len(paid)} marked paid ({_money_by_currency(paid, 'amount')})." + period_basis + " Drafts are excluded. These are manually entered obligations and payment statuses, not a tax computation, filing, bank reconciliation, or government receipt. Zengen cannot determine whether an unrecorded tax applies.",
            intent="finance_payroll_records", needs_more_info=not bool(confirmed or paid),
        )
    if not employees:
        return FinanceLearningAnswer(
            "There are no employees saved in this Finance workspace yet. Add employees in Payroll on the web, then enter leave, claims, and payslips for record-backed answers.",
            intent="finance_data_limit", needs_more_info=True,
        )

    named = [person for person in employees if (
        re.search(rf"\b{re.escape(person.full_name.lower())}\b", lower)
        or re.search(rf"\b{re.escape(person.employee_code.lower())}\b", lower)
    )]
    if named:
        employee_ids = {person.id for person in named}
        slips = [slip for slip in slips if slip.employee_id in employee_ids]
        claims = [claim for claim in claims if claim.employee_id in employee_ids]

    if re.search(r"\b(?:leave|pto)\b", lower):
        if period and not re.search(r"\b(?:balance|remaining|left|entitlement|allowance)\b", lower):
            start, end = period
            entries = list(db.scalars(select(EmployeeLeave).where(
                EmployeeLeave.workspace_id == workspace_id,
                EmployeeLeave.start_date >= start,
                EmployeeLeave.start_date < end,
            ).order_by(EmployeeLeave.start_date)))
            if named:
                entries = [entry for entry in entries if entry.employee_id in employee_ids]
            approved_paid = sum((entry.days for entry in entries if entry.status == "approved" and entry.leave_type == "paid"), Decimal("0"))
            approved_unpaid = sum((entry.days for entry in entries if entry.status == "approved" and entry.leave_type == "unpaid"), Decimal("0"))
            pending = sum(entry.status == "pending" for entry in entries)
            return FinanceLearningAnswer(
                f"Saved leave entries starting from {start.isoformat()} to before {end.isoformat()}: {approved_paid} approved paid day(s), {approved_unpaid} approved unpaid day(s), and {pending} pending entry/entries. This groups by leave start date and excludes rejected or canceled entries; unpaid days do not automatically change salary.",
                intent="finance_payroll_records",
            )
        year = date.today().year
        selected = named
        if not selected:
            selected = ([person for person in employees if person.active] or employees)[:8]
        lines = []
        for person in selected:
            used = db.scalar(select(func.coalesce(func.sum(EmployeeLeave.days), 0)).where(
                EmployeeLeave.workspace_id == workspace_id,
                EmployeeLeave.employee_id == person.id,
                EmployeeLeave.leave_type == "paid",
                EmployeeLeave.status == "approved",
                EmployeeLeave.start_date >= date(year, 1, 1),
                EmployeeLeave.start_date < date(year + 1, 1, 1),
            )) or Decimal("0")
            remaining = Decimal(person.annual_paid_leave_days or 0) - Decimal(used)
            lines.append(f"{person.full_name}: {remaining} days remaining ({person.annual_paid_leave_days} entered annual allowance - {used} approved paid days)")
        return FinanceLearningAnswer(
            f"Saved paid-leave balances for {year}: " + "; ".join(lines) + (f". Showing {len(selected)} of {len(employees)} employees." if len(selected) < len(employees) else ".") + " This uses calendar-year entries and the allowance entered for each employee; it does not check local entitlement, carryover, or payroll deduction policy.",
            intent="finance_payroll_records",
            assumptions=("Only approved paid leave reduces the entered calendar-year allowance. Pending and unpaid leave are excluded.",),
        )

    if re.search(r"\b(?:reimburs\w*|expense claims?)\b", lower):
        if period:
            start, end = period
            paid_question = bool(re.search(r"\b(?:paid reimbursements|reimbursements paid|paid claims|claims paid)\b", lower)) and not bool(re.search(r"\b(?:unpaid|not paid)\b", lower))
            if paid_question:
                claims = [claim for claim in claims if claim.paid_at and start <= claim.paid_at < end]
            else:
                claims = [claim for claim in claims if start <= claim.expense_date < end]
        pending = [claim for claim in claims if claim.status == "pending"]
        approved = [claim for claim in claims if claim.status == "approved"]
        paid = [claim for claim in claims if claim.status == "paid"]
        return FinanceLearningAnswer(
            f"Employee claims saved in this workspace" + (f" for {period[0].isoformat()} to before {period[1].isoformat()} ({'paid date' if paid_question else 'expense date'})" if period else "") + f": {len(pending)} pending ({_money_by_currency(pending, 'amount')}); {len(approved)} approved but not marked paid ({_money_by_currency(approved, 'amount')}); {len(paid)} marked paid ({_money_by_currency(paid, 'amount')}). Approval and payment statuses are user-entered; receipt contents and bank settlement are not verified.",
            intent="finance_payroll_records",
        )

    if re.search(r"\b(?:how many|list|show|which|who)\b", lower) and re.search(r"\b(?:employees?|staff)\b", lower) and not re.search(r"\b(?:paid|salary|wage|payroll|tax|leave)\b", lower):
        active = [person for person in employees if person.active]
        names = ", ".join(person.full_name for person in active[:10])
        return FinanceLearningAnswer(
            f"This workspace has {len(active)} active employee(s). " + (f"First {min(len(active), 10)}: {names}." if active else "") + " Employee details are saved by your account; this count does not confirm anyone was paid.",
            intent="finance_payroll_records",
        )

    confirmed = [slip for slip in slips if slip.status in {"finalized", "paid"}]
    if re.search(r"\b(?:bonus(?:es)?|commissions?)\b", lower):
        matched = confirmed
        if period:
            start, end = period
            matched = [slip for slip in confirmed if start <= slip.period_end < end]
        return FinanceLearningAnswer(
            f"Finalized payslip bonus/commission entries total {_money_by_currency(matched, 'bonus_pay')} across {len(matched)} payslip(s)" + (f" with pay-period end from {period[0].isoformat()} to before {period[1].isoformat()}" if period else " across saved periods") + ". These are entered amounts; Zengen has no separate commission-eligibility or approval calculation.",
            intent="finance_payroll_records",
        )

    if re.search(r"\b(?:tax withheld|withholding|employee tax|employer contributions?)\b", lower):
        matched = confirmed
        if period:
            start, end = period
            matched = [slip for slip in confirmed if start <= slip.period_end < end]
        return FinanceLearningAnswer(
            f"Across {len(matched)} finalized payslip(s)" + (f" with pay-period end from {period[0].isoformat()} to before {period[1].isoformat()}" if period else "") + f", employee tax entered as withheld totals {_money_by_currency(matched, 'employee_tax')}; employer contributions entered total {_money_by_currency(matched, 'employer_contributions')}. These are entered payroll figures, not a tax assessment, return, or proof of government remittance.",
            intent="finance_payroll_records",
        )

    if re.search(r"\b(?:payroll|payslips?|salar(?:y|ies)|wages?|employee pay|staff pay)\b", lower):
        if re.search(r"\b(?:paid|payment|cash)\b", lower) and not re.search(r"\b(?:unpaid|not paid)\b", lower):
            matched = [slip for slip in slips if slip.status == "paid"]
            if period:
                start, end = period
                matched = [slip for slip in matched if slip.paid_at and start <= slip.paid_at < end]
            return FinanceLearningAnswer(
                f"{len(matched)} payslip(s) are marked paid" + (f" with paid date from {period[0].isoformat()} to before {period[1].isoformat()}" if period else "") + f", with employee payout totals of {_money_by_currency(matched, 'cash_to_employee')}. This includes linked expense payments. The paid dates and payment references are user-entered; Zengen has not reconciled them to a bank statement.",
                intent="finance_payroll_records",
            )
        matched = [slip for slip in slips if slip.status == "finalized"] if re.search(r"\b(?:unpaid|owe|due|pending)\b", lower) else confirmed
        if period:
            start, end = period
            matched = [slip for slip in matched if start <= slip.period_end < end]
        if re.search(r"\b(?:show|list|which)\b", lower) and matched:
            lines = [f"{slip.employee_name} ({slip.employee_code}), period ending {slip.period_end.isoformat()}, {slip.status}: net salary {slip.currency} {slip.net_pay:,.2f}; payout including linked expense payments {slip.currency} {slip.cash_to_employee:,.2f}" for slip in sorted(matched, key=lambda item: item.period_end, reverse=True)[:10]]
            return FinanceLearningAnswer(
                "Saved finalized payslips: " + "; ".join(lines) + (f". Showing 10 of {len(matched)}; narrow by employee or period for more." if len(matched) > 10 else ".") + " These are employer-entered amounts, and marked-paid status is not bank-reconciled.",
                intent="finance_payroll_records",
            )
        return FinanceLearningAnswer(
            f"Saved payroll has {len(matched)} finalized payslip(s)" + (f" with pay-period end from {period[0].isoformat()} to before {period[1].isoformat()}" if period else "") + f". Gross pay totals {_money_by_currency(matched, 'gross_pay')}; net salary totals {_money_by_currency(matched, 'net_pay')}; total payout including linked expense payments is {_money_by_currency(matched, 'cash_to_employee')}. Drafts are excluded. Amounts come from employer-entered payslips and are not bank-reconciled or validated against local tax rules.",
            intent="finance_payroll_records",
        )
    return None
