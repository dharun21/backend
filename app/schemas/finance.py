from datetime import date, datetime
from decimal import Decimal

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
    created_at: datetime
    updated_at: datetime


class FinanceRecordCorrection(BaseModel):
    counterparty: str | None = Field(default=None, max_length=200)
    reference_number: str | None = Field(default=None, max_length=120)
    issue_date: date | None = None
    due_date: date | None = None
    currency: str | None = Field(default=None, max_length=12)
    subtotal: Decimal | None = Field(default=None, ge=0)
    tax_total: Decimal | None = Field(default=None, ge=0)
    total_amount: Decimal | None = Field(default=None, ge=0)


class DocumentProcessResponse(BaseModel):
    document_id: str
    status: str
    records: list[FinanceRecordResponse]
    warnings: list[str]
