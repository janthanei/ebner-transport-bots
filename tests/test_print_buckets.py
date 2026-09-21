from pathlib import Path

from email_invoice_bot.main import _move_to_print_bucket


def test_move_to_print_bucket_moves_file(tmp_path: Path):
    src = tmp_path / "invoice.pdf"
    src.write_bytes(b"abc")

    moved = _move_to_print_bucket(src, "druck_erfolg")

    assert not src.exists()
    assert moved.exists()
    assert moved.name == "invoice.pdf"
    assert moved.parent.name == "druck_erfolg"
    assert moved.read_bytes() == b"abc"


def test_move_to_print_bucket_suffixes_existing(tmp_path: Path):
    src = tmp_path / "invoice.pdf"
    src.write_bytes(b"new")
    existing = tmp_path / "druck_fehler" / "invoice.pdf"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_bytes(b"old")

    moved = _move_to_print_bucket(src, "druck_fehler")

    assert moved == tmp_path / "druck_fehler" / "invoice_2.pdf"
    assert existing.read_bytes() == b"old"
    assert moved.read_bytes() == b"new"


def test_move_error_from_dated_folder_to_global_archive(tmp_path: Path):
    day_dir = tmp_path / "Rechnungen" / "2026-04-16"
    pending_dir = day_dir / "druck_ausstehend"
    pending_dir.mkdir(parents=True)
    src = pending_dir / "invoice.pdf"
    src.write_bytes(b"failed")

    moved = _move_to_print_bucket(src, "druck_fehler")

    assert moved == tmp_path / "Rechnungen" / "druck_fehler" / "2026-04-16" / "invoice.pdf"
    assert moved.read_bytes() == b"failed"
    assert not src.exists()
