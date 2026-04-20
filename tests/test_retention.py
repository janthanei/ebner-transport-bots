from datetime import date

from email_invoice_bot.retention import purge_old_output


def test_purge_old_output_deletes_days_outside_retention_window(tmp_path):
    old_day = tmp_path / "output" / "Rechnungen" / "2026-04-12"
    kept_day = tmp_path / "output" / "Rechnungen" / "2026-04-14"
    old_day.mkdir(parents=True)
    kept_day.mkdir(parents=True)
    (old_day / "invoice.pdf").write_bytes(b"old")
    (kept_day / "invoice.pdf").write_bytes(b"keep")

    summary = purge_old_output(
        tmp_path / "output",
        7,
        today=date(2026, 4, 20),
        pending_jobs_file=tmp_path / "state" / "pending_print_jobs.json",
    )

    assert summary.deleted_days == 1
    assert summary.skipped_pending_days == 0
    assert not old_day.exists()
    assert kept_day.exists()


def test_purge_old_output_skips_day_with_pending_bucket_files(tmp_path):
    old_day = tmp_path / "output" / "Rechnungen" / "2026-04-12"
    pending_dir = old_day / "druck_ausstehend"
    pending_dir.mkdir(parents=True)
    (pending_dir / "invoice.pdf").write_bytes(b"pending")

    summary = purge_old_output(
        tmp_path / "output",
        7,
        today=date(2026, 4, 20),
        pending_jobs_file=tmp_path / "state" / "pending_print_jobs.json",
    )

    assert summary.deleted_days == 0
    assert summary.skipped_pending_days == 1
    assert old_day.exists()


def test_purge_old_output_skips_day_referenced_by_pending_job_state(tmp_path):
    old_day = tmp_path / "output" / "Rechnungen" / "2026-04-12"
    old_day.mkdir(parents=True)
    (old_day / "invoice.pdf").write_bytes(b"pending")

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    pending_jobs = state_dir / "pending_print_jobs.json"
    pending_jobs.write_text(
        '{\n'
        '  "pending_jobs": [\n'
        '    {\n'
        '      "job_id": 17,\n'
        '      "file_path": "/tmp/output/Rechnungen/2026-04-12/druck_ausstehend/invoice.pdf"\n'
        "    }\n"
        "  ]\n"
        "}\n",
        encoding="utf-8",
    )

    summary = purge_old_output(
        tmp_path / "output",
        7,
        today=date(2026, 4, 20),
        pending_jobs_file=pending_jobs,
    )

    assert summary.deleted_days == 0
    assert summary.skipped_pending_days == 1
    assert old_day.exists()
