from datetime import date, datetime, timezone
from uuid import uuid4

from sqlalchemy import Date, DateTime, Float, ForeignKey, JSON, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class FinanceRecord(Base):
    __tablename__ = "finance_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("finance_workspaces.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("finance_documents.id", ondelete="CASCADE"), index=True)
    record_type: Mapped[str] = mapped_column(String(32), default="invoice")
    counterparty: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reference_number: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    issue_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(12), nullable=True)
    subtotal: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    tax_total: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    total_amount: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    extraction_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    review_notes: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="needs_review")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    workspace: Mapped["FinanceWorkspace"] = relationship()
    document: Mapped["FinanceDocument"] = relationship()
