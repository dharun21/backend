"""Flag legacy finance records whose type was set automatically during extraction.

Before explicit classification existed, document processing saved every
extracted record as ``record_type="invoice"``. A real supplier bill stored that
way silently counted as customer income. This script flags those legacy
records for re-review without guessing their true type and without deleting or
editing any extracted data (amounts, dates, counterparties, and references are
left untouched):

* ``needs_review`` records still typed ``invoice`` are reset to
  ``unclassified``. New extractions never carry ``invoice`` (they start
  ``unclassified``), so any such record is a leftover extraction default.
* ``confirmed`` records typed ``invoice`` whose last update (confirmation
  time, via the ``updated_at`` onupdate hook) falls before
  ``--confirmed-before`` are moved back to ``needs_review`` and reset to
  ``unclassified``. They drop out of income totals until a reviewer
  classifies them explicitly. The cutoff is required so records confirmed
  after the classification flow shipped (explicitly classified) are never
  touched. Records confirmed after the cutoff are reported and skipped.

Nothing is modified unless ``--apply`` is passed; the default run is a dry
report. The script is idempotent: flagged records no longer match its queries.

Typical use (dry run first, then apply with the deployment time of the
explicit-classification release)::

    python scripts/flag_legacy_invoice_records.py
    python scripts/flag_legacy_invoice_records.py --apply --confirmed-before 2026-10-20T00:00:00
"""

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import models  # noqa: E402,F401
from app.db import SessionLocal, engine  # noqa: E402
from app.models.records import FinanceRecord  # noqa: E402

LEGACY_NOTE = (
    "Flagged for re-review: this record's type was set automatically during "
    "extraction (it defaulted to 'invoice') before explicit classification "
    "existed. Its amount and dates are unchanged. Please confirm whether this "
    "is a customer invoice (money owed to you) or a supplier bill/expense "
    "(money you owe) before relying on income totals."
)


def _as_utc(value: datetime) -> datetime:
    """Normalize naive datetimes (SQLite returns them) to UTC for comparison."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _describe(record: FinanceRecord, document_name: str) -> str:
    amount = f"{record.total_amount} {record.currency or ''}".strip()
    when = _as_utc(record.updated_at).isoformat() if record.updated_at else "unknown"
    return (
        f"  - {record.id} | {record.status:12} | {amount or 'no amount':>16}"
        f" | {record.counterparty or 'Unknown'} | ref {record.reference_number or '-'}"
        f" | last updated {when} | {document_name}"
    )


def _document_name(record: FinanceRecord) -> str:
    return record.document.original_name if record.document else "(no document)"


def _append_note(record: FinanceRecord) -> None:
    # Append, never replace: needs_review records still carry their extraction
    # warnings, and no history should be discarded.
    record.review_notes = [*(record.review_notes or []), LEGACY_NOTE]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--confirmed-before",
        metavar="ISO8601",
        help=(
            "Only flag confirmed 'invoice' records last updated before this "
            "timestamp (the deployment time of the explicit-classification "
            "release). Naive timestamps are treated as UTC. Without this "
            "option, confirmed records are reported but never modified."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the changes. Without this flag the script is a dry run.",
    )
    args = parser.parse_args()

    cutoff = None
    if args.confirmed_before:
        try:
            cutoff = datetime.fromisoformat(args.confirmed_before)
        except ValueError:
            parser.error(
                f"--confirmed-before {args.confirmed_before!r} is not a valid ISO8601 timestamp"
            )
        cutoff = _as_utc(cutoff)

    print(f"Target database: {engine.url.render_as_string(hide_password=True)}")

    with SessionLocal() as db:
        unreviewed = list(
            db.scalars(
                select(FinanceRecord).where(
                    FinanceRecord.record_type == "invoice",
                    FinanceRecord.status == "needs_review",
                )
            )
        )
        all_confirmed = list(
            db.scalars(
                select(FinanceRecord).where(
                    FinanceRecord.record_type == "invoice",
                    FinanceRecord.status == "confirmed",
                )
            )
        )
        confirmed_legacy = []
        confirmed_explicit = []
        for record in all_confirmed:
            updated = _as_utc(record.updated_at) if record.updated_at else None
            if cutoff is not None and updated is not None and updated < cutoff:
                confirmed_legacy.append(record)
            else:
                confirmed_explicit.append(record)

        print()
        print(f"Legacy records still awaiting review (needs_review + invoice): {len(unreviewed)}")
        for record in unreviewed:
            print(_describe(record, _document_name(record)))
        if unreviewed:
            print(
                "  -> with --apply: record_type reset to 'unclassified', "
                "review note appended."
            )

        print()
        print(f"Confirmed records typed 'invoice': {len(all_confirmed)}")
        if cutoff is None:
            print(
                "  No --confirmed-before cutoff given: confirmed records are listed "
                "but never modified. Re-run with --confirmed-before <ISO8601> set to "
                "the deployment time of the explicit-classification release so only "
                "legacy auto-typed records are flagged."
            )
        else:
            print(
                f"  Confirmed before {cutoff.isoformat()} (legacy, will be flagged): "
                f"{len(confirmed_legacy)}"
            )
            for record in confirmed_legacy:
                print(_describe(record, _document_name(record)))
            if confirmed_legacy:
                print(
                    "  -> with --apply: status reset to needs_review, record_type to "
                    "'unclassified', review note appended, document marked "
                    "needs_review. They leave income totals until re-confirmed with "
                    "an explicit classification."
                )
            print(
                f"  Confirmed at/after the cutoff (explicitly classified, untouched): "
                f"{len(confirmed_explicit)}"
            )

        if not args.apply:
            print()
            print(
                "Dry run only: nothing was modified. Re-run with --apply to flag "
                "these records."
            )
            return 0

        for record in unreviewed:
            record.record_type = "unclassified"
            _append_note(record)
        for record in confirmed_legacy:
            record.record_type = "unclassified"
            record.status = "needs_review"
            _append_note(record)
            if record.document is not None:
                record.document.status = "needs_review"
        db.commit()

        print()
        print(
            f"Flagged {len(unreviewed)} unreviewed and {len(confirmed_legacy)} "
            "confirmed record(s) for classification review. No amounts, dates, "
            "counterparties, references, or documents were altered or deleted."
        )
        print(
            "Next: open Finance Analyst -> Finance Overview -> 'Review extracted "
            "details', classify each flagged record (customer invoice or supplier "
            "bill/expense), and confirm it. Income and expense totals only include "
            "explicitly classified confirmed records."
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())