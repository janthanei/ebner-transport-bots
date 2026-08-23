from __future__ import annotations

import hashlib
import struct
from pathlib import Path

import pymupdf


FINGERPRINT_VERSION = b"rendered-pdf-v1\0"


def fingerprint_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def fingerprint_pdf_bytes(content: bytes) -> str:
    """Hash rendered page pixels so volatile PDF metadata does not affect identity."""
    try:
        with pymupdf.open(stream=content, filetype="pdf") as document:
            if document.page_count <= 0:
                raise ValueError("PDF has no pages")

            digest = hashlib.sha256(FINGERPRINT_VERSION)
            digest.update(struct.pack(">I", document.page_count))
            for page in document:
                pixmap = page.get_pixmap(
                    matrix=pymupdf.Matrix(1, 1),
                    colorspace=pymupdf.csRGB,
                    alpha=False,
                    annots=True,
                )
                digest.update(struct.pack(">III", pixmap.width, pixmap.height, pixmap.stride))
                digest.update(pixmap.samples)
        return f"visual-v1:{digest.hexdigest()}"
    except Exception:
        # Invalid or unsupported PDFs still get deterministic exact-byte protection.
        return f"raw-v1:{fingerprint_bytes(content)}"


def fingerprint_file(path: Path) -> str:
    content = path.read_bytes()
    if path.suffix.lower() == ".pdf":
        return fingerprint_pdf_bytes(content)
    return f"raw-v1:{fingerprint_bytes(content)}"
