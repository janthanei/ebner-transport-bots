from datetime import datetime, timezone
from pathlib import Path

from email_invoice_bot.notifications import PrintNotificationService
from email_invoice_bot.print_ledger import PrintLedger


class StubSmtp:
    messages = []

    def __init__(self, host, port, timeout):
        self.host = host
        self.port = port

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def ehlo(self):
        return None

    def starttls(self, context):
        return None

    def login(self, username, password):
        return None

    def send_message(self, message):
        self.messages.append(message)


def _service() -> PrintNotificationService:
    return PrintNotificationService(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_username="user",
        smtp_password="secret",
        smtp_starttls=False,
        from_email="print@example.com",
        from_name="Print Service",
        recipients=["christian@example.com"],
        cc=["jan@example.com"],
        weekly_recipients=["jan@example.com"],
        weekly_cc=[],
        error_share_path=r"\\server\Rechnungen\druck_fehler",
        report_timezone="Europe/Berlin",
        weekly_weekday=0,
        weekly_hour=8,
        smtp_factory=StubSmtp,
    )


def test_sends_each_failure_only_once(tmp_path: Path):
    StubSmtp.messages = []
    ledger = PrintLedger(tmp_path / "history.sqlite3")
    failed = tmp_path / "broken.pdf"
    ledger.record_submission(
        job_id=123,
        file_path=failed,
        printer_id=456,
        email_subject="Invoice 1",
        email_web_url="https://outlook.office.com/mail/deeplink/read/message-1",
        retry_count=1,
        submitted_utc="2026-09-20T10:00:00+00:00",
    )
    ledger.update_status(123, "error", error_message="renderer failed")

    service = _service()
    service.send_unnotified_errors(ledger)
    service.send_unnotified_errors(ledger)

    assert len(StubSmtp.messages) == 1
    message = StubSmtp.messages[0]
    assert message["To"] == "christian@example.com"
    assert message["Cc"] == "jan@example.com"
    plain = message.get_body(preferencelist=("plain",)).get_content()
    html = message.get_body(preferencelist=("html",)).get_content()
    assert "automatische zweite Druckversuch" in plain
    assert "PrintNode" not in plain
    assert "Originale E-Mail öffnen" in html
    assert "https://outlook.office.com/mail/deeplink/read/message-1" in html


def test_weekly_report_is_idempotent(tmp_path: Path):
    StubSmtp.messages = []
    ledger = PrintLedger(tmp_path / "history.sqlite3")
    ledger.record_submission(
        job_id=123,
        file_path=tmp_path / "invoice.pdf",
        printer_id=456,
        submitted_utc="2026-09-15T10:00:00+00:00",
    )
    ledger.update_status(123, "done")
    service = _service()
    now = datetime(2026, 9, 21, 6, 30, tzinfo=timezone.utc)

    assert service.maybe_send_weekly_report(ledger, now_utc=now)
    assert not service.maybe_send_weekly_report(ledger, now_utc=now)

    assert len(StubSmtp.messages) == 1
    message = StubSmtp.messages[0]
    assert message["To"] == "jan@example.com"
    assert message["Cc"] is None
    plain = message.get_body(preferencelist=("plain",)).get_content()
    assert "Verarbeitete Dokumente: 1" in plain
    assert "14.09.2026 bis 20.09.2026" in plain
    assert "Davon beim zweiten Versuch" not in plain
    assert "Noch in Bearbeitung" not in plain


def test_weekly_report_lists_only_failures_from_report_period(tmp_path: Path):
    StubSmtp.messages = []
    ledger = PrintLedger(tmp_path / "history.sqlite3")
    ledger.record_submission(
        job_id=123,
        file_path=tmp_path / "current.pdf",
        printer_id=456,
        email_subject="Current invoice",
        email_web_url="https://outlook.office.com/mail/deeplink/read/current",
        submitted_utc="2026-09-20T10:00:00+00:00",
    )
    ledger.update_status(123, "error", error_message="renderer failed")
    ledger.record_submission(
        job_id=124,
        file_path=tmp_path / "old.pdf",
        printer_id=456,
        email_subject="Old invoice",
        email_web_url="https://outlook.office.com/mail/deeplink/read/old",
        submitted_utc="2026-09-01T10:00:00+00:00",
    )
    with ledger._connect() as connection:
        connection.execute(
            "UPDATE print_jobs SET updated_utc = ? WHERE printnode_job_id = ?",
            ("2026-09-20T10:01:00+00:00", 123),
        )
        connection.execute(
            "UPDATE print_jobs SET status = 'error', updated_utc = ? WHERE printnode_job_id = ?",
            ("2026-09-01T10:01:00+00:00", 124),
        )

    service = _service()
    assert service.maybe_send_weekly_report(
        ledger,
        now_utc=datetime(2026, 9, 21, 6, 30, tzinfo=timezone.utc),
    )

    message = StubSmtp.messages[0]
    plain = message.get_body(preferencelist=("plain",)).get_content()
    html = message.get_body(preferencelist=("html",)).get_content()
    assert "current.pdf" in plain
    assert "old.pdf" not in plain
    assert "Originale E-Mail öffnen" in html
