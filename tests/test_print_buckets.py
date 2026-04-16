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


def test_move_to_print_bucket_replaces_existing(tmp_path: Path):
    src = tmp_path / "invoice.pdf"
    src.write_bytes(b"new")
    existing = tmp_path / "druck_fehler" / "invoice.pdf"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_bytes(b"old")

    moved = _move_to_print_bucket(src, "druck_fehler")

    assert moved == existing
    assert moved.read_bytes() == b"new"
