"""Retail and corporate finance guidance based on the saved business profile.

Only explicit, labelled example inputs are calculated here. Workspace records
do not contain POS, inventory, budgets, entities, or a general ledger.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from app.services.finance_learning import FinanceLearningAnswer


IAS_2 = {"title": "IFRS Foundation: IAS 2 Inventories", "url": "https://www.ifrs.org/issued-standards/list-of-standards/ias-2-inventories/"}
IFRS_15 = {"title": "IFRS Foundation: IFRS 15 Revenue from Contracts with Customers", "url": "https://www.ifrs.org/issued-standards/list-of-standards/ifrs-15-revenue-from-contracts-with-customers/"}
IFRS_10 = {"title": "IFRS Foundation: IFRS 10 Consolidated Financial Statements", "url": "https://www.ifrs.org/issued-standards/list-of-standards/ifrs-10-consolidated-financial-statements/"}
IAS_21 = {"title": "IFRS Foundation: IAS 21 Foreign Exchange Rates", "url": "https://www.ifrs.org/issued-standards/list-of-standards/ias-21-the-effects-of-changes-in-foreign-exchange-rates/"}


def _input(question: str, label: str) -> Decimal | None:
    match = re.search(rf"\b(?:{label})\b\s*(?:is|are|of|=|:)?\s*((?:(?:USD|EUR|GBP|INR|CAD|AUD)\s*)?[$€£₹]?\s*-?\d[\d,]*(?:\.\d+)?)", question, re.I)
    if not match:
        return None
    try:
        return Decimal(re.sub(r"[^\d.\-]", "", match.group(1)))
    except InvalidOperation:
        return None


def _example(reply: str, label: str, value: str) -> FinanceLearningAnswer:
    return FinanceLearningAnswer(
        reply, intent="finance_business_type_calculation",
        figures=({"label": label, "value": value, "source": "labelled values supplied in this question; not workspace records"},),
        assumptions=("Illustrative calculation only. Inputs must describe the same period and, for money, the same currency; no POS, inventory, budget, entity, or ledger values were read from the workspace.",),
    )


def _mixed_currencies(question: str) -> bool:
    codes = set(re.findall(r"\b(?:USD|EUR|GBP|INR|CAD|AUD)\b", question.upper()))
    symbols = {code for symbol, code in (("€", "EUR"), ("£", "GBP"), ("₹", "INR")) if symbol in question}
    if "$" in question and not codes:
        symbols.add("dollar currency")
    return len(codes | symbols) > 1


def _kind(question: str, profile: dict) -> str | None:
    explicit = re.search(r"\b(retail|corporate)\b", question, re.I)
    if explicit:
        return explicit.group(1).lower()
    selected = str(profile.get("business_type") or "").strip().lower()
    return selected if selected in {"retail", "corporate"} else None


def answer_business_type_finance(question: str, profile: dict) -> FinanceLearningAnswer | None:
    """Give type-specific guidance without inventing type-specific workspace data."""
    lower = question.lower()
    if re.search(r"\bretail\b", lower) and re.search(r"\bcorporate\b", lower) and re.search(r"\b(?:compare|comparison|difference|different|versus|vs|both)\b", lower):
        return FinanceLearningAnswer("Retail describes how a business sells goods to customers; corporate describes an organization context and can include a retail group. Retail reviews often need POS, SKU, inventory, returns, and product margin data. Corporate reviews may also need entity, department, budget, contract, procurement, and close data. Zengen currently has confirmed invoices and supplier bills, so it can analyze their open balances for either profile but cannot infer those extra ledgers.", intent="finance_business_type_guidance", sources=(IAS_2, IFRS_10))
    kind = _kind(question, profile)
    # A corporation can operate stores, and a retailer can use budgets or POs.
    # Follow the explicit finance topic before the broad profile label.
    if re.search(r"\b(?:inventory|stock|sku|sell[ -]?through|shrinkage|reorder|pos|point of sale|card (?:settlement|sales|payments?)|cash drawer|cash register|merchant fee|payment gateway)\b", lower):
        kind = "retail"
    elif re.search(r"\b(?:budget|department|cost cent(?:er|re)|variance|purchase order|procurement|three[ -]?way|goods received|consolidat\w*|intercompany|subsidiar\w*|multi[ -]?entity|group accounts?|deferred revenue|performance obligation|revenue recognition|payroll|accrual|month[ -]?end|year[ -]?end|general ledger|journal)\b", lower):
        kind = "corporate"
    if kind is None:
        return None

    if kind == "retail":
        if re.search(r"\b(?:inventory|stock)\s+turnover\b", lower):
            cogs = _input(question, r"(?:cost of goods sold|cogs)")
            average = _input(question, r"average\s+inventory")
            if cogs is not None and average is not None:
                if _mixed_currencies(question):
                    return FinanceLearningAnswer("Use cost of goods sold and average inventory in the same currency, or provide a dated conversion rate first.", intent="finance_data_limit", needs_more_info=True)
                if average <= 0 or cogs < 0:
                    return FinanceLearningAnswer("Inventory turnover needs nonnegative cost of goods sold and positive average inventory at cost.", intent="finance_data_limit", needs_more_info=True)
                result = cogs / average
                return _example(f"Illustrative inventory turnover = cost of goods sold / average inventory at cost = {cogs:,.2f} / {average:,.2f} = {result:.2f} times. Compare the same period and inventory valuation basis.", "Inventory turnover", f"{result:.2f} times")
            return FinanceLearningAnswer(
                "Retail inventory turnover = cost of goods sold / average inventory at cost for the same period. Zengen does not store stock movements, inventory valuation, or cost of goods sold separately, so I cannot calculate it from your confirmed invoices and bills. Supply both labelled inputs for an example.",
                intent="finance_data_limit", sources=(IAS_2,), needs_more_info=True,
            )
        if re.search(r"\bsell[ -]?through\b", lower):
            sold = _input(question, r"units?\s+sold")
            available = _input(question, r"units?\s+(?:available|received)")
            if sold is not None and available is not None:
                if available <= 0 or sold < 0 or sold > available:
                    return FinanceLearningAnswer("Sell-through needs positive units available and units sold between zero and that amount for the same period and product group.", intent="finance_data_limit", needs_more_info=True)
                result = sold / available * 100
                return _example(f"Illustrative sell-through = units sold / units available × 100 = {sold} / {available} × 100 = {result:.2f}%. Define the product group and period consistently.", "Sell-through", f"{result:.2f}%")
            return FinanceLearningAnswer("Sell-through compares units sold with units available in a defined period and product group. Zengen has no SKU or stock ledger, so supply labelled units sold and units available for an example.", intent="finance_data_limit", needs_more_info=True)
        if re.search(r"\b(?:inventory|stock|sku|shrinkage|reorder|slow[ -]?moving|obsolete)\b", lower):
            return FinanceLearningAnswer(
                "For retail stock decisions, review SKU quantities, purchase cost, sell-through, returns, and physical count differences; value inventory under the accounting rules used by the business. Zengen currently has no SKU, stock movement, count, or inventory valuation ledger, so it cannot identify slow movers, shrinkage, reorder points, or stock profit from invoice and bill totals.",
                intent="finance_data_limit", sources=(IAS_2,), needs_more_info=True,
            )
        if re.search(r"\b(?:pos|point of sale|card (?:settlement|sales|payments?)|daily takings|cash drawer|cash register|merchant fee|payment gateway)\b", lower):
            return FinanceLearningAnswer("Retail POS sales, cash, card receipts, processor fees, and settlement dates should be reconciled separately. Zengen has no POS feed or dated bank and processor ledger, so confirmed invoices cannot prove daily takings or cash received.", intent="finance_data_limit", needs_more_info=True)
        if re.search(r"\b(?:return|refund|discount|coupon|credit note|promotion)\b", lower):
            return FinanceLearningAnswer("For retail returns and promotions, link the original sale, discount or credit note, refund, and any stock return so sales and inventory are not double counted. Zengen does not yet store linked return, discount, or credit-note transactions, so I cannot calculate their effect from invoices alone.", intent="finance_data_limit", needs_more_info=True)
        if re.search(r"\b(?:vat|gst|sales tax|tax collected)\b", lower):
            return FinanceLearningAnswer("Retail tax reporting needs jurisdiction, taxable and exempt sales, tax rates, returns, and adjustments. Zengen's invoice tax fields and issue-date totals are not a tax return; confirm the local rules and transaction detail before calculating liability.", intent="finance_data_limit", needs_more_info=True)
        if (re.search(r"\bretail\b", lower) and re.search(r"\b(?:improve|increase|grow|optimi[sz]e|plan|focus|help|analys\w*)\b", lower)) or (re.search(r"\b(?:key metrics?|kpis?|what should (?:i|we) track|prioriti[sz]e)\b", lower) and re.search(r"\b(?:my|our|retail|business|finance)\b", lower)):
            return FinanceLearningAnswer("For a retail finance review, start with confirmed invoice and supplier-bill balances, then add POS settlements, SKU sales, inventory at cost, returns, and operating expenses. Compare gross margin by product only after matching recognized sales with cost of goods sold; check stock turnover and shrinkage from a stock ledger. Zengen can analyze confirmed invoices and bills now, but cannot verify retail gross profit or stock performance yet.", intent="finance_business_type_guidance", sources=(IAS_2,))

    if kind == "corporate":
        if re.search(r"\bbudget\s+variance\b|\bvariance\s+(?:to|against|versus|vs)\s+budget\b", lower):
            budget = _input(question, r"budget(?:ed)?(?:\s+(?:amount|expense|cost|revenue|sales))?")
            actual = _input(question, r"actual(?:\s+(?:amount|expense|cost|revenue|sales))?")
            if budget is not None and actual is not None:
                if _mixed_currencies(question):
                    return FinanceLearningAnswer("Use actual and budget values in the same currency, or provide a dated conversion rate first.", intent="finance_data_limit", needs_more_info=True)
                difference = actual - budget
                classification = "above" if difference > 0 else "below" if difference < 0 else "on"
                return _example(f"Illustrative variance = actual - budget = {actual:,.2f} - {budget:,.2f} = {difference:,.2f} ({classification} budget). Whether that is favorable depends on whether these are costs or revenue. Use the same entity, period, currency, and scope.", "Actual minus budget", f"{difference:,.2f}")
            return FinanceLearningAnswer("Budget variance = actual - budget for the same entity, department, period, and currency. Zengen has no approved budget or department fields, so supply labelled actual and budget values for an example.", intent="finance_data_limit", needs_more_info=True)
        if re.search(r"\b(?:consolidat\w*|intercompany|subsidiar\w*|multi[ -]?entity|group accounts?)\b", lower):
            return FinanceLearningAnswer("Corporate consolidation needs separate entity ledgers, ownership or control information, accounting policies, and intercompany balances and eliminations. Zengen has one finance workspace per account and no entity or elimination ledger, so it cannot produce consolidated statements from its current records.", intent="finance_data_limit", sources=(IFRS_10,), needs_more_info=True)
        if re.search(r"\b(?:budget|department|cost cent(?:er|re)|variance)\b", lower):
            return FinanceLearningAnswer("Corporate budget and department analysis needs approved budgets and tagged actual transactions for the same period and entity. Zengen's confirmed invoices and bills have no budget, department, or cost-center dimension; it cannot claim a real variance or department P&L.", intent="finance_data_limit", needs_more_info=True)
        if re.search(r"\b(?:purchase order|procurement|three[ -]?way|goods received|approval|spend control)\b", lower):
            return FinanceLearningAnswer("Corporate procurement review often compares an approved purchase order, evidence of goods or services received, and the supplier bill before approval. Zengen stores bills and source documents but no purchase-order, receipt, or approval workflow, so it cannot verify a three-way match or policy compliance.", intent="finance_data_limit", needs_more_info=True)
        if re.search(r"\b(?:contract|deferred revenue|performance obligation|revenue recognition|subscription)\b", lower):
            return FinanceLearningAnswer("Corporate contract revenue needs the contract terms, promised goods or services, performance timing, and any advance payments. An issued invoice is not proof that revenue was earned. Zengen does not store performance obligations or deferred-revenue entries, so it cannot calculate recognized contract revenue from invoices alone.", intent="finance_data_limit", sources=(IFRS_15,), needs_more_info=True)
        if re.search(r"\b(?:payroll|accrual|month[ -]?end|year[ -]?end|close|general ledger|journal)\b", lower):
            return FinanceLearningAnswer("A corporate close needs a complete general ledger, reconciliations, payroll, accruals, deferrals, and review evidence. Zengen's confirmed invoices and bills cover only part of that work; it cannot certify a closed period or complete financial statements.", intent="finance_data_limit", needs_more_info=True)
        if re.search(r"\b(?:fx|foreign exchange|currency exposure|multi[ -]?currency|group currency)\b", lower):
            return FinanceLearningAnswer("Corporate currency reporting needs each entity's functional currency, transaction dates, applicable exchange rates, and translation policies. Zengen keeps different currencies separate; it does not consolidate or translate group accounts from invoice totals alone.", intent="finance_data_limit", sources=(IAS_21,), needs_more_info=True)
        if re.search(r"\b(?:audit|control|compliance|risk)\b", lower):
            return FinanceLearningAnswer("For a corporate audit trail, keep source documents, approvals, change history, reconciliations, and reviewer evidence. Zengen can link confirmed records to uploaded files, but it has no complete approval chain, immutable journal, or audit opinion; it cannot certify compliance.", intent="finance_data_limit", needs_more_info=True)
        if re.search(r"\b(?:tax|vat|gst)\b", lower):
            return FinanceLearningAnswer("Corporate tax or indirect-tax liability needs the applicable jurisdiction, entity, tax period, complete taxable transactions, adjustments, and filed positions. Zengen's invoice and bill totals are not a tax computation or return.", intent="finance_data_limit", needs_more_info=True)
        if (re.search(r"\bcorporate\b", lower) and re.search(r"\b(?:improve|increase|grow|optimi[sz]e|plan|focus|help|analys\w*)\b", lower)) or (re.search(r"\b(?:key metrics?|kpis?|what should (?:i|we) track|prioriti[sz]e)\b", lower) and re.search(r"\b(?:my|our|corporate|business|finance)\b", lower)):
            return FinanceLearningAnswer("For a corporate finance review, start with confirmed receivables, payables, due-date exposure, and supplier concentration. Add entity and department tags, approved budgets, contracts, purchase orders, and a complete general ledger before making claims about budget variance, group profit, or consolidated cash. Zengen can analyze confirmed invoice and bill balances now, but cannot perform a corporate close or consolidation yet.", intent="finance_business_type_guidance", sources=(IFRS_10, IFRS_15))
    return None
