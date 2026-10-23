"""Owner-scoped payroll records, review workflow, and payslip downloads."""

from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
import re

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import CurrentUser, Database
from app.models.finance import FinanceWorkspace
from app.models.payroll import Employee, EmployeeLeave, EmployeeReimbursement, GovernmentTax, Payslip
from app.schemas.payroll import (
    EmployeeInput, EmployeeResponse, GovernmentTaxConfirm, GovernmentTaxInput,
    GovernmentTaxResponse, LeaveInput, LeaveResponse, PayrollOverview,
    PayslipFinalize, PayslipInput, PayslipPayment, PayslipResponse,
    ReimbursementInput, ReimbursementPayment, ReimbursementResponse,
)
from app.services.entitlements import require_specialist_access
from app.services.payslip_pdf import render_payslip


router = APIRouter(prefix="/finance/payroll", tags=["finance payroll"])
ZERO = Decimal("0.00")


def _workspace(db: Database, user: CurrentUser) -> FinanceWorkspace:
    require_specialist_access(db, user.id, "finance_analyst")
    workspace = db.scalar(select(FinanceWorkspace).where(FinanceWorkspace.owner_id == user.id))
    if workspace is None:
        raise HTTPException(404, "Finance workspace has not been set up.")
    return workspace


def _employee(db: Database, workspace_id: str, employee_id: str, *, lock: bool = False) -> Employee:
    query = select(Employee).where(Employee.id == employee_id, Employee.workspace_id == workspace_id)
    result = db.scalar(query.with_for_update() if lock else query)
    if result is None:
        raise HTTPException(404, "Employee not found.")
    return result


def _payslip(db: Database, workspace_id: str, payslip_id: str) -> Payslip:
    result = db.scalar(select(Payslip).where(Payslip.id == payslip_id, Payslip.workspace_id == workspace_id))
    if result is None:
        raise HTTPException(404, "Payslip not found.")
    return result


def _currency(code: str) -> str:
    value = code.strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", value):
        raise HTTPException(422, "Use a three-letter currency code.")
    return value


def _employee_response(db: Database, employee: Employee, year: int) -> EmployeeResponse:
    used = db.scalar(
        select(func.coalesce(func.sum(EmployeeLeave.days), 0)).where(
            EmployeeLeave.workspace_id == employee.workspace_id,
            EmployeeLeave.employee_id == employee.id,
            EmployeeLeave.leave_type == "paid",
            EmployeeLeave.status == "approved",
            EmployeeLeave.start_date >= date(year, 1, 1),
            EmployeeLeave.start_date < date(year + 1, 1, 1),
        )
    ) or ZERO
    allowance = employee.annual_paid_leave_days or ZERO
    return EmployeeResponse.model_validate(employee).model_copy(update={
        "paid_leave_used_days": Decimal(used),
        "paid_leave_remaining_days": allowance - Decimal(used),
    })


@router.get("/overview", response_model=PayrollOverview)
def payroll_overview(user: CurrentUser, db: Database) -> PayrollOverview:
    workspace = _workspace(db, user)
    employees = list(db.scalars(select(Employee).where(Employee.workspace_id == workspace.id, Employee.active.is_(True))))
    slips = list(db.scalars(select(Payslip).where(Payslip.workspace_id == workspace.id)))
    claims = list(db.scalars(select(EmployeeReimbursement).where(EmployeeReimbursement.workspace_id == workspace.id)))
    unpaid_taxes = db.scalar(select(func.count(GovernmentTax.id)).where(
        GovernmentTax.workspace_id == workspace.id, GovernmentTax.status == "confirmed",
    )) or 0
    unpaid = [slip for slip in slips if slip.status == "finalized"]
    return PayrollOverview(
        employee_count=len(employees),
        draft_payslips=sum(slip.status == "draft" for slip in slips),
        finalized_unpaid_payslips=len(unpaid),
        pending_reimbursements=sum(claim.status == "pending" for claim in claims),
        approved_unpaid_reimbursements=sum(claim.status == "approved" for claim in claims),
        confirmed_unpaid_taxes=unpaid_taxes,
        currency=workspace.currency,
        next_payroll_amount=sum((slip.cash_to_employee for slip in unpaid if slip.currency == workspace.currency), ZERO),
        basis_note="Finalized payslips are employer-entered and unpaid until marked paid. No bank transfer or tax filing is initiated. The amount includes only the workspace currency.",
    )


@router.get("/employees", response_model=list[EmployeeResponse])
def list_employees(user: CurrentUser, db: Database, year: int | None = None) -> list[EmployeeResponse]:
    workspace = _workspace(db, user)
    selected_year = year or date.today().year
    if selected_year < 1900 or selected_year > 9998:
        raise HTTPException(422, "Choose a valid leave year.")
    employees = db.scalars(select(Employee).where(Employee.workspace_id == workspace.id).order_by(Employee.full_name)).all()
    return [_employee_response(db, employee, selected_year) for employee in employees]


@router.post("/employees", response_model=EmployeeResponse, status_code=status.HTTP_201_CREATED)
def create_employee(payload: EmployeeInput, user: CurrentUser, db: Database) -> EmployeeResponse:
    workspace = _workspace(db, user)
    values = payload.model_dump()
    values["employee_code"] = values["employee_code"].strip().upper()
    values["full_name"] = values["full_name"].strip()
    values["currency"] = _currency(values["currency"])
    if not values["employee_code"] or not values["full_name"]:
        raise HTTPException(422, "Employee code and name are required.")
    if values["pay_type"] == "monthly" and values["monthly_salary"] is None:
        raise HTTPException(422, "Monthly staff need a monthly salary.")
    if values["pay_type"] == "hourly" and values["hourly_rate"] is None:
        raise HTTPException(422, "Hourly staff need an hourly rate.")
    if db.scalar(select(Employee.id).where(Employee.workspace_id == workspace.id, Employee.employee_code == values["employee_code"])):
        raise HTTPException(409, "This employee code is already used.")
    employee = Employee(workspace_id=workspace.id, **values)
    db.add(employee)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "This employee code is already used.") from None
    db.refresh(employee)
    return _employee_response(db, employee, date.today().year)


@router.put("/employees/{employee_id}", response_model=EmployeeResponse)
def update_employee(employee_id: str, payload: EmployeeInput, user: CurrentUser, db: Database) -> EmployeeResponse:
    workspace = _workspace(db, user)
    employee = _employee(db, workspace.id, employee_id, lock=True)
    values = payload.model_dump()
    values["employee_code"] = values["employee_code"].strip().upper()
    values["full_name"] = values["full_name"].strip()
    values["currency"] = _currency(values["currency"])
    if not values["employee_code"] or not values["full_name"]:
        raise HTTPException(422, "Employee code and name are required.")
    if values["pay_type"] == "monthly" and values["monthly_salary"] is None:
        raise HTTPException(422, "Monthly staff need a monthly salary.")
    if values["pay_type"] == "hourly" and values["hourly_rate"] is None:
        raise HTTPException(422, "Hourly staff need an hourly rate.")
    used_leave = _employee_response(db, employee, date.today().year).paid_leave_used_days
    if values["annual_paid_leave_days"] < used_leave:
        raise HTTPException(409, "Annual paid leave allowance cannot be lower than already approved days this year.")
    for key, value in values.items():
        setattr(employee, key, value)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "This employee code is already used.") from None
    db.refresh(employee)
    return _employee_response(db, employee, date.today().year)


@router.get("/leave", response_model=list[LeaveResponse])
def list_leave(user: CurrentUser, db: Database, employee_id: str | None = None) -> list[EmployeeLeave]:
    workspace = _workspace(db, user)
    query = select(EmployeeLeave).where(EmployeeLeave.workspace_id == workspace.id)
    if employee_id:
        _employee(db, workspace.id, employee_id)
        query = query.where(EmployeeLeave.employee_id == employee_id)
    return list(db.scalars(query.order_by(EmployeeLeave.start_date.desc(), EmployeeLeave.created_at.desc())))


@router.post("/leave", response_model=LeaveResponse, status_code=status.HTTP_201_CREATED)
def create_leave(payload: LeaveInput, user: CurrentUser, db: Database) -> EmployeeLeave:
    workspace = _workspace(db, user)
    employee = _employee(db, workspace.id, payload.employee_id)
    if not employee.active:
        raise HTTPException(409, "Inactive employees cannot receive new leave entries.")
    if payload.start_date > payload.end_date or payload.start_date.year != payload.end_date.year:
        raise HTTPException(422, "Leave dates must be ordered and within one calendar year.")
    if payload.days > (payload.end_date - payload.start_date).days + 1:
        raise HTTPException(422, "Leave days cannot exceed the calendar-day span.")
    overlap = db.scalar(select(EmployeeLeave.id).where(
        EmployeeLeave.workspace_id == workspace.id,
        EmployeeLeave.employee_id == employee.id,
        EmployeeLeave.status.in_(["pending", "approved"]),
        EmployeeLeave.start_date <= payload.end_date,
        EmployeeLeave.end_date >= payload.start_date,
    ))
    if overlap:
        raise HTTPException(409, "A pending or approved leave entry already overlaps these dates.")
    entry = EmployeeLeave(workspace_id=workspace.id, **payload.model_dump())
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


@router.post("/leave/{leave_id}/{decision}", response_model=LeaveResponse)
def decide_leave(leave_id: str, decision: str, user: CurrentUser, db: Database) -> EmployeeLeave:
    workspace = _workspace(db, user)
    if decision not in {"approve", "reject", "cancel"}:
        raise HTTPException(404, "Unknown leave action.")
    entry = db.scalar(select(EmployeeLeave).where(EmployeeLeave.id == leave_id, EmployeeLeave.workspace_id == workspace.id).with_for_update())
    if entry is None:
        raise HTTPException(404, "Leave entry not found.")
    if decision == "cancel":
        if entry.status not in {"pending", "approved"}:
            raise HTTPException(409, "Only pending or approved leave can be canceled.")
        if entry.status == "approved" and db.scalar(select(Payslip.id).where(
            Payslip.workspace_id == workspace.id,
            Payslip.employee_id == entry.employee_id,
            Payslip.status.in_(["finalized", "paid"]),
            Payslip.period_start <= entry.end_date,
            Payslip.period_end >= entry.start_date,
        )):
            raise HTTPException(409, "A finalized payslip overlaps this leave. Record a payroll correction separately before changing the leave approval.")
        entry.status = "canceled"
        db.commit()
        db.refresh(entry)
        return entry
    if entry.status != "pending":
        raise HTTPException(409, "Only pending leave can be reviewed.")
    if decision == "approve" and entry.leave_type == "paid":
        employee = _employee(db, workspace.id, entry.employee_id, lock=True)
        balance = _employee_response(db, employee, entry.start_date.year).paid_leave_remaining_days
        if entry.days > balance:
            raise HTTPException(409, "Paid leave exceeds the entered annual allowance. Update the employee policy before approval.")
    entry.status = "approved" if decision == "approve" else "rejected"
    db.commit()
    db.refresh(entry)
    return entry


@router.get("/reimbursements", response_model=list[ReimbursementResponse])
def list_reimbursements(user: CurrentUser, db: Database, employee_id: str | None = None) -> list[EmployeeReimbursement]:
    workspace = _workspace(db, user)
    query = select(EmployeeReimbursement).where(EmployeeReimbursement.workspace_id == workspace.id)
    if employee_id:
        _employee(db, workspace.id, employee_id)
        query = query.where(EmployeeReimbursement.employee_id == employee_id)
    return list(db.scalars(query.order_by(EmployeeReimbursement.expense_date.desc(), EmployeeReimbursement.created_at.desc())))


@router.post("/reimbursements", response_model=ReimbursementResponse, status_code=status.HTTP_201_CREATED)
def create_reimbursement(payload: ReimbursementInput, user: CurrentUser, db: Database) -> EmployeeReimbursement:
    workspace = _workspace(db, user)
    employee = _employee(db, workspace.id, payload.employee_id, lock=True)
    if not employee.active:
        raise HTTPException(409, "Inactive employees cannot receive new claims.")
    values = payload.model_dump()
    values["currency"] = _currency(values["currency"])
    if values["currency"] != employee.currency:
        raise HTTPException(422, "Reimbursement currency must match employee pay currency for this workflow.")
    if not values["category"].strip() or not values["description"].strip():
        raise HTTPException(422, "A category and business purpose are required.")
    claim = EmployeeReimbursement(workspace_id=workspace.id, **values)
    db.add(claim)
    db.commit()
    db.refresh(claim)
    return claim


@router.post("/reimbursements/{claim_id}/{decision}", response_model=ReimbursementResponse)
def decide_reimbursement(claim_id: str, decision: str, user: CurrentUser, db: Database) -> EmployeeReimbursement:
    workspace = _workspace(db, user)
    if decision not in {"approve", "reject", "cancel"}:
        raise HTTPException(404, "Unknown reimbursement action.")
    claim = db.scalar(select(EmployeeReimbursement).where(EmployeeReimbursement.id == claim_id, EmployeeReimbursement.workspace_id == workspace.id).with_for_update())
    if claim is None:
        raise HTTPException(404, "Claim not found.")
    if decision == "cancel":
        if claim.status not in {"pending", "approved"} or claim.payslip_id is not None:
            raise HTTPException(409, "Only unlinked pending or approved claims can be canceled.")
        claim.status = "canceled"
        db.commit()
        db.refresh(claim)
        return claim
    if claim.status != "pending":
        raise HTTPException(409, "Only pending claims can be reviewed.")
    claim.status = "approved" if decision == "approve" else "rejected"
    db.commit()
    db.refresh(claim)
    return claim


@router.post("/reimbursements/{claim_id}/mark-paid", response_model=ReimbursementResponse)
def mark_reimbursement_paid(claim_id: str, payload: ReimbursementPayment, user: CurrentUser, db: Database) -> EmployeeReimbursement:
    workspace = _workspace(db, user)
    claim = db.scalar(select(EmployeeReimbursement).where(
        EmployeeReimbursement.id == claim_id,
        EmployeeReimbursement.workspace_id == workspace.id,
    ).with_for_update())
    if claim is None:
        raise HTTPException(404, "Claim not found.")
    if claim.status != "approved" or claim.payslip_id is not None:
        raise HTTPException(409, "Only approved claims outside a payslip can be marked paid directly.")
    if claim.payroll_treatment == "inside_gross":
        raise HTTPException(409, "Claims classified inside gross pay must be included in a payslip before marking paid.")
    if payload.paid_at > date.today():
        raise HTTPException(422, "Paid date cannot be in the future.")
    claim.status = "paid"
    claim.paid_at = payload.paid_at
    claim.payment_reference = payload.payment_reference
    db.commit()
    db.refresh(claim)
    return claim


@router.get("/payslips", response_model=list[PayslipResponse])
def list_payslips(user: CurrentUser, db: Database, employee_id: str | None = None) -> list[Payslip]:
    workspace = _workspace(db, user)
    query = select(Payslip).where(Payslip.workspace_id == workspace.id)
    if employee_id:
        _employee(db, workspace.id, employee_id)
        query = query.where(Payslip.employee_id == employee_id)
    return list(db.scalars(query.order_by(Payslip.period_end.desc(), Payslip.created_at.desc())))


@router.post("/payslips", response_model=PayslipResponse, status_code=status.HTTP_201_CREATED)
def create_payslip(payload: PayslipInput, user: CurrentUser, db: Database) -> Payslip:
    workspace = _workspace(db, user)
    employee = _employee(db, workspace.id, payload.employee_id, lock=True)
    if not employee.active:
        raise HTTPException(409, "Inactive employees cannot receive new payslips.")
    if payload.period_start > payload.period_end:
        raise HTTPException(422, "Pay period start must be on or before its end.")
    if (payload.period_end - payload.period_start).days > 366:
        raise HTTPException(422, "A pay period cannot exceed 366 days.")
    if db.scalar(select(Payslip.id).where(
        Payslip.workspace_id == workspace.id,
        Payslip.employee_id == employee.id,
        Payslip.period_start <= payload.period_end,
        Payslip.period_end >= payload.period_start,
    )):
        raise HTTPException(409, "This employee already has a payslip overlapping that period.")
    if employee.pay_type == "hourly":
        if payload.regular_hours is None or payload.base_pay is not None:
            raise HTTPException(422, "Hourly staff need regular hours; base pay is calculated from their saved hourly rate.")
        base = (payload.regular_hours * employee.hourly_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    else:
        if payload.regular_hours is not None:
            raise HTTPException(422, "Monthly staff do not need regular hours.")
        base = payload.base_pay if payload.base_pay is not None else employee.monthly_salary
    if base is None:
        raise HTTPException(422, "Base pay is missing.")
    claim_ids = payload.reimbursement_ids
    if len(set(claim_ids)) != len(claim_ids):
        raise HTTPException(422, "A reimbursement claim cannot be added twice.")
    claims = list(db.scalars(select(EmployeeReimbursement).where(
        EmployeeReimbursement.id.in_(claim_ids),
        EmployeeReimbursement.workspace_id == workspace.id,
    ).with_for_update())) if claim_ids else []
    if len(claims) != len(claim_ids) or any(
        claim.employee_id != employee.id or claim.status != "approved" or claim.payslip_id is not None or claim.currency != employee.currency
        for claim in claims
    ):
        raise HTTPException(409, "Choose only this employee's approved, unused claims in the pay currency.")
    reimbursement_total = sum((claim.amount for claim in claims if claim.payroll_treatment == "outside_gross"), ZERO)
    taxable_claim_total = sum((claim.amount for claim in claims if claim.payroll_treatment == "inside_gross"), ZERO)
    gross = base + payload.overtime_pay + payload.bonus_pay + payload.allowances + taxable_claim_total - payload.leave_deduction
    if gross < 0 or payload.employee_tax + payload.other_deductions > gross:
        raise HTTPException(422, "Leave adjustment or deductions exceed gross pay.")
    net = gross - payload.employee_tax - payload.other_deductions
    slip = Payslip(
        workspace_id=workspace.id, employee_id=employee.id,
        employee_name=employee.full_name, employee_code=employee.employee_code,
        employee_email=employee.email,
        business_name=workspace.business_name, period_start=payload.period_start,
        period_end=payload.period_end, currency=employee.currency,
        regular_hours=payload.regular_hours,
        hourly_rate_snapshot=employee.hourly_rate if employee.pay_type == "hourly" else None,
        base_pay=base,
        overtime_pay=payload.overtime_pay, bonus_pay=payload.bonus_pay,
        allowances=payload.allowances, leave_deduction=payload.leave_deduction,
        employee_tax=payload.employee_tax, other_deductions=payload.other_deductions,
        employer_contributions=payload.employer_contributions,
        gross_pay=gross, net_pay=net, reimbursement_total=reimbursement_total,
        taxable_claim_total=taxable_claim_total,
        cash_to_employee=net + reimbursement_total,
        employer_cost=gross + payload.employer_contributions + reimbursement_total,
        reimbursement_lines=[{
            "id": claim.id, "expense_date": claim.expense_date.isoformat(),
            "category": claim.category, "description": claim.description,
            "amount": str(claim.amount), "receipt_reference": claim.receipt_reference,
            "payroll_treatment": claim.payroll_treatment,
        } for claim in claims],
        note=payload.note,
    )
    try:
        db.add(slip)
        db.flush()
        for claim in claims:
            claim.payslip_id = slip.id
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "A conflicting payslip or reimbursement was saved. Refresh and try again.") from None
    db.refresh(slip)
    return slip


@router.delete("/payslips/{payslip_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_draft_payslip(payslip_id: str, user: CurrentUser, db: Database) -> None:
    workspace = _workspace(db, user)
    slip = _payslip(db, workspace.id, payslip_id)
    if slip.status != "draft":
        raise HTTPException(409, "Only draft payslips can be deleted.")
    for claim in db.scalars(select(EmployeeReimbursement).where(EmployeeReimbursement.payslip_id == slip.id, EmployeeReimbursement.workspace_id == workspace.id)):
        claim.payslip_id = None
    db.delete(slip)
    db.commit()


@router.post("/payslips/{payslip_id}/finalize", response_model=PayslipResponse)
def finalize_payslip(payslip_id: str, payload: PayslipFinalize, user: CurrentUser, db: Database) -> Payslip:
    workspace = _workspace(db, user)
    slip = _payslip(db, workspace.id, payslip_id)
    if slip.status != "draft":
        raise HTTPException(409, "Only draft payslips can be finalized.")
    if not payload.reviewed_pay_leave_and_taxes:
        raise HTTPException(422, "Review pay, leave, and tax entries before finalizing.")
    slip.status = "finalized"
    slip.finalized_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(slip)
    return slip


@router.post("/payslips/{payslip_id}/mark-paid", response_model=PayslipResponse)
def mark_payslip_paid(payslip_id: str, payload: PayslipPayment, user: CurrentUser, db: Database) -> Payslip:
    workspace = _workspace(db, user)
    slip = _payslip(db, workspace.id, payslip_id)
    if slip.status != "finalized":
        raise HTTPException(409, "Only finalized, unpaid payslips can be marked paid.")
    if payload.paid_at > date.today():
        raise HTTPException(422, "Paid date cannot be in the future.")
    slip.status = "paid"
    slip.paid_at = payload.paid_at
    slip.payment_reference = payload.payment_reference
    for claim in db.scalars(select(EmployeeReimbursement).where(EmployeeReimbursement.payslip_id == slip.id, EmployeeReimbursement.workspace_id == workspace.id)):
        claim.status = "paid"
        claim.paid_at = payload.paid_at
        claim.payment_reference = payload.payment_reference
    db.commit()
    db.refresh(slip)
    return slip


@router.get("/payslips/{payslip_id}/pdf")
def download_payslip(payslip_id: str, user: CurrentUser, db: Database) -> StreamingResponse:
    workspace = _workspace(db, user)
    slip = _payslip(db, workspace.id, payslip_id)
    if slip.status == "draft":
        raise HTTPException(409, "Finalize and review this payslip before downloading it.")
    safe_code = re.sub(r"[^A-Za-z0-9_-]", "_", slip.employee_code)[:40]
    filename = f"payslip-{safe_code}-{slip.period_end.isoformat()}.pdf"
    return StreamingResponse(
        BytesIO(render_payslip(slip)), media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/taxes", response_model=list[GovernmentTaxResponse])
def list_taxes(user: CurrentUser, db: Database) -> list[GovernmentTax]:
    workspace = _workspace(db, user)
    return list(db.scalars(select(GovernmentTax).where(
        GovernmentTax.workspace_id == workspace.id,
    ).order_by(GovernmentTax.due_date.desc(), GovernmentTax.created_at.desc())))


@router.post("/taxes", response_model=GovernmentTaxResponse, status_code=status.HTTP_201_CREATED)
def create_tax(payload: GovernmentTaxInput, user: CurrentUser, db: Database) -> GovernmentTax:
    workspace = _workspace(db, user)
    if payload.period_start > payload.period_end:
        raise HTTPException(422, "Tax period start must be on or before its end.")
    values = payload.model_dump()
    values["tax_type"] = values["tax_type"].strip()
    values["jurisdiction"] = values["jurisdiction"].strip()
    values["currency"] = _currency(values["currency"])
    if not values["tax_type"] or not values["jurisdiction"]:
        raise HTTPException(422, "Tax type and jurisdiction are required.")
    tax = GovernmentTax(workspace_id=workspace.id, **values)
    db.add(tax)
    db.commit()
    db.refresh(tax)
    return tax


def _tax(db: Database, workspace_id: str, tax_id: str) -> GovernmentTax:
    tax = db.scalar(select(GovernmentTax).where(
        GovernmentTax.id == tax_id, GovernmentTax.workspace_id == workspace_id,
    ).with_for_update())
    if tax is None:
        raise HTTPException(404, "Tax record not found.")
    return tax


@router.delete("/taxes/{tax_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_draft_tax(tax_id: str, user: CurrentUser, db: Database) -> None:
    workspace = _workspace(db, user)
    tax = _tax(db, workspace.id, tax_id)
    if tax.status != "draft":
        raise HTTPException(409, "Only draft tax records can be removed.")
    db.delete(tax)
    db.commit()


@router.post("/taxes/{tax_id}/confirm", response_model=GovernmentTaxResponse)
def confirm_tax(tax_id: str, payload: GovernmentTaxConfirm, user: CurrentUser, db: Database) -> GovernmentTax:
    workspace = _workspace(db, user)
    tax = _tax(db, workspace.id, tax_id)
    if tax.status != "draft":
        raise HTTPException(409, "Only draft tax records can be confirmed.")
    if not payload.reviewed_local_tax_basis:
        raise HTTPException(422, "Review the local tax basis and amount before confirming.")
    tax.status = "confirmed"
    tax.confirmed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(tax)
    return tax


@router.post("/taxes/{tax_id}/mark-paid", response_model=GovernmentTaxResponse)
def mark_tax_paid(tax_id: str, payload: PayslipPayment, user: CurrentUser, db: Database) -> GovernmentTax:
    workspace = _workspace(db, user)
    tax = _tax(db, workspace.id, tax_id)
    if tax.status != "confirmed":
        raise HTTPException(409, "Only confirmed, unpaid tax records can be marked paid.")
    if payload.paid_at > date.today():
        raise HTTPException(422, "Paid date cannot be in the future.")
    tax.status = "paid"
    tax.paid_at = payload.paid_at
    tax.payment_reference = payload.payment_reference
    db.commit()
    db.refresh(tax)
    return tax
