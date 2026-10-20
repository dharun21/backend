from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class OnboardingSession(Base):
    __tablename__ = "onboarding_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    current_step: Mapped[int] = mapped_column(Integer, default=0)
    completed: Mapped[bool] = mapped_column(default=False)
    answers: Mapped[list[dict]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )


class BusinessProfile(Base):
    __tablename__ = "business_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    business_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(200), nullable=True)
    country: Mapped[str | None] = mapped_column(String(120), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(12), nullable=True)
    financial_year: Mapped[str | None] = mapped_column(String(80), nullable=True)
    what_they_sell: Mapped[str | None] = mapped_column(Text, nullable=True)
    revenue_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    business_stage: Mapped[str | None] = mapped_column(String(80), nullable=True)
    main_goal: Mapped[str | None] = mapped_column(String(200), nullable=True)
    biggest_challenge: Mapped[str | None] = mapped_column(Text, nullable=True)
    targets: Mapped[list[dict]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )