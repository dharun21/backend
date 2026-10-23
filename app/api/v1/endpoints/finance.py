from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import delete, select

from app.api.dependencies import CurrentUser, Database
from app.core.config import ALLOWED_EXTENSIONS, MAX_UPLOAD_BYTES, UPLOAD_DIR
from app.models.finance import FinanceDocument, FinanceStatement, FinanceStatementTransaction, FinanceWorkspace
from app.models.onboarding import BusinessProfile
from app.models.records import FinanceRecord
from app.models.user import User
from app.services.notifications import create_notification
from app.services.entitlements import require_specialist_access
from app.services.document_processing import extract_invoice_records, extract_statement_data
from app.services.upload_validation import UnsafeUpload, validate_upload
from app.services.malware_scan import MalwareDetected, ScannerUnavailable, scan_upload
from app.schemas.finance import (
    DocumentProcessResponse,
    FinanceDocumentResponse,
    FinancePaymentUpdate,
    FinanceRecordCorrection,
    FinanceRecordResponse,
    FinanceReminderUpdate,
    FinanceStatementResponse,
    FinanceWorkspaceInput,
    FinanceWorkspaceResponse,
)

router = APIRouter(prefix="/finance", tags=["finance workspace"])
MIME_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def get_workspace(db: Database, user: User) -> FinanceWorkspace:
    require_specialist_access(db, user.id, "finance_analyst")
    workspace = db.scalar(select(FinanceWorkspace).where(FinanceWorkspace.owner_id == user.id))
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Finance workspace has not been set up.")
    return workspace


def records_in_workspace(workspace_id: str):
    return (
        select(FinanceRecord)
        .join(FinanceDocument, FinanceRecord.document_id == FinanceDocument.id)
        .where(
            FinanceRecord.workspace_id == workspace_id,
            FinanceDocument.workspace_id == workspace_id,
        )
    )


@router.get("/workspace", response_model=FinanceWorkspaceResponse)
def read_workspace(user: CurrentUser, db: Database) -> FinanceWorkspace:
    return get_workspace(db, user)


@router.put("/workspace", response_model=FinanceWorkspaceResponse)
def save_workspace(payload: FinanceWorkspaceInput, user: CurrentUser, db: Database) -> FinanceWorkspace:
    require_specialist_access(db, user.id, "finance_analyst")
    workspace = db.scalar(select(FinanceWorkspace).where(FinanceWorkspace.owner_id == user.id))
    values = payload.model_dump()
    if values["business_type"] is None:
        if workspace is not None:
            values["business_type"] = workspace.business_type
        else:
            profile = db.scalar(select(BusinessProfile).where(BusinessProfile.user_id == user.id))
            selected_type = (profile.business_type or "").strip().title() if profile else ""
            values["business_type"] = selected_type if selected_type in {"Retail", "Corporate"} else None
    if workspace is None:
        workspace = FinanceWorkspace(owner_id=user.id, **values)
        db.add(workspace)
    else:
        for key, value in values.items():
            setattr(workspace, key, value)
    if values["business_type"] in {"Retail", "Corporate"}:
        profile = db.scalar(select(BusinessProfile).where(BusinessProfile.user_id == user.id))
        if profile is None:
            profile = BusinessProfile(user_id=user.id)
            db.add(profile)
        profile.business_type = values["business_type"]
    db.commit()
    db.refresh(workspace)
    return workspace


@router.get("/documents", response_model=list[FinanceDocumentResponse])
def list_documents(user: CurrentUser, db: Database) -> list[FinanceDocument]:
    workspace = get_workspace(db, user)
    return list(db.scalars(select(FinanceDocument).where(FinanceDocument.workspace_id == workspace.id).order_by(FinanceDocument.created_at.desc())))


@router.get("/documents/{document_id}", response_model=FinanceDocumentResponse)
def read_document(document_id: str, user: CurrentUser, db: Database) -> FinanceDocument:
    workspace = get_workspace(db, user)
    document = db.scalar(
        select(FinanceDocument).where(
            FinanceDocument.id == document_id,
            FinanceDocument.workspace_id == workspace.id,
        )
    )
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    return document


@router.post("/documents", response_model=FinanceDocumentResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(file: UploadFile, user: CurrentUser, db: Database) -> FinanceDocument:
    workspace = get_workspace(db, user)
    original_name = Path((file.filename or "").replace("\\", "/")).name
    original_name = "".join(character for character in original_name if character.isprintable()).strip()
    extension = Path(original_name).suffix.lower()
    if not original_name or extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Choose a PDF, PNG, JPG, CSV, or XLSX file.")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    storage_name = f"{uuid4().hex}{extension}"
    destination = UPLOAD_DIR / storage_name
    size_bytes = 0
    try:
        with destination.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                size_bytes += len(chunk)
                if size_bytes > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="File exceeds the configured upload limit.")
                output.write(chunk)
        if size_bytes == 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The selected file is empty.")

        try:
            validate_upload(destination, extension)
        except UnsafeUpload as error:
            raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(error)) from error
        try:
            scan_upload(destination)
        except MalwareDetected as error:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
        except ScannerUnavailable as error:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error

        document = FinanceDocument(
            workspace_id=workspace.id,
            original_name=original_name[:255],
            storage_name=storage_name,
            extension=extension,
            content_type=MIME_TYPES[extension],
            size_bytes=size_bytes,
            status="awaiting_processing",
        )
        db.add(document)
        db.commit()
        db.refresh(document)
        return document
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


@router.get("/documents/{document_id}/download")
def download_document(document_id: str, user: CurrentUser, db: Database) -> FileResponse:
    workspace = get_workspace(db, user)
    document = db.scalar(select(FinanceDocument).where(FinanceDocument.id == document_id, FinanceDocument.workspace_id == workspace.id))
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    path = UPLOAD_DIR / document.storage_name
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stored file is missing.")
    return FileResponse(
        path,
        filename=document.original_name,
        media_type=MIME_TYPES.get(document.extension, "application/octet-stream"),
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"},
    )


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(document_id: str, user: CurrentUser, db: Database) -> None:
    workspace = get_workspace(db, user)
    document = db.scalar(select(FinanceDocument).where(FinanceDocument.id == document_id, FinanceDocument.workspace_id == workspace.id))
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    storage_path = UPLOAD_DIR / document.storage_name
    db.execute(delete(FinanceStatement).where(FinanceStatement.document_id == document.id, FinanceStatement.workspace_id == workspace.id))
    db.execute(
        delete(FinanceRecord).where(
            FinanceRecord.document_id == document.id,
            FinanceRecord.workspace_id == workspace.id,
        )
    )
    db.delete(document)
    db.commit()
    storage_path.unlink(missing_ok=True)


@router.get("/statements", response_model=list[FinanceStatementResponse])
def list_statements(user: CurrentUser, db: Database) -> list[FinanceStatementResponse]:
    workspace = get_workspace(db, user)
    rows = db.execute(
        select(FinanceStatement, FinanceDocument.original_name)
        .join(FinanceDocument, FinanceDocument.id == FinanceStatement.document_id)
        .where(FinanceStatement.workspace_id == workspace.id, FinanceDocument.workspace_id == workspace.id)
        .order_by(FinanceStatement.statement_date.desc().nullslast(), FinanceStatement.created_at.desc())
    ).all()
    statements = [
        FinanceStatementResponse(
            id=statement.id,
            document_id=statement.document_id,
            document_name=document_name,
            source_type=statement.source_type,
            institution_name=statement.institution_name,
            account_holder=statement.account_holder,
            account_number=statement.account_number,
            statement_date=statement.statement_date,
            period_start=statement.period_start,
            period_end=statement.period_end,
            currency=statement.currency,
            document_reference=statement.document_reference,
            transaction_count=len(statement.transactions or []),
            created_at=statement.created_at,
            status="saved",
        )
        for statement, document_name in rows
    ]
    saved_document_ids = {item.document_id for item in statements}
    legacy_rows = db.execute(
        select(FinanceRecord, FinanceDocument)
        .join(FinanceDocument, FinanceDocument.id == FinanceRecord.document_id)
        .where(
            FinanceRecord.workspace_id == workspace.id,
            FinanceDocument.workspace_id == workspace.id,
            FinanceRecord.status == "unsupported",
        )
        .order_by(FinanceDocument.created_at.desc())
    ).all()
    for record, document in legacy_rows:
        if document.id in saved_document_ids:
            continue
        # Older statement uploads may have been marked unsupported before the
        # dedicated statement tables existed. Keep them visible until rechecked.
        statements.append(FinanceStatementResponse(
            id=f"legacy-{document.id}",
            document_id=document.id,
            document_name=document.original_name,
            source_type="statement_or_unsupported",
            institution_name=None,
            account_holder=None,
            account_number=None,
            statement_date=record.issue_date,
            period_start=None,
            period_end=None,
            currency=record.currency,
            document_reference=record.reference_number,
            transaction_count=0,
            created_at=document.created_at,
            status="needs_recheck",
        ))
        saved_document_ids.add(document.id)
    return statements

@router.post("/documents/{document_id}/process", response_model=DocumentProcessResponse)
def process_document(document_id: str, user: CurrentUser, db: Database) -> DocumentProcessResponse:
    workspace = get_workspace(db, user)
    document = db.scalar(
        select(FinanceDocument).where(
            FinanceDocument.id == document_id,
            FinanceDocument.workspace_id == workspace.id,
        )
    )
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    if document.extension != ".pdf":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="The first processor handles text-based PDF invoices. CSV and spreadsheet processing will be added next.",
        )

    existing_records = list(db.scalars(records_in_workspace(workspace.id).where(FinanceRecord.document_id == document.id)))
    path = UPLOAD_DIR / document.storage_name
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stored file is missing.")
    try:
        extracted_records = extract_invoice_records(path, business_name=workspace.business_name)
        statement_data = extract_statement_data(path)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="This PDF could not be read. Check that it is a valid, unprotected PDF.") from error

    # Statements have their own durable source table. They never become finance
    # records and their transaction rows are excluded from current totals.
    finance_extractions = [
        (fields, warnings) for fields, warnings in extracted_records
        if fields.get("status_suggestion") != "unsupported"
    ]
    legacy_statement_records = [
        record for record in existing_records
        if record.status == "unsupported"
        and (
            statement_data is not None
            or any(note.startswith("Recognized as a bank/account statement") for note in (record.review_notes or []))
        )
    ]
    for legacy_record in legacy_statement_records:
        db.delete(legacy_record)
    existing_records = [record for record in existing_records if record not in legacy_statement_records]

    if statement_data:
        statement = db.scalar(select(FinanceStatement).where(
            FinanceStatement.document_id == document.id,
            FinanceStatement.workspace_id == workspace.id,
        ))
        if statement is None:
            statement = FinanceStatement(workspace_id=workspace.id, document_id=document.id)
            db.add(statement)
        transaction_rows = statement_data.pop("transactions", [])
        for key, value in statement_data.items():
            setattr(statement, key, value)
        statement.transactions = [
            FinanceStatementTransaction(
                source_type="statement",
                transaction_type=row.get("transaction_type", "unknown"),
                transaction_date=date.fromisoformat(row["transaction_date"]) if row.get("transaction_date") else None,
                description=row.get("description", "")[:500],
                reference=row.get("reference"),
                debit=Decimal(row["debit"]) if row.get("debit") is not None else None,
                credit=Decimal(row["credit"]) if row.get("credit") is not None else None,
                balance=Decimal(row["balance"]) if row.get("balance") is not None else None,
                currency=row.get("currency"),
            )
            for row in transaction_rows
        ]
    if not finance_extractions and not statement_data:
        warnings = extracted_records[0][1] if extracted_records else ["No invoice text could be extracted."]
        if existing_records:
            return DocumentProcessResponse(
                document_id=document.id,
                status=document.status,
                records=[FinanceRecordResponse.model_validate(record) for record in existing_records],
                warnings=[*warnings, "The existing review record was kept unchanged."],
            )
        document.status = "ocr_required"
        create_notification(db, user.id, "Document needs another look", f"{document.original_name} could not be read automatically. Review the file or provide a clearer copy.", "/ai/finance")
        db.commit()
        return DocumentProcessResponse(document_id=document.id, status=document.status, records=[], warnings=warnings)

    # Reprocessing is idempotent by reference number. Confirmed and unsupported
    # records are kept exactly as reviewed; only still-pending suggestions update.
    existing_by_reference = {record.reference_number: record for record in existing_records if record.reference_number}
    records = list(existing_records)
    matched_existing_ids = set()
    all_warnings = []
    changed_review_records = 0
    for fields, warnings in finance_extractions:
        suggested_type = fields.pop("record_type", "unclassified")
        suggested_status = fields.pop("status_suggestion", "needs_review")
        if suggested_status == "unsupported":
            classification_note = "Recognized as a bank/account statement. Kept for reference and excluded from finance totals."
        elif suggested_type in ("invoice", "expense"):
            classification_note = "Review the suggested classification before confirming; suggestions do not enter totals until confirmed."
        else:
            classification_note = "Choose whether this is a customer invoice (money owed to you) or a supplier bill/expense (money you owe); the document was ambiguous."

        reference = fields.get("reference_number")
        record = existing_by_reference.get(reference) if reference else next((
            item for item in existing_records
            if item.id not in matched_existing_ids
            and item.reference_number is None
            and item.status == suggested_status
        ), None)
        if record and record.status in ("confirmed", "unsupported"):
            matched_existing_ids.add(record.id)
            all_warnings.extend(warnings)
            continue
        if record:
            matched_existing_ids.add(record.id)
            for key, value in fields.items():
                setattr(record, key, value)
            record.record_type = suggested_type
            record.status = suggested_status
            record.review_notes = list(dict.fromkeys([
                *[
                    note for note in (record.review_notes or [])
                    if not note.startswith((
                        "Classify this record during review:",
                        "Choose whether this is a customer invoice",
                        "Review the suggested classification before confirming;",
                        "Recognized as a bank/account statement.",
                        "Suggested as ",
                        "The document does not clearly show whether this is money owed to you",
                    ))
                ],
                *warnings,
                classification_note,
            ]))
        else:
            record = FinanceRecord(
                workspace_id=workspace.id,
                document_id=document.id,
                record_type=suggested_type,
                status=suggested_status,
                review_notes=[
                    *warnings,
                    classification_note,
                ],
                subtotal=None,
                tax_total=None,
                **fields,
            )
            db.add(record)
            records.append(record)
        if record.status == "needs_review":
            changed_review_records += 1
        all_warnings.extend(warnings)
    db.flush()
    if any(record.status == "needs_review" for record in records):
        document.status = "needs_review"
    elif statement_data:
        document.status = "statement_saved"
    else:
        document.status = "confirmed"
    if changed_review_records:
        create_notification(
            db,
            user.id,
            "Finance details need your review",
            f"{changed_review_records} finance record(s) from {document.original_name} are ready for review.",
            "/ai/finance",
        )
    db.commit()
    for record in records:
        db.refresh(record)
    return DocumentProcessResponse(
        document_id=document.id,
        status=document.status,
        records=[FinanceRecordResponse.model_validate(record) for record in records],
        warnings=list(dict.fromkeys(all_warnings)),
    )


@router.get("/records", response_model=list[FinanceRecordResponse])
def list_finance_records(user: CurrentUser, db: Database) -> list[FinanceRecord]:
    workspace = get_workspace(db, user)
    return list(
        db.scalars(
            records_in_workspace(workspace.id)
            .order_by(FinanceRecord.created_at.desc())
        )
    )


@router.get("/records/{record_id}", response_model=FinanceRecordResponse)
def read_finance_record(record_id: str, user: CurrentUser, db: Database) -> FinanceRecord:
    workspace = get_workspace(db, user)
    record = db.scalar(
        records_in_workspace(workspace.id).where(FinanceRecord.id == record_id)
    )
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Finance record not found.")
    return record


@router.patch("/records/{record_id}/payment", response_model=FinanceRecordResponse)
def update_record_payment(
    record_id: str,
    payload: FinancePaymentUpdate,
    user: CurrentUser,
    db: Database,
) -> FinanceRecord:
    workspace = get_workspace(db, user)
    record = db.scalar(records_in_workspace(workspace.id).where(FinanceRecord.id == record_id))
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Finance record not found.")
    if record.status != "confirmed" or record.record_type not in ("invoice", "expense"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only confirmed invoices and bills can have a payment status.")
    if (payload.payment_status is None) == (payload.amount_paid is None):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Provide either payment_status or cumulative amount_paid.")
    if record.total_amount is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Confirm the invoice or bill total before recording payment.")
    total = Decimal(str(record.total_amount))
    amount_paid = (total if payload.payment_status == "paid" else Decimal("0.00")) if payload.payment_status else payload.amount_paid
    assert amount_paid is not None
    if amount_paid > total:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Recorded paid amount cannot exceed the invoice or bill total.")
    previous_status = record.payment_status
    record.amount_paid = amount_paid
    record.payment_status = "paid" if amount_paid == total else "partial" if amount_paid > 0 else "unpaid"
    if record.payment_status == "paid":
        record.paid_at = (record.paid_at or date.today()) if previous_status == "paid" else date.today()
    else:
        record.paid_at = None
    if record.payment_status == "paid":
        record.reminder_date = None
    db.commit()
    db.refresh(record)
    return record


@router.patch("/records/{record_id}/reminder", response_model=FinanceRecordResponse)
def update_record_reminder(
    record_id: str,
    payload: FinanceReminderUpdate,
    user: CurrentUser,
    db: Database,
) -> FinanceRecord:
    workspace = get_workspace(db, user)
    record = db.scalar(records_in_workspace(workspace.id).where(FinanceRecord.id == record_id))
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Finance record not found.")
    if record.status != "confirmed" or record.record_type != "expense" or record.payment_status == "paid":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Reminders are available for confirmed unpaid bills only.")
    if payload.reminder_date and payload.reminder_date < date.today():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Choose today or a future reminder date.")
    record.reminder_date = payload.reminder_date
    db.commit()
    db.refresh(record)
    return record


@router.patch("/records/{record_id}/confirm", response_model=FinanceRecordResponse)
def confirm_finance_record(
    record_id: str,
    corrections: FinanceRecordCorrection,
    user: CurrentUser,
    db: Database,
) -> FinanceRecord:
    workspace = get_workspace(db, user)
    record = db.scalar(
        records_in_workspace(workspace.id).where(FinanceRecord.id == record_id)
    )
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Finance record not found.")
    data = corrections.model_dump(exclude_unset=True)
    record_type = data.pop("record_type", None)
    if record_type not in ("invoice", "expense"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Classify this record before confirming it: a customer invoice (money owed to you) or a supplier bill/expense (money you owe).",
        )
    recorded_paid = Decimal(str(record.amount_paid or 0))
    revised_total = data.get("total_amount", record.total_amount)
    if recorded_paid > 0 and revised_total is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Keep a total amount while recorded payments exist. Correct the payment first.")
    if revised_total is not None and recorded_paid > Decimal(str(revised_total)):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The corrected total cannot be less than the recorded paid amount. Correct the payment first.")
    if recorded_paid > 0 and "currency" in data and (data["currency"] or "").upper() != (record.currency or workspace.currency or "").upper():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Correct the recorded payment before changing the currency.")
    for field, value in data.items():
        setattr(record, field, value)
    if record.total_amount is not None and record.amount_paid:
        fully_paid = Decimal(str(record.amount_paid)) == Decimal(str(record.total_amount))
        record.payment_status = "paid" if fully_paid else "partial"
        record.paid_at = (record.paid_at or date.today()) if fully_paid else None
    record.record_type = record_type
    record.status = "confirmed"
    record.review_notes = []
    document_records = list(db.scalars(select(FinanceRecord).where(FinanceRecord.document_id == record.document_id)))
    if any(item.status == "needs_review" for item in document_records):
        record.document.status = "needs_review"
    elif document_records and all(item.status == "unsupported" for item in document_records):
        record.document.status = "unsupported"
    else:
        record.document.status = "confirmed"
    db.commit()
    db.refresh(record)
    return record


@router.patch("/records/{record_id}/mark-unsupported", response_model=FinanceRecordResponse)
def mark_record_unsupported(
    record_id: str,
    user: CurrentUser,
    db: Database,
) -> FinanceRecord:
    """Mark a record as a statement/unsupported document, not income or expense.

    For uploads that are neither a customer invoice nor a supplier bill (bank or
    loan statements, exports, letters). The file and extracted data are kept,
    but the record stays out of finance totals and due lists (intelligence only
    reads ``confirmed`` records). The document is marked ``unsupported`` only
    when every record on it is unsupported; otherwise it stays ``needs_review``.
    """
    workspace = get_workspace(db, user)
    record = db.scalar(
        records_in_workspace(workspace.id).where(FinanceRecord.id == record_id)
    )
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Finance record not found.")
    if record.status != "needs_review":
        # Only unreviewed records can be set aside. A confirmed record must keep
        # counting in its totals until it is corrected through confirmation, so
        # it can never be silently removed from income, expense, or due lists.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only records awaiting review can be marked as a statement/unsupported document.",
        )
    record.status = "unsupported"
    record.review_notes = [
        *(record.review_notes or []),
        "Marked as a statement/unsupported document: kept for reference, excluded from income, expense, and due totals.",
    ]
    siblings = list(
        db.scalars(
            select(FinanceRecord).where(FinanceRecord.document_id == record.document_id)
        )
    )
    if any(s.status == "needs_review" for s in siblings):
        record.document.status = "needs_review"
    elif siblings and all(s.status == "unsupported" for s in siblings):
        record.document.status = "unsupported"
    else:
        record.document.status = "confirmed"
    db.commit()
    db.refresh(record)
    return record
