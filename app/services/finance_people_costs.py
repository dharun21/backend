"""Reviewed payroll, leave, and employee-expense guidance for Finance chat.

Only labelled values in the question are used for examples. Saved employee and
payroll facts are handled separately by finance_payroll_workspace.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from app.services.finance_learning import FinanceLearningAnswer


IAS_19 = {
    "title": "IFRS Foundation: IAS 19 Employee Benefits",
    "url": "https://www.ifrs.org/issued-standards/list-of-standards/ias-19-employee-benefits/",
}
ILO_LEAVE = {
    "title": "International Labour Organization: paid annual leave guidance",
    "url": "https://www.ilo.org/ilo-helpdesk-questions-and-answers-business-and-working-time",
}
IRS_REIMBURSEMENTS = {
    "title": "IRS: Publication 463, employee expense reimbursements (US-specific)",
    "url": "https://www.irs.gov/publications/p463",
}
IRS_EMPLOYER_TAX = {
    "title": "IRS: Employer's Tax Guide (US-specific)",
    "url": "https://www.irs.gov/publications/p15",
}

_AMOUNT = r"(?:(?:USD|EUR|GBP|INR|CAD|AUD)\s*)?[$€£₹]?\s*(-?\d[\d,]*(?:\.\d+)?)"


def _number(question: str, *labels: str) -> Decimal | None:
    for label in labels:
        match = re.search(rf"\b(?:{label})\b\s*(?:is|are|of|=|:)?\s*{_AMOUNT}", question, re.I)
        if match:
            try:
                return Decimal(match.group(1).replace(",", ""))
            except InvalidOperation:
                return None
    return None


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}"


def _mixed_currencies(question: str) -> bool:
    codes = set(re.findall(r"\b(?:USD|EUR|GBP|INR|CAD|AUD)\b", question.upper()))
    symbols = {code for symbol, code in (("€", "EUR"), ("£", "GBP"), ("₹", "INR")) if symbol in question}
    return len(codes | symbols) > 1 or ("$" in question and bool((codes | symbols) - {"USD"}))


def _example(reply: str, label: str, value: Decimal, *, unit: str = "money") -> FinanceLearningAnswer:
    formatted = _money(value) if unit == "money" else f"{value.normalize()} days"
    return FinanceLearningAnswer(
        reply=reply,
        intent="finance_people_calculation",
        figures=({"label": label, "value": formatted, "source": "labelled inputs in this question; not workspace records"},),
        assumptions=(
            "Illustrative arithmetic only. Inputs must refer to the same employee, period and currency where relevant. Employment policy and local law must be checked before payment or filing.",
        ),
    )


def _limit(reply: str, sources: tuple[dict[str, str], ...] = ()) -> FinanceLearningAnswer:
    return FinanceLearningAnswer(reply, intent="finance_data_limit", sources=sources, needs_more_info=True)


def answer_finance_people_costs(question: str, profile: dict) -> FinanceLearningAnswer | None:
    """Answer employee-cost questions without inventing workspace or legal facts."""
    lower = question.lower()
    salary = bool(re.search(r"\b(?:salar(?:y|ies)|wages?|payroll|payslip|paychecks?|gross pay|net pay|take[ -]?home pay|hourly (?:pay|rate)|overtime|employee pay|staff pay)\b", lower))
    leave = bool(re.search(r"\b(?:leave|pto|paid time off|absen(?:ce|ces|t)|holiday pay|vacation|sick days?)\b", lower))
    bonus = bool(re.search(r"\b(?:bonus(?:es)?|incentive pay|commission(?:s)?)\b", lower))
    reimbursement = bool(re.search(r"\b(?:reimburs\w*|employee expense claims?|staff expense claims?)\b", lower))
    tax = bool(re.search(r"\b(?:payroll tax(?:es)?|salary tax(?:es)?|employee tax(?:es)?|employer tax(?:es)?|employment tax(?:es)?|tax withholding|withhold\w*|tax deducted|tax deduction|government taxes|tax(?:es)? to (?:the )?government|social security|provident fund|pension contributions?)\b", lower)) or bool(
        re.search(r"\btax(?:es)?\b", lower)
        and re.search(r"\b(?:employee|employer|staff|salary|payroll|business|company|government|retail|corporate)\b", lower)
    )
    if not (salary or leave or bonus or reimbursement or tax):
        return None

    kind = str(profile.get("business_type") or "").strip().lower()
    if re.search(r"\bretail\b", lower) and not re.search(r"\bcorporate\b", lower):
        kind = "retail"
    elif re.search(r"\bcorporate\b", lower) and not re.search(r"\bretail\b", lower):
        kind = "corporate"
    kind = kind if kind in {"retail", "corporate"} else "business"
    if _mixed_currencies(question):
        return _limit("Use one currency for an example, or supply a dated conversion rate before combining employee-cost amounts.")

    if leave:
        opening = _number(question, r"opening (?:leave )?balance")
        earned = _number(question, r"(?:accrued|earned) (?:leave )?days?")
        taken = _number(question, r"(?:leave|days?) taken", r"(?:used|taken) (?:leave )?days?")
        if None not in (opening, earned, taken):
            assert opening is not None and earned is not None and taken is not None
            if min(opening, earned, taken) < 0 or taken > opening + earned:
                return _limit("A leave-balance example needs nonnegative opening, earned, and taken days, with taken days no greater than days available.")
            balance = opening + earned - taken
            return _example(f"Illustrative leave balance = opening balance + earned days - days taken = {opening} + {earned} - {taken} = {balance} days. Check the policy for carryover, accrual, and leave types.", "Remaining leave", balance, unit="days")
        monthly = _number(question, r"monthly (?:base )?salary", r"monthly wages?")
        workdays = _number(question, r"(?:paid |scheduled |working )?(?:workdays|working days|work days) in (?:the )?month", r"(?:paid |scheduled |working )?(?:workdays|working days|work days)")
        unpaid = _number(question, r"unpaid leave days?", r"unpaid days?")
        if None not in (monthly, workdays, unpaid):
            assert monthly is not None and workdays is not None and unpaid is not None
            if monthly < 0 or workdays <= 0 or unpaid < 0 or unpaid > workdays:
                return _limit("Unpaid-leave arithmetic needs nonnegative monthly salary, positive agreed workdays, and unpaid days no greater than workdays.")
            deduction = monthly / workdays * unpaid
            return _example(f"Illustrative unpaid-leave deduction = monthly salary / agreed workdays × unpaid days = {_money(monthly)} / {workdays} × {unpaid} = {_money(deduction)}. This assumes that daily-rate method is allowed by the employment terms and local law; verify it before adjusting pay.", "Unpaid-leave deduction", deduction)
        return _limit(
            f"For {kind} staff, track leave type, entitlement, opening balance, accrual, approved days taken, and policy carryover. Paid leave normally remains within agreed salary; do not add it again just because days were taken. Some earned, unused paid leave may create an obligation under the applicable accounting policy. An unpaid-leave deduction needs the applicable policy, payroll period, and agreed daily-rate basis. Zengen can summarize saved annual allowances and approved leave, but it cannot determine statutory entitlement or an allowable deduction without your local policy. For examples, provide `opening leave balance`, `earned leave days`, and `leave taken`, or `monthly salary`, `workdays`, and `unpaid leave days`.",
            (IAS_19, ILO_LEAVE),
        )

    if reimbursement:
        claim = _number(question, r"(?:approved )?(?:reimbursement )?claim amount", r"(?:approved )?reimbursement amount")
        advance = _number(question, r"(?:employee |travel |expense )?advance")
        if claim is not None and advance is not None:
            if claim < 0 or advance < 0 or advance > claim:
                return _limit("A reimbursement example needs a nonnegative approved claim and an advance between zero and the claim amount.")
            due = claim - advance
            return _example(f"Illustrative amount still to reimburse = approved claim - advance = {_money(claim)} - {_money(advance)} = {_money(due)}. Confirm receipts, business purpose, approval, and whether any part must be treated as taxable pay locally.", "Remaining reimbursement", due)
        return _limit(
            f"{kind.capitalize()} teams can reimburse approved business expenses such as travel, supplies, or client visits. Record the employee, expense date, purpose, receipts, policy category, approver, currency, advance, amount approved, and payment date. Keep this separate from salary; local tax rules may treat some payments as wages. Zengen can summarize saved claims and employer-entered approval or payment status, but it does not verify receipts or bank settlement. For an example, provide `approved claim amount` and `advance`. The linked IRS guidance describes US rules only.",
            (IRS_REIMBURSEMENTS,),
        )

    taxable_base = _number(question, r"taxable (?:pay|wages|base|amount)")
    stated_rate = _number(question, r"(?:assumed |example |hypothetical )?(?:tax|withholding) rate")
    if tax and taxable_base is not None and stated_rate is not None:
        if taxable_base < 0 or stated_rate < 0 or stated_rate > 100:
            return _limit("A tax arithmetic example needs a nonnegative stated taxable base and a stated rate from 0% to 100%.")
        amount = taxable_base * stated_rate / 100
        return _example(f"Illustrative amount = stated taxable base × stated rate = {_money(taxable_base)} × {stated_rate}% = {_money(amount)}. This does not establish that the tax applies or that the base and rate are legally correct; check the jurisdiction, tax year, exemptions, and filing rules.", "Hypothetical tax amount", amount)

    eligible_pay = _number(question, r"(?:bonus|commission) (?:base|eligible pay)", r"eligible (?:pay|salary) for (?:bonus|commission)")
    bonus_rate = _number(question, r"(?:bonus|commission) rate")
    if bonus_rate is not None and eligible_pay is None:
        eligible_pay = _number(question, r"base salary")
    if bonus and eligible_pay is not None and bonus_rate is not None:
        if eligible_pay < 0 or bonus_rate < 0 or bonus_rate > 100:
            return _limit("A percentage-bonus example needs nonnegative eligible pay and a stated rate from 0% to 100%.")
        amount = eligible_pay * bonus_rate / 100
        return _example(f"Illustrative bonus = eligible pay × stated bonus rate = {_money(eligible_pay)} × {bonus_rate}% = {_money(amount)}. Confirm eligibility, approval, period, and local tax treatment before paying it.", "Illustrative bonus", amount)

    gross_input = _number(question, r"gross pay", r"gross salary")
    base = _number(question, r"base salary", r"monthly salary", r"wages?")
    if base is None and gross_input is None:
        base = _number(question, r"salary")
    hourly_rate = _number(question, r"(?:regular )?hourly rate")
    hours_worked = _number(question, r"(?:regular )?hours worked")
    hourly_component = hourly_rate is not None or hours_worked is not None
    if hourly_component:
        if hourly_rate is None or hours_worked is None or hourly_rate < 0 or hours_worked < 0:
            return _limit("Hourly-pay arithmetic needs nonnegative `hourly rate` and `hours worked` for the same pay period.")
        if base is not None or gross_input is not None:
            return _limit("Please clarify whether stated salary or gross pay already includes the hourly wages, to avoid counting the same work twice.")
        base = hourly_rate * hours_worked
    bonus_amount = _number(question, r"bonus(?: amount)?", r"incentive(?: amount)?", r"commission(?: amount)?")
    overtime = _number(question, r"overtime(?: pay| amount)?")
    overtime_rate = _number(question, r"overtime hourly rate", r"overtime rate")
    overtime_hours = _number(question, r"overtime hours?")
    if overtime_rate is not None or overtime_hours is not None:
        if overtime_rate is None or overtime_hours is None or overtime_rate < 0 or overtime_hours < 0:
            return _limit("Overtime arithmetic needs nonnegative `overtime rate` and `overtime hours` under the applicable employment rules.")
        if overtime is not None:
            return _limit("Provide either `overtime pay` or `overtime rate` and `overtime hours`, to avoid counting overtime twice.")
        overtime = overtime_rate * overtime_hours
    allowances = _number(question, r"(?:taxable )?allowances?")
    withholding = _number(question, r"(?:employee )?(?:tax )?withholding", r"employee tax(?:es)?", r"tax deducted")
    deductions = _number(question, r"other deductions?", r"employee deductions?")
    employer = _number(question, r"employer contributions?", r"employer taxes?")
    if gross_input is not None and any(value is not None for value in (base, bonus_amount, overtime, allowances)):
        return _limit("Please clarify whether the stated gross pay already includes base salary, bonus, overtime, and allowances, to avoid counting them twice.")
    if (base is not None or gross_input is not None) and (hourly_component or any(value is not None for value in (bonus_amount, overtime, allowances, withholding, deductions, employer))):
        values = (base, gross_input, bonus_amount, overtime, allowances, withholding, deductions, employer)
        if any(value is not None and value < 0 for value in values):
            return _limit("Payroll example inputs must be nonnegative. Give adjustments separately with their meaning and period.")
        gross = gross_input if gross_input is not None else (base or 0) + (bonus_amount or 0) + (overtime or 0) + (allowances or 0)
        withheld = (withholding or 0) + (deductions or 0)
        if withheld > gross:
            return _limit("Supplied withholding and deductions exceed the example gross pay; check the inputs before calculating take-home pay.")
        figures = [{"label": "Gross pay", "value": _money(gross), "source": "labelled inputs in this question; not workspace records"}]
        base_detail = f" Hourly base pay = {_money(hourly_rate)} × {hours_worked} = {_money(base)}." if hourly_component else ""
        parts = [f"Illustrative gross pay = {_money(gross)} from the supplied pay components.{base_detail}"]
        if withholding is not None or deductions is not None:
            after_listed = gross - withheld
            parts.append(f"After the supplied deductions = gross pay - supplied withholding - supplied other deductions = {_money(after_listed)}. This is not final take-home pay unless all deductions are included.")
            figures.append({"label": "After supplied deductions", "value": _money(after_listed), "source": "labelled inputs in this question; other deductions may be missing"})
        else:
            parts.append("Take-home pay is unknown because employee withholding and other deductions were not supplied.")
        if employer is not None:
            cost = gross + employer
            parts.append(f"Employer cost after supplied contributions = gross pay + supplied employer contributions = {_money(cost)}; other employer costs may still be missing.")
            figures.append({"label": "Cost after supplied employer contributions", "value": _money(cost), "source": "labelled inputs in this question; other employer costs may be missing"})
        else:
            parts.append("Full employer payroll cost is unknown because employer contributions and other costs were not supplied.")
        parts.append("Reimbursements are tracked separately. These figures are not a payslip, tax assessment, or proof of payment.")
        return FinanceLearningAnswer(
            " ".join(parts),
            intent="finance_people_calculation",
            figures=tuple(figures),
            assumptions=("All amounts apply to the same employee, period, and currency. Tax and leave treatment depend on local law and employment terms; no actual workspace payroll records were used.",),
        )

    if tax:
        return _limit(
            "A business may owe employee withholding, employer payroll contributions, and other taxes, depending on its jurisdiction, registration, employee status, tax period, and transactions. These are different obligations: employee withholding is deducted from pay; employer contributions add to the employer's cost. Business income tax and sales tax/GST/VAT are separate from payroll. Zengen can summarize entered payslip withholding and manually confirmed government tax records, but it has no automatic tax calculation, filing, or verified remittance; it cannot determine whether a tax applies or what your business legally owes. Provide the country/region, tax year, tax type, worker status, and applicable official rate or rule. For arithmetic only, give `taxable pay` and `tax rate`. The linked IRS guide is a US example, not a rule for your location.",
            (IRS_EMPLOYER_TAX,),
        )
    if bonus:
        return _limit(
            f"For {kind} staff, a bonus or commission needs a documented eligibility rule, measurement period, approved amount, and payment date. Keep it separate from base pay until the approved payroll run, and assess local withholding and employer contributions. Zengen can report bonus amounts entered on finalized payslips, but it has no separate sales-commission eligibility or bonus-approval calculation. For a pay example, provide `base salary` and `bonus amount` for one employee and period.",
            (IAS_19,),
        )
    if salary:
        detail = "For shift or hourly retail staff, also track agreed hours, overtime, and commissions." if kind == "retail" else "For corporate staff, also track pay grades, departments, variable pay, benefits, and approvals." if kind == "corporate" else "Track agreed hours, overtime, commissions, and benefits where applicable."
        if re.search(r"\b(?:book|account(?:ing)?|record|expense|accru\w*|journal|ledger)\b", lower):
            return _limit(
                "Under the applicable accounting basis, wages and employer contributions generally become expenses as staff provide service, with unpaid amounts recorded as liabilities. Employee withholding is a liability to remit; net pay clears the payable when paid. Approved business reimbursements need their own expense and payable trail, with local rules deciding whether any part is taxable pay. Labor may be operating expense or production cost depending on what staff did and the accounting policy. Zengen stores employer-entered payslips and claims but has no payroll journal, bank reconciliation, or tax-remittance ledger; it cannot post or verify accounting entries from payslips alone.",
                (IAS_19,),
            )
        return _limit(
            f"A payroll run needs each employee's contract and pay period, base pay or hours and rate, overtime, approved bonuses, paid or unpaid leave, employee deductions, employer contributions, and payment evidence. {detail} Gross pay, take-home pay, employer cost, and cash paid are different figures. Zengen can summarize saved employee records and finalized payslips, but payment status is employer-entered and cannot be inferred from invoice or bill totals. For an example, provide `base salary`, plus labelled bonus, overtime, withholding, deductions, or employer contributions.",
            (IAS_19,),
        )
    return None
