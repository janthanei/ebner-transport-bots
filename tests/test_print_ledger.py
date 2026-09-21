import sqlite3
from pathlib import Path

from email_invoice_bot.print_ledger import PrintLedger


def test_print_ledger_migrates_existing_database_for_email_links(tmp_path: Path):
    database = tmp_path / "print_history.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE print_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                printnode_job_id INTEGER UNIQUE,
                file_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                status TEXT NOT NULL,
                submitted_utc TEXT NOT NULL,
                updated_utc TEXT NOT NULL
            )
            """
        )

    PrintLedger(database)

    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(print_jobs)")}
    assert "email_web_url" in columns


def test_print_ledger_records_and_updates_job(tmp_path: Path):
    ledger = PrintLedger(tmp_path / "state" / "print_history.sqlite3")
    pending = tmp_path / "druck_ausstehend" / "invoice.pdf"

    ledger.record_submission(
        job_id=123,
        file_path=pending,
        printer_id=456,
        email_uid="mail-1",
        email_subject="Invoice 1",
        email_web_url="https://outlook.office.com/mail/deeplink/read/message-1",
        submitted_utc="2026-09-21T08:00:00+00:00",
    )
    ledger.update_status(
        123,
        "done",
        file_path=tmp_path / "druck_erfolg" / "invoice.pdf",
    )

    job = ledger.get_job(123)
    assert job is not None
    assert job["status"] == "done"
    assert job["email_subject"] == "Invoice 1"
    assert job["email_web_url"] == "https://outlook.office.com/mail/deeplink/read/message-1"
    assert job["resolved_utc"] is not None
    assert ledger.summary(
        "2026-09-21T00:00:00+00:00",
        "2026-09-22T00:00:00+00:00",
    ) == {"done": 1, "total": 1}


def test_print_ledger_tracks_unresolved_submission_failure(tmp_path: Path):
    ledger = PrintLedger(tmp_path / "print_history.sqlite3")
    failed = tmp_path / "druck_fehler" / "2026-09-21" / "broken.pdf"

    ledger.record_submission_failure(
        file_path=failed,
        printer_id=456,
        error_message="SMTP is unrelated",
        email_uid="mail-2",
        email_subject="Broken invoice",
    )

    errors = ledger.unresolved_errors()
    assert len(errors) == 1
    assert errors[0]["file_name"] == "broken.pdf"
    assert errors[0]["status"] == "error"
    assert errors[0]["error_message"] == "SMTP is unrelated"


def test_import_printnode_history_is_idempotent_and_suppresses_old_errors(tmp_path: Path):
    ledger = PrintLedger(tmp_path / "print_history.sqlite3")
    jobs = [
        {
            "id": 100,
            "title": "invoice.pdf",
            "state": "done",
            "createTimestamp": "2026-09-20T10:00:00Z",
            "printer": {"id": 456},
        },
        {
            "id": 101,
            "title": "broken.pdf",
            "state": "error",
            "createTimestamp": "2026-09-20T11:00:00Z",
            "printer": {"id": 456},
        },
    ]

    assert ledger.import_printnode_history(jobs) == 2
    assert ledger.import_printnode_history(jobs) == 0
    assert ledger.get_job(100)["status"] == "done"
    assert ledger.get_job(101)["notified_utc"] == "2026-09-20T11:00:00Z"
    assert ledger.unnotified_errors() == []
