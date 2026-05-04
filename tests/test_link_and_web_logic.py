from io import BytesIO

import pytest

from email_invoice_bot.link_extractor import filter_target_links, is_domain_allowed
from email_invoice_bot.web_downloader import PdfCandidate, WebDownloader, contains_cmr


def test_filter_target_links():
    links = [
        "https://download.example.com/a",
        "https://other.example.com/b",
    ]
    out = filter_target_links(links, "download.example.com")
    assert out == ["https://download.example.com/a"]


def test_domain_allowlist():
    assert is_domain_allowed("https://files.example.com/x", ["example.com"])
    assert not is_domain_allowed("https://evil.com/x", ["example.com"])


def test_contains_cmr():
    candidates = [
        PdfCandidate(index=0, href="https://x/a.pdf", label="Invoice", filename_hint="invoice.pdf"),
        PdfCandidate(index=1, href="https://x/b.pdf", label="CMR Letter", filename_hint="note.pdf"),
    ]
    assert contains_cmr(candidates, "cmr")


def test_convert_downloaded_image_to_pdf(tmp_path):
    PIL = pytest.importorskip("PIL")
    from PIL import Image

    img_path = tmp_path / "cmr.jpg"
    image = Image.new("RGB", (20, 20), "white")
    out = BytesIO()
    image.save(out, format="JPEG")
    img_path.write_bytes(out.getvalue())

    pdf_path = WebDownloader._convert_image_file_to_pdf(img_path)
    assert pdf_path.exists()
    assert pdf_path.suffix.lower() == ".pdf"
    assert not img_path.exists()


def test_convert_downloaded_image_to_pdf_suffixes_existing_pdf(tmp_path):
    PIL = pytest.importorskip("PIL")
    from PIL import Image

    existing_pdf = tmp_path / "cmr.pdf"
    existing_pdf.write_bytes(b"old")
    img_path = tmp_path / "cmr.jpg"
    image = Image.new("RGB", (20, 20), "white")
    out = BytesIO()
    image.save(out, format="JPEG")
    img_path.write_bytes(out.getvalue())

    pdf_path = WebDownloader._convert_image_file_to_pdf(img_path)
    assert pdf_path == tmp_path / "cmr_2.pdf"
    assert pdf_path.exists()
    assert existing_pdf.read_bytes() == b"old"
    assert not img_path.exists()
