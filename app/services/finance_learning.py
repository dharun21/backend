"""Reviewed small-business finance explanations and explicit-input calculators.

This is application knowledge, not a fine-tune of the Ollama model. Workspace
figures continue to come from confirmed records in FinanceIntelligence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP


IRS_RECORDS = {
    "title": "IRS: Recordkeeping for small businesses",
    "url": "https://www.irs.gov/businesses/small-businesses-self-employed/recordkeeping",
}
IFRS_CASH_FLOWS = {
    "title": "IFRS: IAS 7 Statement of Cash Flows",
    "url": "https://www.ifrs.org/issued-standards/list-of-standards/ias-7-statement-of-cash-flows/",
}
IFRS_REVENUE = {
    "title": "IFRS: IFRS 15 Revenue from Contracts with Customers",
    "url": "https://www.ifrs.org/issued-standards/list-of-standards/ifrs-15-revenue-from-contracts-with-customers/",
}
IFRS_SME_AGE_ANALYSIS = {
    "title": "IFRS Foundation: IFRS for SMEs update on age and maturity analysis",
    "url": "https://www.ifrs.org/news-and-events/news/2025/09/september-2025-ifrs-for-smes-accounting-standard-update/",
    "date": "2025-09",
}

FINANCE_MODEL_GUIDANCE = (
    "For small-business finance, distinguish issued invoice totals, recognized revenue, cash received, "
    "profit, and bank cash. An invoice issue date alone does not establish revenue recognition; the "
    "accounting basis and performance matter. Profit needs complete recognized income and expenses, "
    "and cash flow needs dated receipts and payments. Do not call Zengen's issue-date difference final "
    "profit or cash flow. Receivables and payables can be unpaid, partly paid, overdue, or disputed; "
    "use recorded cumulative payments to calculate remaining balances; payment entries are not a bank-reconciled cash ledger. Gross profit needs cost of goods sold. "
    "Working capital needs current assets and liabilities. Show formula, units, period, source records, "
    "and assumptions for calculations. Never add different currencies without conversion with a dated rate. "
    "Age open receivables and payables by saved due date, preserve missing due dates as unknown, "
    "and treat concentration and due-date views as exposure rather than credit-loss estimates. "
    "Treat forecasts as conditional scenarios, and say when source data is missing. "
    "Use the saved business type as context, not as proof of an accounting policy or available data. "
    "For retail, distinguish POS receipts, inventory at cost, cost of goods sold, returns, and invoice totals; "
    "do not invent SKU margins or stock counts. For corporate, distinguish entity-level and group figures, "
    "budgets, approvals, contracts, and a complete general ledger; do not invent consolidation or budget results. "
    "Corporate organizations can operate retail businesses. "
    "For both retail and corporate employees, keep salary, bonus, overtime, paid or unpaid leave, "
    "employee withholding, employer contributions, reimbursements, and actual cash payments distinct. "
    "Do not infer employee payroll, leave balances, approved claims, or tax remittances from invoices or supplier bills. "
    "Never assume a statutory tax rate, leave entitlement, deduction method, or reimbursement tax treatment from business type alone; "
    "these require the jurisdiction, period, employment terms, and applicable policy."
)


@dataclass(frozen=True)
class FinanceLearningAnswer:
    reply: str
    intent: str = "finance_education"
    sources: tuple[dict[str, str], ...] = ()
    figures: tuple[dict[str, str], ...] = ()
    assumptions: tuple[str, ...] = ()
    needs_more_info: bool = False


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}"


def _named_amount(question: str, labels: tuple[str, ...]) -> Decimal | None:
    amount = r"(?:[$₹€£]\s*)?([0-9][0-9,]*(?:\.[0-9]+)?)"
    for label in labels:
        match = re.search(rf"\b(?:{label})\b\s*(?:is|are|of|=|:)?\s*{amount}", question, re.I)
        if match:
            try:
                return Decimal(match.group(1).replace(",", ""))
            except InvalidOperation:
                return None
    return None


def _calculation(question: str) -> FinanceLearningAnswer | None:
    lower = question.lower()
    source = "values supplied in this question; not workspace records"
    assumption = ("This is an example using the values you supplied, not a calculation from confirmed workspace records. Use the same currency and appropriate date or period for all inputs.",)
    if re.search(r"\bcurrent\s+ratio\b", lower):
        assets = _named_amount(question, (r"current\s+assets",))
        liabilities = _named_amount(question, (r"current\s+liabilities",))
        if assets is not None and liabilities is not None:
            if liabilities <= 0:
                return FinanceLearningAnswer("Current ratio needs a positive current-liabilities amount; check the supplied balance.", intent="finance_calculation", assumptions=assumption)
            ratio = assets / liabilities
            return FinanceLearningAnswer(
                f"Current ratio = current assets / current liabilities = {_money(assets)} / {_money(liabilities)} = {ratio:.2f}. "
                "Use balances from the same date and accounting basis. This ratio alone does not show when cash will arrive.",
                intent="finance_calculation",
                figures=({"label": "Current ratio", "value": f"{ratio:.2f}", "source": source},),
                assumptions=assumption,
            )
    if re.search(r"\bquick\s+ratio\b", lower):
        cash = _named_amount(question, (r"cash",))
        securities = _named_amount(question, (r"marketable\s+securities",))
        receivables = _named_amount(question, (r"(?:accounts?\s+)?receivables?",))
        liabilities = _named_amount(question, (r"current\s+liabilities",))
        if None not in (cash, securities, receivables, liabilities):
            assert cash is not None and securities is not None and receivables is not None and liabilities is not None
            if liabilities <= 0:
                return FinanceLearningAnswer("Quick ratio needs a positive current-liabilities amount; check the supplied balance.", intent="finance_calculation", assumptions=assumption)
            ratio = (cash + securities + receivables) / liabilities
            return FinanceLearningAnswer(
                f"Quick ratio = (cash + marketable securities + receivables) / current liabilities = "
                f"({_money(cash)} + {_money(securities)} + {_money(receivables)}) / {_money(liabilities)} = {ratio:.2f}. "
                "Use balances from the same date and check whether receivables are collectible; the ratio is not bank cash.",
                intent="finance_calculation",
                figures=({"label": "Quick ratio", "value": f"{ratio:.2f}", "source": source},),
                assumptions=assumption,
            )
    if re.search(r"\b(?:cash\s+runway|runway)\b", lower):
        cash = _named_amount(question, (r"(?:available|opening)\s+cash",))
        burn = _named_amount(question, (r"monthly\s+net\s+cash\s+burn",))
        if cash is not None and burn is not None:
            if burn <= 0:
                return FinanceLearningAnswer("Cash runway is undefined when monthly net cash burn is zero or negative.", intent="finance_calculation", assumptions=assumption)
            if cash < 0:
                return FinanceLearningAnswer("This cash-runway example needs nonnegative available cash; check the supplied balance.", intent="finance_calculation", assumptions=assumption)
            months = cash / burn
            return FinanceLearningAnswer(
                f"Illustrative cash runway = available cash / monthly net cash burn = {_money(cash)} / {_money(burn)} = {months:.2f} months. "
                "This assumes the supplied burn stays constant and excludes new financing, collections, and one-time cash movements.",
                intent="finance_calculation",
                figures=({"label": "Illustrative runway", "value": f"{months:.2f} months", "source": source},),
                assumptions=assumption,
            )
    if re.search(r"\bbreak[ -]?even\b", lower):
        fixed = _named_amount(question, (r"fixed\s+costs?",))
        price = _named_amount(question, (r"(?:sale|selling)\s+price\s+per\s+unit", r"price\s+per\s+unit"))
        variable = _named_amount(question, (r"variable\s+cost\s+per\s+unit",))
        if None not in (fixed, price, variable):
            assert fixed is not None and price is not None and variable is not None
            if price <= variable:
                return FinanceLearningAnswer(
                    "No finite break-even sales volume exists with these inputs: price per unit must exceed variable cost per unit. "
                    f"Here, contribution per unit is {_money(price - variable)}.",
                    intent="finance_calculation", assumptions=assumption,
                )
            units = (fixed / (price - variable)).to_integral_value(rounding=ROUND_CEILING)
            revenue = units * price
            return FinanceLearningAnswer(
                f"Break-even = fixed costs / (price per unit - variable cost per unit) = "
                f"{_money(fixed)} / ({_money(price)} - {_money(variable)}) = {units:,} whole units. "
                f"Sales at that volume would be {_money(revenue)}. This assumes a constant price and variable cost per unit, "
                "all units are sold, and the fixed costs cover the same period.",
                intent="finance_calculation",
                figures=({"label": "Break-even units", "value": str(units), "source": source},),
                assumptions=assumption,
            )
    if re.search(r"\bgross\s+(?:profit|margin)\b", lower):
        revenue = _named_amount(question, (r"revenue", r"sales"))
        cogs = _named_amount(question, (r"cost\s+of\s+goods\s+sold", r"cogs"))
        if revenue is not None and cogs is not None:
            if revenue == 0:
                return FinanceLearningAnswer("Gross margin percentage is undefined when revenue is zero.", intent="finance_calculation", assumptions=assumption)
            gross = revenue - cogs
            pct = gross / revenue * 100
            return FinanceLearningAnswer(
                f"Gross profit = revenue - cost of goods sold = {_money(revenue)} - {_money(cogs)} = {_money(gross)}. "
                f"Gross margin = gross profit / revenue × 100 = {pct:.2f}%.",
                intent="finance_calculation",
                figures=({"label": "Gross profit", "value": _money(gross), "source": source},
                         {"label": "Gross margin", "value": f"{pct:.2f}%", "source": source}),
                assumptions=assumption,
            )
    if re.search(r"\bnet\s+(?:profit\s+)?margin\b", lower):
        revenue = _named_amount(question, (r"revenue", r"sales"))
        profit = _named_amount(question, (r"net\s+profit",))
        if revenue is not None and profit is not None:
            if revenue == 0:
                return FinanceLearningAnswer("Net margin percentage is undefined when revenue is zero.", intent="finance_calculation", assumptions=assumption)
            pct = profit / revenue * 100
            return FinanceLearningAnswer(
                f"Net margin = net profit / revenue × 100 = {_money(profit)} / {_money(revenue)} × 100 = {pct:.2f}%. "
                "This assumes the supplied net profit includes all relevant expenses for the same period.",
                intent="finance_calculation",
                figures=({"label": "Net margin", "value": f"{pct:.2f}%", "source": source},),
                assumptions=assumption,
            )
    if re.search(r"\bexpense\s+ratio\b", lower):
        revenue = _named_amount(question, (r"revenue", r"sales"))
        expenses = _named_amount(question, (r"(?:total\s+)?expenses?",))
        if revenue is not None and expenses is not None:
            if revenue == 0:
                return FinanceLearningAnswer("Expense ratio is undefined when revenue is zero.", intent="finance_calculation", assumptions=assumption)
            pct = expenses / revenue * 100
            return FinanceLearningAnswer(
                f"Expense ratio = expenses / revenue × 100 = {_money(expenses)} / {_money(revenue)} × 100 = {pct:.2f}%. "
                "Use amounts for the same period and accounting basis.",
                intent="finance_calculation",
                figures=({"label": "Expense ratio", "value": f"{pct:.2f}%", "source": source},),
                assumptions=assumption,
            )
    if re.search(r"\b(?:month[ -]?over[ -]?month|percentage\s+change|percent\s+change)\b", lower):
        previous = _named_amount(question, (r"(?:previous|last)\s+month", r"previous\s+value"))
        current = _named_amount(question, (r"(?:current|this)\s+month", r"current\s+value"))
        if previous is not None and current is not None:
            if previous == 0:
                return FinanceLearningAnswer("Percentage change is undefined with a zero previous value; report the absolute change instead.", intent="finance_calculation", assumptions=assumption)
            change = current - previous
            pct = change / abs(previous) * 100
            return FinanceLearningAnswer(
                f"Change = current - previous = {_money(current)} - {_money(previous)} = {_money(change)}. "
                f"Percentage change = change / |previous| × 100 = {pct:.2f}%. "
                "Compare the same category, currency, and length of period.",
                intent="finance_calculation",
                figures=({"label": "Percentage change", "value": f"{pct:.2f}%", "source": source},),
                assumptions=assumption,
            )
    percent_of = re.search(r"\b([0-9]+(?:\.[0-9]+)?)\s*%\s+of\s+(?:[$₹€£]\s*)?([0-9][0-9,]*(?:\.[0-9]+)?)\b", question)
    if percent_of:
        part = Decimal(percent_of.group(1))
        whole = Decimal(percent_of.group(2).replace(",", ""))
        result = part / 100 * whole
        return FinanceLearningAnswer(
            f"{part}% of {_money(whole)} = {part} / 100 × {_money(whole)} = {_money(result)}.",
            intent="finance_calculation",
            figures=({"label": "Percentage amount", "value": _money(result), "source": source},),
            assumptions=assumption,
        )
    list_match = re.search(r"\b(?:calculate\s+|find\s+)?(total|sum|average|mean)\s+of\s+(.+?)\s*\??$", question, re.I)
    if list_match:
        values_text = list_match.group(2).strip()
        # A comma between three digits can mean a thousands separator. Require
        # another format rather than silently interpreting an ambiguous input.
        if not re.search(r"\b\d{1,3},\d{3}\b", values_text):
            pieces = re.split(r"\s*,\s*|\s+and\s+", values_text, flags=re.I)
            if 2 <= len(pieces) <= 20 and all(re.fullmatch(r"\d+(?:\.\d+)?", piece) for piece in pieces):
                values = [Decimal(piece) for piece in pieces]
                total = sum(values, Decimal(0))
                is_average = list_match.group(1).lower() in {"average", "mean"}
                result = total / len(values) if is_average else total
                label = "Average" if is_average else "Total"
                formula = f"({_money(total)}) / {len(values)}" if is_average else " + ".join(_money(value) for value in values)
                return FinanceLearningAnswer(
                    f"{label} = {formula} = {_money(result)}. These are values supplied in your question; make sure they use the same currency and period.",
                    intent="finance_calculation",
                    figures=({"label": label, "value": _money(result), "source": source},),
                    assumptions=assumption,
                )
    return None


def answer_finance_learning(question: str) -> FinanceLearningAnswer | None:
    """Answer general concepts or fully labelled example calculations.

    Questions about this user's records stay with the workspace finance tools.
    """
    calculation = _calculation(question)
    if calculation:
        return calculation
    lower = question.lower().strip()
    if not re.search(r"\b(?:what|explain|define|difference|how|why|teach|meaning|formula|compare|tell)\b", lower):
        return None
    if re.search(r"\b(?:my|our|these|those|uploaded|workspace|zengen)\b", lower):
        return None

    if "cash" in lower and re.search(r"\brevenue\b", lower):
        return FinanceLearningAnswer(
            "Cash received is money a customer has paid. Revenue is recorded under the business's accounting basis when the relevant sale or service qualifies for recognition. "
            "An issued invoice can remain unpaid, and an advance payment can arrive before revenue is recognized. "
            "Zengen's invoice issue-date totals alone do not establish recognized revenue or cash receipts.",
            sources=(IFRS_REVENUE, IFRS_CASH_FLOWS),
        )
    if "profit" in lower and "cash" in lower:
        return FinanceLearningAnswer(
            "Profit is recognized income minus recognized expenses for a period. Cash available depends on actual receipts and payments, opening cash, loans, owner withdrawals, and other movements. "
            "A profitable business can be short of cash while customers have not paid. Zengen needs dated payment and bank balances to calculate available cash.",
            sources=(IFRS_CASH_FLOWS,),
        )
    if re.search(r"\bworking\s+capital\b", lower):
        return FinanceLearningAnswer(
            "Working capital = current assets - current liabilities. It describes short-term resources available after short-term obligations. "
            "Examples include cash, receivables and inventory versus supplier payables and short-term debt. A complete balance sheet is needed; invoices alone are insufficient."
        )
    if re.search(r"\b(?:receivables?|payables?|invoices?|bills?)\s+ag(?:e|ing|eing)\b|\baging\s+(?:receivables?|payables?|invoices?|bills?)\b", lower):
        return FinanceLearningAnswer(
            "A due-date aging view groups open balances by how many calendar days past the saved due date they are. "
            "Keep not-yet-due, due-today, overdue, and missing-due-date records distinct; never infer a due date from the issue date. "
            "Aging helps review collections and obligations but does not itself measure expected credit losses or predict payment timing.",
            sources=(IFRS_SME_AGE_ANALYSIS,),
        )
    if re.search(r"\b(?:concentration\s+risk|customer\s+concentration|supplier\s+concentration)\b", lower):
        return FinanceLearningAnswer(
            "Concentration compares each identified counterparty's open balance with the total identified open balance in the same currency. "
            "A large share shows exposure to one relationship; it does not prove default risk or tell you what to collect first. "
            "Keep unidentified counterparties and different currencies separate."
        )
    if re.search(r"\b(?:current\s+ratio|quick\s+ratio|cash\s+runway)\b", lower):
        return FinanceLearningAnswer(
            "Current ratio = current assets / current liabilities. Quick ratio = (cash + marketable securities + collectible receivables) / current liabilities. "
            "Both need a complete, same-date balance sheet; Zengen's invoice and bill records alone are insufficient. "
            "Illustrative cash runway = available cash / monthly net cash burn when burn is positive, using dated cash information and explicit assumptions."
        )
    if re.search(r"\bcash[ -]?flow\b", lower):
        return FinanceLearningAnswer(
            "Cash flow tracks dated money in and out. A cash-flow summary needs opening cash, actual receipts, actual payments, and closing cash for a period. "
            "An unpaid invoice is a possible future receipt, not current cash. A 'paid next month' estimate is a conditional scenario, not a guaranteed forecast. "
            "Zengen's current invoice issue-date totals cannot establish a bank cash balance.",
            sources=(IFRS_CASH_FLOWS,),
        )
    if re.search(r"\bcredit\s+notes?\b|\brefunds?\b", lower):
        return FinanceLearningAnswer(
            "A credit note reduces or reverses an earlier invoice or bill, often after a return, price adjustment, or error. A refund is cash paid back. "
            "They should be linked to the original transaction so sales, expenses, receivables, and cash are not counted twice. "
            "Zengen's current confirmed record types do not separately represent refunds or credit notes."
        )
    if re.search(r"\baccounts?\s+receivable\b|\breceivables?\b", lower):
        return FinanceLearningAnswer(
            "Accounts receivable is money customers owe for credit sales. Track each invoice's original amount, payments, remaining balance, due date, and any dispute. "
            "Unpaid means nothing has been recorded as paid; partially paid means a balance remains; overdue means that balance is past its due date. "
            "A disputed invoice needs review. Zengen can track the cumulative amount paid and remaining balance on confirmed invoices, but does not yet track disputes or a payment event ledger."
        )
    if re.search(r"\baccounts?\s+payable\b|\bpayables?\b", lower):
        return FinanceLearningAnswer(
            "Accounts payable is money the business owes suppliers. Track the bill amount, payments, remaining balance, due date, and any dispute. "
            "Upcoming bills are not yet due; overdue bills have a remaining balance past the due date. Paid and disputed bills need separate treatment. "
            "Zengen can track the cumulative amount paid and remaining balance on confirmed bills, but does not yet track disputes or a payment event ledger."
        )
    if re.search(r"\b(?:invoice|supplier\s+bill|customer\s+payment)\b", lower):
        return FinanceLearningAnswer(
            "A sales invoice requests payment from a customer; a customer payment records cash received against it. A supplier bill requests payment from the business; paying it records cash out. "
            "An expense is a cost recognized under the applicable accounting basis, which may differ from when the bill is issued or paid. Keep each document and its payments linked."
        )
    if re.search(r"\b(?:receipts?|recordkeeping|supporting\s+documents?)\b", lower):
        return FinanceLearningAnswer(
            "Keep invoices, receipts, credit notes, payment evidence, and other supporting documents with each transaction. They help verify amounts and dates, prepare statements, and support tax records. "
            "Required documents and retention periods depend on the jurisdiction and transaction; the linked IRS guidance applies to US businesses.",
            sources=(IRS_RECORDS,),
        )
    if re.search(r"\b(?:financial\s+statements?|balance\s+sheet|profit\s+and\s+loss|p&l)\b", lower):
        return FinanceLearningAnswer(
            "A profit-and-loss statement shows recognized income and expenses over a period. A balance sheet shows assets, liabilities, and equity at a date. "
            "A cash-flow statement shows actual cash movements over a period. Each answers a different question, and incomplete records can make any statement misleading. "
            "Zengen's current issue-date invoice/bill overview is not a complete set of financial statements."
        )
    if re.search(r"\b(?:expense\s+categor|spending\s+categor|unusual\s+(?:expense|change)|month[ -]?over[ -]?month)\b", lower):
        return FinanceLearningAnswer(
            "Assign expenses to consistent categories, such as rent, payroll, supplies, and marketing. Compare the same category across equal periods: "
            "month-over-month change = (current - previous) / |previous| × 100. A zero previous value has no meaningful percentage change. "
            "Flag large changes for review, then check seasonality, one-time bills, missing records, and category changes before calling them unusual."
        )
    if re.search(r"\bbreak[ -]?even\b", lower):
        return FinanceLearningAnswer(
            "Break-even units = fixed costs / (selling price per unit - variable cost per unit), rounded up to whole units. "
            "The denominator is contribution per unit and must be positive. State the period, price, variable cost, fixed costs, and the assumption that all units sell."
        )
    if re.search(r"\b(?:gross\s+(?:profit|margin)|net\s+(?:profit|margin)|expense\s+ratio|profit\s+margin)\b", lower):
        return FinanceLearningAnswer(
            "Gross profit = recognized revenue - cost of goods sold; gross margin = gross profit / revenue × 100. "
            "Net profit = recognized revenue - all recognized expenses; net margin = net profit / revenue × 100. "
            "Expense ratio = the chosen expense amount / revenue × 100. Use matching periods, and do not treat all supplier bills as cost of goods sold. "
            "Zengen's current issue-date invoice-minus-bill difference is not verified net profit."
        )
    if re.search(r"\b(?:average|percentage|total|currency\s+conversion|exchange\s+rate)\b", lower):
        return FinanceLearningAnswer(
            "Total = sum of comparable amounts; average = total / count; percentage = part / whole × 100 when the whole is nonzero. "
            "For currency conversion, multiply by a dated exchange rate and show the rate source and direction. Convert before combining amounts in different currencies."
        )
    if re.search(r"\b(?:revenue|expenses?|profit|loss)\b", lower):
        return FinanceLearningAnswer(
            "Revenue is income recognized from sales or services under the applicable accounting basis. Expenses are recognized business costs. "
            "Profit is revenue minus expenses for a period; a negative result is a loss. Cash receipts and payments can happen in different periods. "
            "An issued invoice or bill alone does not establish a complete profit figure."
        )
    return None


def answer_finance_workspace_limit(question: str) -> FinanceLearningAnswer | None:
    """Describe gaps that existing confirmed records cannot resolve."""
    lower = question.lower()
    if not re.search(r"\b(?:my|our|these|those|workspace|records)\b", lower):
        return None
    if re.search(r"\b(?:cash[ -]?flow|cash\s+(?:received|available|balance)|bank\s+balance)\b", lower):
        return FinanceLearningAnswer(
            "I can show confirmed invoice and bill issue-date amounts, but I cannot calculate your cash flow or available bank cash from them. "
            "I need opening and closing balances plus dated receipts and payments. An invoice marked paid is not a complete cash ledger, "
            "and cumulative recorded payments are not a complete bank-reconciled cash ledger.",
            intent="finance_data_limit", needs_more_info=True,
        )
    if re.search(r"\bworking\s+capital\b", lower):
        return FinanceLearningAnswer(
            "I cannot calculate your working capital from the current workspace. It needs current assets and current liabilities from a complete balance sheet; "
            "uploaded invoices and supplier bills are only part of that picture.",
            intent="finance_data_limit", needs_more_info=True,
        )
    if re.search(r"\b(?:current\s+ratio|quick\s+ratio)\b", lower):
        return FinanceLearningAnswer(
            "I cannot calculate your liquidity ratios from invoices and bills alone. I need current assets and liabilities from a complete same-date balance sheet; "
            "the quick ratio also needs cash, marketable securities, and collectible receivables.",
            intent="finance_data_limit", needs_more_info=True,
        )
    if re.search(r"\b(?:cash\s+runway|runway)\b", lower):
        return FinanceLearningAnswer(
            "I cannot calculate your actual cash runway from invoice and bill records. Provide available bank cash and a dated monthly net cash burn, "
            "and I can calculate an illustrative constant-burn scenario.",
            intent="finance_data_limit", needs_more_info=True,
        )
    if re.search(r"\bgross\s+(?:profit|margin)\b", lower):
        return FinanceLearningAnswer(
            "I cannot calculate your gross profit or gross margin from current confirmed records because cost of goods sold is not identified separately. "
            "Provide recognized revenue and matching cost of goods sold for the same period, and I can calculate an example.",
            intent="finance_data_limit", needs_more_info=True,
        )
    if re.search(r"\bnet\s+(?:profit|margin)\b", lower):
        return FinanceLearningAnswer(
            "I can show the difference between confirmed invoice and bill issue-date totals, but it is not verified net profit or net margin. "
            "A complete profit figure needs recognized revenue and all relevant expenses for the same period.",
            intent="finance_data_limit", needs_more_info=True,
        )
    if re.search(r"\bdisputed\b", lower) and re.search(r"\b(?:invoices?|bills?|receivables?|payables?)\b", lower):
        return FinanceLearningAnswer(
            "The workspace can track paid amounts and remaining balances, but not whether an invoice or bill is disputed. "
            "Disputes need separate status and supporting notes before I can identify them reliably.",
            intent="finance_data_limit", needs_more_info=True,
        )
    if re.search(r"\bexpense\s+categor(?:y|ies)\b", lower):
        return FinanceLearningAnswer(
            "The current confirmed records do not store a category for each expense, so I cannot report your spending by category yet. "
            "I can compare confirmed supplier bill amounts by issue month.",
            intent="finance_data_limit", needs_more_info=True,
        )
    if re.search(r"\b(?:financial\s+statements?|balance\s+sheet|profit\s+and\s+loss|p&l)\b", lower):
        return FinanceLearningAnswer(
            "The current workspace can summarize confirmed invoice and bill issue-date amounts. It does not contain all accounts and cash movements "
            "needed for a complete profit-and-loss statement, balance sheet, or cash-flow statement.",
            intent="finance_data_limit", needs_more_info=True,
        )
    return None
