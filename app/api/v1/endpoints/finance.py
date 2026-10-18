from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import delete, select

from app.api.dependencies import CurrentUser, Database
from app.core.config import ALLOWED_EXTENSIONS, MAX_UPLOAD_BYTES, UPLOAD_DIR
from app.models.finance import FinanceDocument, FinanceWorkspace
from app.models.records import FinanceRecord
from app.models.user import User
from app.services.notifications import create_notification
from app.services.document_processing import extract_invoice_fields
from app.services.upload_validation import UnsafeUpload, validate_upload
from app.services.malware_scan import MalwareDetected, ScannerUnavailable, scan_upload
from app.schemas.finance import (
    DocumentProcessResponse,
    FinanceDocumentResponse,
    FinanceRecordCorrection,
    FinanceRecordResponse,
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
    workspace = db.scalar(select(FinanceWorkspace).where(FinanceWorkspace.owner_id == user.id))
    if workspace is None:
        workspace = FinanceWorkspace(owner_id=user.id, **payload.model_dump())
        db.add(workspace)
    else:
        for key, value in payload.model_dump().items():
            setattr(workspace, key, value)
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
    db.execute(
        delete(FinanceRecord).where(
            FinanceRecord.document_id == document.id,
            FinanceRecord.workspace_id == workspace.id,
        )
    )
    db.delete(document)
    db.commit()
    storage_path.unlink(missing_ok=True)


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
    if existing_records:
        return DocumentProcessResponse(
            document_id=document.id,
            status=document.status,
            records=[FinanceRecordResponse.model_validate(record) for record in existing_records],
            warnings=["This document was already processed; existing review records were returned."],
        )

    path = UPLOAD_DIR / document.storage_name
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stored file is missing.")
    try:
        fields, warnings = extract_invoice_fields(path)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="This PDF could not be read. Check that it is a valid, unprotected PDF.") from error

    if not fields:
        document.status = "ocr_required"
        create_notification(db, user.id, "Document needs another look", f"{document.original_name} could not be read automatically. Review the file or provide a clearer copy.", "/ai/finance")
        db.commit()
        return DocumentProcessResponse(document_id=document.id, status=document.status, records=[], warnings=warnings)

    record = FinanceRecord(
        workspace_id=workspace.id,
        document_id=document.id,
        record_type="invoice",
        status="needs_review",
        review_notes=warnings,
        subtotal=None,
        tax_total=None,
        **fields,
    )
    db.add(record)
    db.flush()
    document.status = "needs_review"
    create_notification(db, user.id, "Finance details need your review", f"Check the suggested details extracted from {document.original_name} before confirming them.", "/ai/finance")
    db.commit()
    db.refresh(record)
    return DocumentProcessResponse(
        document_id=document.id,
        status=document.status,
        records=[FinanceRecordResponse.model_validate(record)],
        warnings=warnings,
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
    for field, value in corrections.model_dump(exclude_unset=True).items():
        setattr(record, field, value)
    record.status = "confirmed"
    record.review_notes = []
    record.document.status = "confirmed"
    db.commit()
    db.refresh(record)
    return record
