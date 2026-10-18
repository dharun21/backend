from __future__ import annotations

import csv
import zipfile
from pathlib import Path


class UnsafeUpload(ValueError):
    """Raised when file bytes do not match an allowed, expected format."""


def validate_upload(path: Path, extension: str) -> None:
    """Validate the file signature and basic container structure after upload.

    This is format validation, not antivirus scanning. Uploaded files are kept
    outside the web root and served only through authenticated API endpoints.
    """
    with path.open("rb") as stream:
        header = stream.read(8192)

    if extension == ".pdf":
        if b"%PDF-" not in header[:1024]:
            raise UnsafeUpload("The file contents do not look like a valid PDF.")
        return
    if extension == ".png":
        if not header.startswith(b"\x89PNG\r\n\x1a\n"):
            raise UnsafeUpload("The file contents do not match a PNG image.")
        return
    if extension in {".jpg", ".jpeg"}:
        if not header.startswith(b"\xff\xd8\xff"):
            raise UnsafeUpload("The file contents do not match a JPEG image.")
        return
    if extension == ".xlsx":
        if not zipfile.is_zipfile(path):
            raise UnsafeUpload("The file contents do not match an Excel workbook.")
        try:
            with zipfile.ZipFile(path) as archive:
                members = archive.infolist()
                names = {member.filename for member in members}
                if len(members) > 1000 or "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
                    raise UnsafeUpload("The Excel workbook structure is invalid.")
                expanded_size = sum(member.file_size for member in members)
                if expanded_size > 100 * 1024 * 1024 or any(member.file_size > 50 * 1024 * 1024 for member in members):
                    raise UnsafeUpload("The Excel workbook expands beyond the safe processing limit.")
                if any(".." in Path(member.filename).parts for member in members):
                    raise UnsafeUpload("The Excel workbook contains an unsafe path.")
                if any(name.lower().endswith("vbaproject.bin") for name in names):
                    raise UnsafeUpload("Macro-enabled workbooks are not accepted.")
        except zipfile.BadZipFile as error:
            raise UnsafeUpload("The Excel workbook archive is damaged.") from error
        return
    if extension == ".csv":
        if b"\x00" in header:
            raise UnsafeUpload("The CSV contains binary data.")
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                sample = stream.read(8192)
            if sample.strip():
                next(csv.reader([sample.splitlines()[0]]), None)
        except (UnicodeDecodeError, csv.Error) as error:
            raise UnsafeUpload("The file contents do not look like a readable CSV.") from error
        return
    raise UnsafeUpload("This file type is not supported.")

