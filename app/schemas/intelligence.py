from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FocusBalance(BaseModel):
    currency: str
    amount: str
    count: int


class FocusAging(FocusBalance):
    bucket: str
    label: str


class FocusCounterparty(BaseModel):
    name: str
    currency: str
    amount: str
    share_percent: str
    record_count: int


class FocusMissingData(BaseModel):
    title: str
    detail: str


class FinanceFocusResponse(BaseModel):
    business_type: Literal["retail", "corporate", "general"]
    as_of: str
    balances: dict[str, list[FocusBalance]]
    aging: dict[str, list[FocusAging]]
    concentration: dict[str, list[FocusCounterparty]]
    missing_counterparty_counts: dict[str, int]
    missing_data: list[FocusMissingData]
    basis_note: str


class FinanceOverviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    total_income: float
    total_expenses: float
    net_profit: float
    outstanding_invoices: list[dict]
    overdue_invoices: list[dict]
    overdue_bills: list[dict]
    bills_due_soon: list[dict]
    recent_transactions: list[dict]
    monthly_issued_amounts: list[dict]
    monthly_revenue_and_costs: list[dict]
    records_needing_attention: list[dict]
    basis_note: str
    data_completeness_note: str | None = None
    focus: FinanceFocusResponse


class ScenarioComparison(BaseModel):
    name: str
    description: str
    estimated_impact: float
    assumptions: list[str]
    confidence: str  # "low" | "medium" | "high"


class ProfitCoachResponse(BaseModel):
    answer: str
    response_type: str  # "fact_lookup" | "business_guidance" | "proposed_action"
    supporting_figures: list[dict] = Field(default_factory=list)
    supporting_documents: list[dict] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    scenarios: list[ScenarioComparison] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    follow_up_questions: list[dict] = Field(default_factory=list)
    needs_more_info: bool = False


class QuestionForUser(BaseModel):
    id: str
    question: str
    context: str
    suggested_answers: list[str]
    allow_custom: bool = True
