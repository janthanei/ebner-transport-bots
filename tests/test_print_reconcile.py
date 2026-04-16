from __future__ import annotations

from pathlib import Path

from email_invoice_bot.main import ProcessSummary, _move_to_print_bucket, _reconcile_pending_print_jobs
from email_invoice_bot.print_job_store import PendingPrintJob, PrintJobStore


class StubPrintNodeClient:
    def __init__(self, states: dict[int, str]):
        self.states = states

    def get_printjob(self, job_id: int):
        return {"state": self.states[job_id]}


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
    assert (day_dir / "druck_fehler" / "invoice.pdf").exists()
    assert store.items() == []
    assert reloaded.items() == []

