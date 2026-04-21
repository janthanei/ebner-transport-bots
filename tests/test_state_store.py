from datetime import datetime
from pathlib import Path

from email_invoice_bot.email_parser import ParsedAttachment
from email_invoice_bot.email_parser import ParsedEmail
from email_invoice_bot.main import ProcessSummary, _finalize_processed_email, process_cycle
from email_invoice_bot.state_store import StateStore


def _email(uid: str, message_id: str, received_at: datetime) -> ParsedEmail:
    return ParsedEmail(
        uid=uid,
        message_id=message_id,
        subject="Invoice",
        sender="sender@example.com",
        received_at=received_at,
        body_text="hello",
        links=[],
        attachments=[],
    )


def test_process_cycle_skips_previously_processed_messages(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MAIL_PROVIDER", "graph")
    monkeypatch.setenv("LINK_SUBSTRING", "download.example.com")
    monkeypatch.setenv("OUTPUT_ROOT", str(tmp_path / "output"))

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    state_path = state_dir / "processed_state.json"
    state_path.write_text(
        '{\n'
        '  "processed_keys": [\n'
        '    "uid-1:<msg-1@example.com>"\n'
        "  ]\n"
        "}\n",
        encoding="utf-8",
    )

    emails = [
        _email("uid-1", "<msg-1@example.com>", datetime(2026, 4, 15, 9, 0, 0)),
        _email("uid-2", "<msg-2@example.com>", datetime(2026, 4, 15, 9, 5, 0)),
    ]

    def _stub_graph(**_kwargs):
        class _G:
            def fetch_recent_messages(self, _max_count, _lookback_hours):
                return emails

            def fetch_message_attachments(self, _message_id):
                return []

            def mark_message_read(self, _message_id):
                pass

        return _G()

    monkeypatch.setattr("email_invoice_bot.graph_client.GraphClient", _stub_graph)

    class StubAttachmentProcessor:
        def __init__(self, storage):
            self.storage = storage

        def process(self, email_obj, attachments=None):
            return [Path(self.storage.get_day_dir(email_obj.received_at) / f"{email_obj.uid}.pdf")]

        @staticmethod
        def is_printable_filename(_filename):
            return True

        @staticmethod
        def output_filename(filename):
            return filename

    class StubWebDownloader:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr("email_invoice_bot.main.AttachmentProcessor", StubAttachmentProcessor)
    monkeypatch.setattr("email_invoice_bot.main.WebDownloader", StubWebDownloader)

    summary = process_cycle(__import__("email_invoice_bot.config", fromlist=["AppConfig"]).AppConfig.from_env())

    assert summary == ProcessSummary(processed=1, saved_attachments=1, downloaded_from_web=0)
    saved_state = state_path.read_text(encoding="utf-8")
    assert '"uid-1:<msg-1@example.com>"' in saved_state
    assert '"uid-2:<msg-2@example.com>"' in saved_state



def test_finalize_processed_email_flushes_state_immediately(tmp_path):
    state_path = tmp_path / "processed_state.json"
    store = StateStore(state_path)
    summary = ProcessSummary()

    _finalize_processed_email(
        summary,
        store,
        "uid-3:<msg-3@example.com>",
        None,
        False,
        "uid-3",
    )

    reloaded = StateStore(state_path)
    reloaded.load()

    assert summary.processed == 1
    assert state_path.exists()
    assert reloaded.has("uid-3:<msg-3@example.com>")


def test_process_cycle_leaves_unread_when_no_printable_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MAIL_PROVIDER", "graph")
    monkeypatch.setenv("LINK_SUBSTRING", "download.example.com")
    monkeypatch.setenv("OUTPUT_ROOT", str(tmp_path / "output"))

    email = _email("uid-1", "<msg-1@example.com>", datetime(2026, 4, 20, 9, 0, 0))
    mark_calls: list[str] = []

    def _stub_graph(**_kwargs):
        class _G:
            def fetch_recent_messages(self, _max_count, _lookback_hours):
                return [email]

            def fetch_message_attachments(self, _message_id):
                return []

            def mark_message_read(self, message_id):
                mark_calls.append(message_id)

        return _G()

    monkeypatch.setattr("email_invoice_bot.graph_client.GraphClient", _stub_graph)

    summary = process_cycle(__import__("email_invoice_bot.config", fromlist=["AppConfig"]).AppConfig.from_env())

    assert summary == ProcessSummary(processed=1, saved_attachments=0, downloaded_from_web=0, printed_jobs=0)
    assert mark_calls == []


def test_process_cycle_marks_read_when_new_attachment_is_extracted(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MAIL_PROVIDER", "graph")
    monkeypatch.setenv("LINK_SUBSTRING", "download.example.com")
    monkeypatch.setenv("OUTPUT_ROOT", str(tmp_path / "output"))

    email = ParsedEmail(
        uid="uid-1",
        message_id="<msg-1@example.com>",
        subject="Invoice A",
        sender="sender@example.com",
        received_at=datetime(2026, 4, 20, 9, 0, 0),
        body_text="hello",
        links=[],
        attachments=[
            ParsedAttachment(
                filename="invoice.pdf",
                content_type="application/pdf",
                payload=b"%PDF-1.7 fake",
                inline=False,
            )
        ],
    )
    mark_calls: list[str] = []

    def _stub_graph(**_kwargs):
        class _G:
            def fetch_recent_messages(self, _max_count, _lookback_hours):
                return [email]

            def fetch_message_attachments(self, _message_id):
                return []

            def mark_message_read(self, message_id):
                mark_calls.append(message_id)

        return _G()

    monkeypatch.setattr("email_invoice_bot.graph_client.GraphClient", _stub_graph)

    summary = process_cycle(__import__("email_invoice_bot.config", fromlist=["AppConfig"]).AppConfig.from_env())

    assert summary.saved_attachments == 1
    assert mark_calls == ["uid-1"]


def test_process_cycle_skips_duplicate_subject_without_marking_read(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MAIL_PROVIDER", "graph")
    monkeypatch.setenv("LINK_SUBSTRING", "download.example.com")
    monkeypatch.setenv("OUTPUT_ROOT", str(tmp_path / "output"))

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "duplicate_history.json").write_text(
        '{\n'
        '  "records": [\n'
        '    {\n'
        '      "processed_at_utc": "2026-04-19T08:00:00+00:00",\n'
        '      "subject_key": "invoice a",\n'
        '      "filename_key": "invoice.pdf"\n'
        "    }\n"
        "  ]\n"
        "}\n",
        encoding="utf-8",
    )

    email = ParsedEmail(
        uid="uid-1",
        message_id="<msg-1@example.com>",
        subject="Invoice   A",
        sender="sender@example.com",
        received_at=datetime(2026, 4, 20, 9, 0, 0),
        body_text="hello",
        links=[],
        attachments=[
            ParsedAttachment(
                filename="fresh.pdf",
                content_type="application/pdf",
                payload=b"%PDF-1.7 fake",
                inline=False,
            )
        ],
    )
    mark_calls: list[str] = []

    def _stub_graph(**_kwargs):
        class _G:
            def fetch_recent_messages(self, _max_count, _lookback_hours):
                return [email]

            def fetch_message_attachments(self, _message_id):
                return []

            def mark_message_read(self, message_id):
                mark_calls.append(message_id)

        return _G()

    monkeypatch.setattr("email_invoice_bot.graph_client.GraphClient", _stub_graph)

    summary = process_cycle(__import__("email_invoice_bot.config", fromlist=["AppConfig"]).AppConfig.from_env())

    assert summary.saved_attachments == 0
    assert mark_calls == []
    assert not any((tmp_path / "output").rglob("*.pdf"))


def test_process_cycle_skips_duplicate_filename_but_processes_new_attachment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MAIL_PROVIDER", "graph")
    monkeypatch.setenv("LINK_SUBSTRING", "download.example.com")
    monkeypatch.setenv("OUTPUT_ROOT", str(tmp_path / "output"))

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "duplicate_history.json").write_text(
        '{\n'
        '  "records": [\n'
        '    {\n'
        '      "processed_at_utc": "2026-04-19T08:00:00+00:00",\n'
        '      "subject_key": "older invoice",\n'
        '      "filename_key": "invoice.pdf"\n'
        "    }\n"
        "  ]\n"
        "}\n",
        encoding="utf-8",
    )

    email = ParsedEmail(
        uid="uid-1",
        message_id="<msg-1@example.com>",
        subject="New invoice batch",
        sender="sender@example.com",
        received_at=datetime(2026, 4, 20, 9, 0, 0),
        body_text="hello",
        links=[],
        attachments=[
            ParsedAttachment(
                filename="invoice.pdf",
                content_type="application/pdf",
                payload=b"%PDF-1.7 duplicate",
                inline=False,
            ),
            ParsedAttachment(
                filename="fresh.pdf",
                content_type="application/pdf",
                payload=b"%PDF-1.7 fresh",
                inline=False,
            ),
        ],
    )
    mark_calls: list[str] = []

    def _stub_graph(**_kwargs):
        class _G:
            def fetch_recent_messages(self, _max_count, _lookback_hours):
                return [email]

            def fetch_message_attachments(self, _message_id):
                return []

            def mark_message_read(self, message_id):
                mark_calls.append(message_id)

        return _G()

    monkeypatch.setattr("email_invoice_bot.graph_client.GraphClient", _stub_graph)

    summary = process_cycle(__import__("email_invoice_bot.config", fromlist=["AppConfig"]).AppConfig.from_env())

    saved_files = sorted(path.name for path in (tmp_path / "output").rglob("*.pdf"))
    assert summary.saved_attachments == 1
    assert saved_files == ["fresh.pdf"]
    assert mark_calls == ["uid-1"]


def test_process_cycle_skips_duplicate_only_attachments_without_saving_printing_or_marking_read(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MAIL_PROVIDER", "graph")
    monkeypatch.setenv("LINK_SUBSTRING", "download.example.com")
    monkeypatch.setenv("OUTPUT_ROOT", str(tmp_path / "output"))
    monkeypatch.setenv("PRINT_ENABLED", "true")
    monkeypatch.setenv("PRINTNODE_API_KEY", "dummy-key")
    monkeypatch.setenv("PRINTNODE_PRINTER_ID", "1")

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "duplicate_history.json").write_text(
        '{\n'
        '  "records": [\n'
        '    {\n'
        '      "processed_at_utc": "2026-04-19T08:00:00+00:00",\n'
        '      "subject_key": "older invoice",\n'
        '      "filename_key": "faktura.pdf"\n'
        '    },\n'
        '    {\n'
        '      "processed_at_utc": "2026-04-19T08:00:00+00:00",\n'
        '      "subject_key": "older invoice",\n'
        '      "filename_key": "za_czniki.pdf"\n'
        "    }\n"
        "  ]\n"
        "}\n",
        encoding="utf-8",
    )

    email = ParsedEmail(
        uid="uid-1",
        message_id="<msg-1@example.com>",
        subject="KOIMEX invoice",
        sender="sender@example.com",
        received_at=datetime(2026, 4, 20, 9, 0, 0),
        body_text="hello",
        links=[],
        attachments=[
            ParsedAttachment(
                filename="faktura.pdf",
                content_type="application/pdf",
                payload=b"%PDF-1.7 duplicate-a",
                inline=False,
            ),
            ParsedAttachment(
                filename="załączniki.pdf",
                content_type="application/pdf",
                payload=b"%PDF-1.7 duplicate-b",
                inline=False,
            ),
        ],
    )
    mark_calls: list[str] = []
    print_calls: list[str] = []

    def _stub_graph(**_kwargs):
        class _G:
            def fetch_recent_messages(self, _max_count, _lookback_hours):
                return [email]

            def fetch_message_attachments(self, _message_id):
                return []

            def mark_message_read(self, message_id):
                mark_calls.append(message_id)

        return _G()

    def _stub_printnode(*_args, **_kwargs):
        class _P:
            def submit_pdf(self, pdf_path):
                print_calls.append(pdf_path.name)
                return 123

            def get_printjob(self, _job_id):
                return {"state": "queued"}

        return _P()

    monkeypatch.setattr("email_invoice_bot.graph_client.GraphClient", _stub_graph)
    monkeypatch.setattr("email_invoice_bot.main.PrintNodeClient", _stub_printnode)

    summary = process_cycle(__import__("email_invoice_bot.config", fromlist=["AppConfig"]).AppConfig.from_env())

    assert summary == ProcessSummary(processed=1, saved_attachments=0, downloaded_from_web=0, printed_jobs=0)
    assert print_calls == []
    assert mark_calls == []
    assert not any((tmp_path / "output").rglob("*.pdf"))
    duplicate_state = (state_dir / "duplicate_history.json").read_text(encoding="utf-8")
    assert duplicate_state.count('"filename_key"') == 2
