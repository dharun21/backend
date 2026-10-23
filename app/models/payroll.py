"""Owner-scoped employee costs and immutable finalized payslips."""

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import Date, DateTime, ForeignKey, JSON, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class Employee(Base):
    __tablename__ = "finance_employees"
    __table_args__ = (UniqueConstraint("workspace_id", "employee_code", name="uq_finance_employee_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("finance_workspaces.id", ondelete="CASCADE"), index=True)
    employee_code: Mapped[str] = mapped_column(String(40))
    full_name: Mapped[str] = mapped_column(String(160))
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    department: Mapped[str | None] = mapped_column(String(120), nullable=True)
    pay_type: Mapped[str] = mapped_column(String(12), default="monthly")
    monthly_salary: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    hourly_rate: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(12))
    annual_paid_leave_days: Mapped[Decimal] = mapped_column(Numeric(7, 2), default=Decimal("0"))
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class EmployeeLeave(Base):
    __tablename__ = "finance_employee_leave"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("finance_workspaces.id", ondelete="CASCADE"), index=True)
    employee_id: Mapped[str] = mapped_column(ForeignKey("finance_employees.id", ondelete="CASCADE"), index=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    days: Mapped[Decimal] = mapped_column(Numeric(7, 2))
    leave_type: Mapped[str] = mapped_column(String(12))
    status: Mapped[str] = mapped_column(String(12), default="pending")
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class EmployeeReimbursement(Base):
    __tablename__ = "finance_employee_reimbursements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("finance_workspaces.id", ondelete="CASCADE"), index=True)
    employee_id: Mapped[str] = mapped_column(ForeignKey("finance_employees.id", ondelete="CASCADE"), index=True)
    payslip_id: Mapped[str | None] = mapped_column(ForeignKey("finance_payslips.id", ondelete="SET NULL"), nullable=True, index=True)
    expense_date: Mapped[date] = mapped_column(Date)
    category: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(String(300))
    receipt_reference: Mapped[str | None] = mapped_column(String(160), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(12))
    payroll_treatment: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(12), default="pending")
    paid_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    payment_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Payslip(Base):
    __tablename__ = "finance_payslips"
    __table_args__ = (UniqueConstraint("employee_id", "period_start", "period_end", name="uq_finance_payslip_period"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("finance_workspaces.id", ondelete="CASCADE"), index=True)
    employee_id: Mapped[str] = mapped_column(ForeignKey("finance_employees.id", ondelete="RESTRICT"), index=True)
    employee_name: Mapped[str] = mapped_column(String(160))
    employee_code: Mapped[str] = mapped_column(String(40))
    employee_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    business_name: Mapped[str] = mapped_column(String(200))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(12))
    regular_hours: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    hourly_rate_snapshot: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    base_pay: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    overtime_pay: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    bonus_pay: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    allowances: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    leave_deduction: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    employee_tax: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    other_deductions: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    employer_contributions: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    gross_pay: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    net_pay: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    reimbursement_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    taxable_claim_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    cash_to_employee: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    employer_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    reimbursement_lines: Mapped[list[dict]] = mapped_column(JSON, default=list)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="draft")
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    payment_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class GovernmentTax(Base):
    __tablename__ = "finance_government_taxes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("finance_workspaces.id", ondelete="CASCADE"), index=True)
    tax_type: Mapped[str] = mapped_column(String(100))
    jurisdiction: Mapped[str] = mapped_column(String(120))
    authority_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(12))
    status: Mapped[str] = mapped_column(String(12), default="draft")
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    payment_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
