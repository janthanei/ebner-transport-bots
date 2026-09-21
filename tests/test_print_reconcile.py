from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone

from email_invoice_bot.main import ProcessSummary, _move_to_print_bucket, _reconcile_pending_print_jobs
from email_invoice_bot.print_job_store import PendingPrintJob, PrintJobStore
from email_invoice_bot.print_ledger import PrintLedger


class StubPrintNodeClient:
    printer_id = 456

    def __init__(self, states: dict[int, str], error_message: str = ""):
        self.states = states
        self.error_message = error_message
        self.submissions = []

    def get_printjob(self, job_id: int):
        return {"state": self.states[job_id]}

    def get_printjob_states(self, job_id: int):
        return [{"state": self.states[job_id], "message": self.error_message}]

    def submit_pdf(self, file_path: Path, idempotency_key: str | None = None):
        self.submissions.append((file_path, idempotency_key))
        self.states[3] = "queued"
        return 3


def test_reconcile_moves_done_to_druck_erfolg(tmp_path: Path):
    day_dir = tmp_path / "2026-04-16"
    day_dir.mkdir(parents=True)
    src = day_dir / "invoice.pdf"
    src.write_bytes(b"abc")

    pending_path = _move_to_print_bucket(src, "druck_ausstehend")

    store = PrintJobStore(tmp_path / "state.json")
    store.add(
        PendingPrintJob(
            job_id=1,
            file_path=str(pending_path),
            base_dir=str(day_dir),
            created_utc="2026-04-16T00:00:00+00:00",
        )
    )
    summary = ProcessSummary()

    _reconcile_pending_print_jobs(StubPrintNodeClient({1: "done"}), store, summary)

    assert summary.printed_jobs == 1
    assert not pending_path.exists()
    assert (day_dir / "druck_erfolg" / "invoice.pdf").exists()
    assert store.items() == []


def test_reconcile_moves_error_to_druck_fehler(tmp_path: Path):
    day_dir = tmp_path / "2026-04-16"
    day_dir.mkdir(parents=True)
    src = day_dir / "invoice.pdf"
    src.write_bytes(b"abc")

    pending_path = _move_to_print_bucket(src, "druck_ausstehend")

    store = PrintJobStore(tmp_path / "state.json")
    store.add(
        PendingPrintJob(
            job_id=2,
            file_path=str(pending_path),
            base_dir=str(day_dir),
            created_utc="2026-04-16T00:00:00+00:00",
        )
    )
    summary = ProcessSummary()

    _reconcile_pending_print_jobs(StubPrintNodeClient({2: "error"}), store, summary)

    reloaded = PrintJobStore(tmp_path / "state.json")
    reloaded.load()

    assert summary.printed_jobs == 0
    assert not pending_path.exists()
    assert (tmp_path / "druck_fehler" / "2026-04-16" / "invoice.pdf").exists()
    assert store.items() == []
    assert reloaded.items() == []


def test_reconcile_normalizes_and_retries_renderer_error_once(tmp_path: Path, monkeypatch):
    day_dir = tmp_path / "2026-04-16"
    day_dir.mkdir(parents=True)
    src = day_dir / "invoice.pdf"
    src.write_bytes(b"pdf")
    pending_path = _move_to_print_bucket(src, "druck_ausstehend")
    store = PrintJobStore(tmp_path / "state.json")
    store.add(
        PendingPrintJob(
            job_id=2,
            file_path=str(pending_path),
            base_dir=str(day_dir),
            created_utc="2026-04-16T00:00:00+00:00",
            original_job_id=2,
        )
    )
    store.flush()
    ledger = PrintLedger(tmp_path / "history.sqlite3")
    ledger.record_submission(
        job_id=2,
        file_path=pending_path,
        printer_id=456,
        submitted_utc="2026-04-16T00:00:00+00:00",
    )
    client = StubPrintNodeClient(
        {2: "error"},
        "engine6_error; Unable to print document: Too few operands in path.",
    )
    normalized = []
    monkeypatch.setattr(
        "email_invoice_bot.main.normalize_pdf",
        lambda path: normalized.append(path),
    )
    now = datetime(2026, 4, 16, tzinfo=timezone.utc)

    _reconcile_pending_print_jobs(
        client,
        store,
        ProcessSummary(),
        ledger,
        retry_enabled=True,
        retry_delay_seconds=0,
        now_utc=now,
    )
    _reconcile_pending_print_jobs(
        client,
        store,
        ProcessSummary(),
        ledger,
        retry_enabled=True,
        retry_delay_seconds=0,
        now_utc=now,
    )

    assert normalized == [pending_path]
    assert client.submissions == [(pending_path, "ebner-retry-2")]
    assert [job.job_id for job in store.items()] == [3]
    assert store.items()[0].retry_count == 1
    assert ledger.get_job(2)["status"] == "retried"
    assert ledger.get_job(3)["retry_of_job_id"] == 2

    client.states[3] = "done"
    summary = ProcessSummary()
    _reconcile_pending_print_jobs(client, store, summary, ledger, now_utc=now)

    assert summary.printed_jobs == 1
    assert ledger.get_job(2)["status"] == "recovered"
    assert ledger.get_job(3)["status"] == "done"
    assert (day_dir / "druck_erfolg" / "invoice.pdf").exists()
