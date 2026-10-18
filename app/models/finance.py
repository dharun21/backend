from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class FinanceWorkspace(Base):
    __tablename__ = "finance_workspaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    business_name: Mapped[str] = mapped_column(String(200))
    industry: Mapped[str | None] = mapped_column(String(160), nullable=True)
    country: Mapped[str | None] = mapped_column(String(120), nullable=True)
    currency: Mapped[str] = mapped_column(String(12), default="USD")
    size: Mapped[str | None] = mapped_column(String(80), nullable=True)
    financial_year: Mapped[str | None] = mapped_column(String(80), nullable=True)
    workflows: Mapped[list[str]] = mapped_column(JSON, default=list)
    sources: Mapped[list[str]] = mapped_column(JSON, default=list)
    payment_terms: Mapped[str | None] = mapped_column(String(80), nullable=True)
    approval_limit: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reviewer: Mapped[str | None] = mapped_column(String(160), nullable=True)
    categories: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    completed: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    owner: Mapped["User"] = relationship(back_populates="finance_workspace")
    documents: Mapped[list["FinanceDocument"]] = relationship(back_populates="workspace", cascade="all, delete-orphan")


class FinanceDocument(Base):
    __tablename__ = "finance_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("finance_workspaces.id", ondelete="CASCADE"), index=True)
    original_name: Mapped[str] = mapped_column(String(255))
    storage_name: Mapped[str] = mapped_column(String(80), unique=True)
    extension: Mapped[str] = mapped_column(String(12))
    content_type: Mapped[str | None] = mapped_column(String(160), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(40), default="awaiting_processing")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    workspace: Mapped["FinanceWorkspace"] = relationship(back_populates="documents")
