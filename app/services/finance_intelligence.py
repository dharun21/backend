"""Finance calculations used by API responses and the local Finance Analyst."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select

from app.models.finance import FinanceDocument, FinanceWorkspace
from app.models.records import FinanceRecord


class FinanceIntelligence:
    def __init__(self, db, user):
        self.db = db
        self.user = user

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _workspace(self):
        return self.db.scalar(
            select(FinanceWorkspace).where(FinanceWorkspace.owner_id == self.user.id)
        )

    def _confirmed_records(self, workspace_id: str | None):
        if not workspace_id:
            return []
        return list(
            self.db.scalars(
                select(FinanceRecord)
                .join(FinanceDocument, FinanceRecord.document_id == FinanceDocument.id)
                .where(
                    FinanceRecord.workspace_id == workspace_id,
                    FinanceDocument.workspace_id == workspace_id,
                    FinanceRecord.status == "confirmed",
                )
            ).all()
        )

    @staticmethod
    def _to_float(value) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    # ------------------------------------------------------------------ #
    # Overview
    # ------------------------------------------------------------------ #
    def compute_overview(self) -> dict[str, Any]:
        workspace = self._workspace()
        if not workspace:
            return self._empty_overview()

        records = self._confirmed_records(workspace.id)
        currency = workspace.currency or "USD"

        # Only explicitly classified records count. "invoice" means money owed to
        # the business (customer invoice); "expense" means money the business owes
        # (supplier bill). Unclassified records are excluded from both totals so a
        # bill can never be silently counted as customer income.
        income_records = [r for r in records if r.record_type == "invoice"]
        expense_records = [r for r in records if r.record_type == "expense"]

        total_income = sum(self._to_float(r.total_amount) for r in income_records if r.total_amount)
        total_expenses = sum(self._to_float(r.total_amount) for r in expense_records if r.total_amount)
        net_profit = total_income - total_expenses

        # Due dates are business-calendar dates with no timezone, so they must be
        # compared against the server's local date. Using the UTC date would
        # misclassify items as overdue/not-due for a few hours around midnight.
        now = date.today()

        outstanding = []
        overdue = []
        bills_due_soon = []
        overdue_bills = []

        for r in records:
            if not r.total_amount:
                continue
            item = {
                "id": r.id,
                "name": r.counterparty or "Unknown",
                "reference": r.reference_number,
                "amount": self._to_float(r.total_amount),
                "currency": r.currency or currency,
                "due_date": r.due_date.isoformat() if r.due_date else None,
                "document_id": r.document_id,
            }
            due = r.due_date
            if not due:
                continue
            if r.payment_status == "paid":
                continue
            if r.record_type == "expense":
                # Money the business owes: past-due bills are overdue, near-due
                # bills are due soon. Never mixed into the invoice lists.
                if due < now:
                    overdue_bills.append(item)
                elif (due - now).days <= 7:
                    bills_due_soon.append(item)
            elif r.record_type == "invoice":
                # Money owed to the business: past-due invoices are overdue,
                # every other confirmed invoice is outstanding.
                if due < now:
                    overdue.append(item)
                else:
                    outstanding.append(item)
            # Unclassified records stay out of every due list until a reviewer
            # classifies them.

        recent = sorted(records, key=lambda r: r.created_at, reverse=True)[:10]
        recent_transactions = [
            {
                "id": r.id,
                "name": r.counterparty or "Unknown",
                "type": r.record_type,
                "amount": self._to_float(r.total_amount),
                "currency": r.currency or currency,
                "date": r.issue_date.isoformat() if r.issue_date else None,
                "status": r.status,
            }
            for r in recent
        ]

        monthly_issued_amounts = self._monthly_issued_amounts(records)
        monthly_revenue_and_costs = self._monthly_revenue_and_costs(records)

        needing_attention = list(
            self.db.scalars(
                select(FinanceRecord)
                .join(FinanceDocument, FinanceRecord.document_id == FinanceDocument.id)
                .where(
                    FinanceRecord.workspace_id == workspace.id,
                    FinanceDocument.workspace_id == workspace.id,
                    FinanceRecord.status == "needs_review",
                )
            ).all()
        )
        records_needing_attention = [
            {
                "id": r.id,
                "name": r.counterparty or "Unknown",
                "document_id": r.document_id,
                "status": r.status,
                "confidence": r.extraction_confidence,
                "review_notes": r.review_notes or [],
            }
            for r in needing_attention
        ]

        total_records = len(records)
        data_completeness_note = None
        if total_records == 0:
            data_completeness_note = "No confirmed financial records yet. Upload and confirm documents to see your overview."
        elif total_records < 5:
            data_completeness_note = (
                f"Based on {total_records} confirmed record(s) only — figures may not represent your complete business."
            )

        return {
            "total_income": round(total_income, 2),
            "total_expenses": round(total_expenses, 2),
            "net_profit": round(net_profit, 2),
            "currency": currency,
            "outstanding_invoices": outstanding,
            "overdue_invoices": overdue,
            "overdue_bills": overdue_bills,
            "bills_due_soon": bills_due_soon,
            "recent_transactions": recent_transactions,
            "monthly_issued_amounts": monthly_issued_amounts,
            "monthly_revenue_and_costs": monthly_revenue_and_costs,
            "records_needing_attention": records_needing_attention,
            "basis_note": (
                "Totals are sums of confirmed record amounts grouped by each record's issue date. "
                "They are not cash received or paid, and the monthly difference is not a final business profit."
            ),
            "data_completeness_note": data_completeness_note,
            "total_records": total_records,
        }

    def _empty_overview(self) -> dict[str, Any]:
        return {
            "total_income": 0,
            "total_expenses": 0,
            "net_profit": 0,
            "currency": "USD",
            "outstanding_invoices": [],
            "overdue_invoices": [],
            "overdue_bills": [],
            "bills_due_soon": [],
            "recent_transactions": [],
            "monthly_issued_amounts": [],
            "monthly_revenue_and_costs": [],
            "records_needing_attention": [],
            "basis_note": (
                "Totals are sums of confirmed record amounts grouped by each record's issue date. "
                "They are not cash received or paid, and the monthly difference is not a final business profit."
            ),
            "data_completeness_note": "No finance workspace found. Set up your finance workspace first.",
            "total_records": 0,
        }

    # ------------------------------------------------------------------ #
    # Monthly totals (grouped by issue date)
    # ------------------------------------------------------------------ #
    def _monthly_issued_amounts(self, records: list[FinanceRecord]) -> list[dict]:
        """Confirmed invoice/expense totals grouped by issue month.

        These are issue-date amounts, not cash movements: an invoice recorded
        this month may be paid much later, and a bill may be paid on terms.
        """
        from collections import defaultdict

        monthly: dict[str, dict[str, float]] = defaultdict(lambda: {"income": 0.0, "expense": 0.0})
        for r in records:
            if not r.issue_date or not r.total_amount:
                continue
            if r.record_type not in ("invoice", "expense"):
                continue
            key = r.issue_date.strftime("%Y-%m")
            amt = self._to_float(r.total_amount)
            if r.record_type == "expense":
                monthly[key]["expense"] += amt
            else:
                monthly[key]["income"] += amt

        sorted_months = sorted(monthly.keys())[-6:]
        return [
            {
                "month": m,
                "income": round(monthly[m]["income"], 2),
                "expense": round(monthly[m]["expense"], 2),
                "net": round(monthly[m]["income"] - monthly[m]["expense"], 2),
            }
            for m in sorted_months
        ]

    def _monthly_revenue_and_costs(self, records: list[FinanceRecord]) -> list[dict]:
        """Confirmed invoice totals vs expense totals by issue month.

        `net` is the difference of confirmed issue-date amounts only; payment
        timing and uncategorized costs are not reflected, so it is not a
        business profit figure.
        """
        from collections import defaultdict

        monthly: dict[str, dict[str, float]] = defaultdict(lambda: {"revenue": 0.0, "costs": 0.0})
        for r in records:
            if not r.issue_date or not r.total_amount:
                continue
            if r.record_type not in ("invoice", "expense"):
                continue
            key = r.issue_date.strftime("%Y-%m")
            amt = self._to_float(r.total_amount)
            if r.record_type == "expense":
                monthly[key]["costs"] += amt
            else:
                monthly[key]["revenue"] += amt

        sorted_months = sorted(monthly.keys())[-6:]
        return [
            {
                "month": m,
                "revenue": round(monthly[m]["revenue"], 2),
                "costs": round(monthly[m]["costs"], 2),
                "net": round(monthly[m]["revenue"] - monthly[m]["costs"], 2),
            }
            for m in sorted_months
        ]

    # ------------------------------------------------------------------ #
    # Break-even
    # ------------------------------------------------------------------ #
    def break_even(self, fixed_costs: float, price_per_unit: float, variable_cost_per_unit: float) -> dict[str, Any]:
        contribution_margin = price_per_unit - variable_cost_per_unit
        if contribution_margin <= 0:
            return {
                "break_even_units": None,
                "break_even_revenue": None,
                "contribution_margin": contribution_margin,
                "note": "Variable cost per unit is equal to or higher than price — no break-even point.",
            }
        units = fixed_costs / contribution_margin
        return {
            "break_even_units": round(units, 2),
            "break_even_revenue": round(units * price_per_unit, 2),
            "contribution_margin": round(contribution_margin, 2),
            "note": f"You need to sell approximately {round(units)} units to break even.",
        }

    # ------------------------------------------------------------------ #
    # Gross margin
    # ------------------------------------------------------------------ #
    def gross_margin(self, revenue: float, cogs: float) -> dict[str, Any]:
        if revenue <= 0:
            return {"gross_margin": 0.0, "gross_margin_percent": 0.0, "note": "No revenue recorded."}
        gm = revenue - cogs
        pct = (gm / revenue) * 100
        return {
            "gross_margin": round(gm, 2),
            "gross_margin_percent": round(pct, 2),
            "note": f"Gross margin is {round(pct, 2)}% of revenue.",
        }

    @staticmethod
    def _to_float(value) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0
