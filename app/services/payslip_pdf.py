"""Generate a private employee payslip PDF from a finalized snapshot."""

from io import BytesIO
from decimal import Decimal

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.models.payroll import Payslip


def render_payslip(slip: Payslip) -> bytes:
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    def text(x: float, y: float, value: object, size: int = 10, bold: bool = False) -> None:
        pdf.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        # Standard PDF fonts cover Western text. Replace unsupported glyphs;
        # a bundled Unicode font is needed before this can serve every script.
        pdf.drawString(x, y, str(value).encode("latin-1", "replace").decode("latin-1")[:95])

    def money(value: object) -> str:
        return f"{slip.currency} {value:,.2f}"

    pdf.setFillColor(HexColor("#211b32"))
    pdf.rect(0, height - 112, width, 112, stroke=0, fill=1)
    pdf.setFillColor(HexColor("#ffffff"))
    text(44, height - 49, slip.business_name, 17, True)
    text(44, height - 76, "EMPLOYEE PAYSLIP", 12, True)
    pdf.setFillColor(HexColor("#24202c"))
    y = height - 145
    for label, value in (
        ("Employee", slip.employee_name),
        ("Employee code", slip.employee_code),
        ("Pay period", f"{slip.period_start.isoformat()} to {slip.period_end.isoformat()}"),
        ("Status", "Paid" if slip.status == "paid" else "Finalized; payment unconfirmed"),
    ):
        text(44, y, label, 10, True)
        text(205, y, value)
        y -= 24
    if slip.paid_at:
        text(44, y, "Recorded paid date", 10, True)
        text(205, y, slip.paid_at.isoformat())
        y -= 24
    if slip.regular_hours is not None and slip.hourly_rate_snapshot is not None:
        text(44, y, "Hourly basis", 10, True)
        text(205, y, f"{slip.regular_hours} hours x {money(slip.hourly_rate_snapshot)}")
        y -= 24
    y -= 12
    pdf.setStrokeColor(HexColor("#c9c4d3"))
    pdf.line(44, y, width - 44, y)
    y -= 27
    rows = (
        ("Base pay", slip.base_pay),
        ("Overtime", slip.overtime_pay),
        ("Bonus / commission", slip.bonus_pay),
        ("Allowances", slip.allowances),
        ("Expense payments in gross pay", slip.taxable_claim_total),
        ("Unpaid-leave adjustment", -slip.leave_deduction),
        ("Gross pay", slip.gross_pay),
        ("Employee tax withheld", -slip.employee_tax),
        ("Other deductions", -slip.other_deductions),
        ("Net salary", slip.net_pay),
        ("Expense payments outside gross", slip.reimbursement_total),
        ("Total to employee", slip.cash_to_employee),
        ("Employer contributions (separate)", slip.employer_contributions),
        ("Employer cost incl. reimbursements", slip.employer_cost),
    )
    for label, value in rows:
        strong = label in {"Gross pay", "Net salary", "Total to employee", "Employer cost incl. reimbursements"}
        text(44, y, label, 10, strong)
        text(390, y, money(value), 10, strong)
        y -= 25
    if slip.reimbursement_lines:
        y -= 13
        text(44, y, "REIMBURSEMENTS INCLUDED", 10, True)
        y -= 23
        for line in slip.reimbursement_lines:
            if y < 100:
                pdf.showPage()
                y = height - 55
                pdf.setFillColor(HexColor("#24202c"))
                text(44, y, f"{slip.employee_name} · expense payments continued", 10, True)
                y -= 28
            label = f"{line['expense_date']}  {line['category']}  {line['description']}"
            text(44, y, label[:58], 8)
            text(420, y, money(Decimal(line["amount"])), 8)
            y -= 19
    if slip.note:
        if y < 100:
            pdf.showPage()
            y = height - 55
            pdf.setFillColor(HexColor("#24202c"))
        y -= 12
        text(44, y, "Note", 9, True)
        y -= 18
        text(44, y, slip.note, 9)
    pdf.setFillColor(HexColor("#615b6d"))
    text(44, 54, "Prepared from employer-entered data. Tax and leave treatment must be reviewed locally.", 8)
    text(44, 38, "This payslip does not initiate a bank transfer or email delivery.", 8)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()
