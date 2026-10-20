from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FinanceWorkspaceInput(BaseModel):
    business_name: str = Field(min_length=1, max_length=200)
    industry: str | None = Field(default=None, max_length=160)
    country: str | None = Field(default=None, max_length=120)
    currency: str = Field(default="USD", min_length=3, max_length=12)
    size: str | None = Field(default=None, max_length=80)
    financial_year: str | None = Field(default=None, max_length=80)
    workflows: list[str] = Field(default_factory=list, max_length=20)
    sources: list[str] = Field(default_factory=list, max_length=20)
    payment_terms: str | None = Field(default=None, max_length=80)
    approval_limit: str | None = Field(default=None, max_length=80)
    reviewer: str | None = Field(default=None, max_length=160)
    categories: str | None = Field(default=None, max_length=1000)
    completed: bool = True


class FinanceWorkspaceResponse(FinanceWorkspaceInput):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    updated_at: datetime


class FinanceDocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    original_name: str
    extension: str
    content_type: str | None
    size_bytes: int
    status: str
    created_at: datetime


class FinanceStatementResponse(BaseModel):
    id: str
    document_id: str
    document_name: str
    source_type: str
    institution_name: str | None
    account_holder: str | None
    account_number: str | None
    statement_date: date | None
    period_start: date | None
    period_end: date | None
    currency: str | None
    document_reference: str | None
    transaction_count: int
    created_at: datetime
    status: str = "saved"

class FinanceRecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    document_id: str
    record_type: str
    counterparty: str | None
    reference_number: str | None
    issue_date: date | None
    due_date: date | None
    currency: str | None
    subtotal: Decimal | None
    tax_total: Decimal | None
    total_amount: Decimal | None
    extraction_confidence: float
    review_notes: list[str]
    status: str
    payment_status: Literal["unpaid", "paid"] = "unpaid"
    paid_at: date | None = None
    reminder_date: date | None = None
    created_at: datetime
    updated_at: datetime


class FinanceRecordCorrection(BaseModel):
    # Explicit classification is required at confirmation time so a supplier bill
    # can never be silently confirmed (and counted) as customer income.
    record_type: Literal["invoice", "expense"] | None = None
    counterparty: str | None = Field(default=None, max_length=200)
    reference_number: str | None = Field(default=None, max_length=120)
    issue_date: date | None = None
    due_date: date | None = None
    currency: str | None = Field(default=None, max_length=12)
    subtotal: Decimal | None = Field(default=None, ge=0)
    tax_total: Decimal | None = Field(default=None, ge=0)
    total_amount: Decimal | None = Field(default=None, ge=0)


class FinancePaymentUpdate(BaseModel):
    payment_status: Literal["unpaid", "paid"]


class FinanceReminderUpdate(BaseModel):
    reminder_date: date | None


class DocumentProcessResponse(BaseModel):
    document_id: str
    status: str
    records: list[FinanceRecordResponse]
    warnings: list[str]
