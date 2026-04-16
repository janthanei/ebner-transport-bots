from datetime import datetime
from pathlib import Path

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

            def mark_message_read(self, _message_id):
                pass

        return _G()

    monkeypatch.setattr("email_invoice_bot.graph_client.GraphClient", _stub_graph)

    class StubAttachmentProcessor:
        def __init__(self, storage):
            self.storage = storage

        def process(self, email_obj):
            return [Path(self.storage.get_day_dir(email_obj.received_at) / f"{email_obj.uid}.pdf")]

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
