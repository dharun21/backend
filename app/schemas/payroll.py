from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


Money = Decimal


class EmployeeInput(BaseModel):
    employee_code: str = Field(min_length=1, max_length=40)
    full_name: str = Field(min_length=1, max_length=160)
    email: EmailStr | None = None
    department: str | None = Field(default=None, max_length=120)
    pay_type: Literal["monthly", "hourly"] = "monthly"
    monthly_salary: Money | None = Field(default=None, ge=0, decimal_places=2)
    hourly_rate: Money | None = Field(default=None, ge=0, decimal_places=2)
    currency: str = Field(min_length=3, max_length=12)
    annual_paid_leave_days: Decimal = Field(default=Decimal("0"), ge=0, le=366, decimal_places=2)
    active: bool = True


class EmployeeResponse(EmployeeInput):
    model_config = ConfigDict(from_attributes=True)
    id: str
    created_at: datetime
    updated_at: datetime
    paid_leave_used_days: Decimal = Decimal("0")
    paid_leave_remaining_days: Decimal = Decimal("0")


class LeaveInput(BaseModel):
    employee_id: str
    start_date: date
    end_date: date
    days: Decimal = Field(gt=0, le=366, decimal_places=2)
    leave_type: Literal["paid", "unpaid"]
    note: str | None = Field(default=None, max_length=500)


class LeaveResponse(LeaveInput):
    model_config = ConfigDict(from_attributes=True)
    id: str
    status: str
    created_at: datetime


class ReimbursementInput(BaseModel):
    employee_id: str
    expense_date: date
    category: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=300)
    receipt_reference: str | None = Field(default=None, max_length=160)
    amount: Money = Field(gt=0, decimal_places=2)
    currency: str = Field(min_length=3, max_length=12)
    payroll_treatment: Literal["outside_gross", "inside_gross"]


class ReimbursementResponse(ReimbursementInput):
    model_config = ConfigDict(from_attributes=True)
    id: str
    payslip_id: str | None
    status: str
    paid_at: date | None
    payment_reference: str | None
    created_at: datetime


class ReimbursementPayment(BaseModel):
    paid_at: date
    payment_reference: str | None = Field(default=None, max_length=120)


class PayslipInput(BaseModel):
    employee_id: str
    period_start: date
    period_end: date
    regular_hours: Decimal | None = Field(default=None, ge=0, le=1000, decimal_places=2)
    base_pay: Money | None = Field(default=None, ge=0, decimal_places=2)
    overtime_pay: Money = Field(default=Decimal("0"), ge=0, decimal_places=2)
    bonus_pay: Money = Field(default=Decimal("0"), ge=0, decimal_places=2)
    allowances: Money = Field(default=Decimal("0"), ge=0, decimal_places=2)
    leave_deduction: Money = Field(default=Decimal("0"), ge=0, decimal_places=2)
    employee_tax: Money = Field(default=Decimal("0"), ge=0, decimal_places=2)
    other_deductions: Money = Field(default=Decimal("0"), ge=0, decimal_places=2)
    employer_contributions: Money = Field(default=Decimal("0"), ge=0, decimal_places=2)
    reimbursement_ids: list[str] = Field(default_factory=list, max_length=50)
    note: str | None = Field(default=None, max_length=500)


class PayslipResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    employee_id: str
    employee_name: str
    employee_code: str
    employee_email: EmailStr | None
    business_name: str
    period_start: date
    period_end: date
    currency: str
    regular_hours: Decimal | None
    hourly_rate_snapshot: Money | None
    base_pay: Money
    overtime_pay: Money
    bonus_pay: Money
    allowances: Money
    leave_deduction: Money
    employee_tax: Money
    other_deductions: Money
    employer_contributions: Money
    gross_pay: Money
    net_pay: Money
    reimbursement_total: Money
    taxable_claim_total: Money
    cash_to_employee: Money
    employer_cost: Money
    reimbursement_lines: list[dict]
    note: str | None
    status: str
    finalized_at: datetime | None
    paid_at: date | None
    payment_reference: str | None
    created_at: datetime


class PayslipFinalize(BaseModel):
    reviewed_pay_leave_and_taxes: bool


class PayslipPayment(BaseModel):
    paid_at: date
    payment_reference: str | None = Field(default=None, max_length=120)


class PayrollOverview(BaseModel):
    employee_count: int
    draft_payslips: int
    finalized_unpaid_payslips: int
    pending_reimbursements: int
    approved_unpaid_reimbursements: int
    confirmed_unpaid_taxes: int
    currency: str
    next_payroll_amount: Decimal
    basis_note: str


class GovernmentTaxInput(BaseModel):
    tax_type: str = Field(min_length=1, max_length=100)
    jurisdiction: str = Field(min_length=1, max_length=120)
    authority_name: str | None = Field(default=None, max_length=160)
    period_start: date
    period_end: date
    due_date: date | None = None
    amount: Money = Field(ge=0, decimal_places=2)
    currency: str = Field(min_length=3, max_length=12)
    note: str | None = Field(default=None, max_length=500)


class GovernmentTaxResponse(GovernmentTaxInput):
    model_config = ConfigDict(from_attributes=True)
    id: str
    status: str
    confirmed_at: datetime | None
    paid_at: date | None
    payment_reference: str | None
    created_at: datetime


class GovernmentTaxConfirm(BaseModel):
    reviewed_local_tax_basis: bool
