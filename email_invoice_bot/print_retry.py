from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


PDF_REPAIR_MARKERS = (
    "engine6_error",
    "unable to print document",
    "too few operands",
    "invalid pdf",
    "pdf error",
)
TRANSIENT_MARKERS = (
    "busy",
    "connection",
    "download",
    "network",
    "offline",
    "temporar",
    "timeout",
    "unavailable",
)


def classify_retry(error_message: str) -> str | None:
    normalized = error_message.casefold()
    if any(marker in normalized for marker in PDF_REPAIR_MARKERS):
        return "normalize"
    if any(marker in normalized for marker in TRANSIENT_MARKERS):
        return "resubmit"
    return None


def normalize_pdf(file_path: Path) -> None:
    ghostscript = shutil.which("gs")
    temporary = file_path.with_name(f".{file_path.stem}.normalized.tmp.pdf")
    try:
        if ghostscript is not None:
            subprocess.run(
                [
                    ghostscript,
                    "-q",
                    "-dNOPAUSE",
                    "-dBATCH",
                    "-dSAFER",
                    "-sDEVICE=pdfwrite",
                    "-dCompatibilityLevel=1.4",
                    f"-sOutputFile={temporary}",
                    str(file_path),
                ],
                check=True,
                capture_output=True,
                timeout=120,
            )
        else:
            _rasterize_with_pymupdf(file_path, temporary)
        if not temporary.exists() or temporary.stat().st_size == 0:
            raise RuntimeError("PDF normalization produced no output")
        temporary.replace(file_path)
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Ghostscript normalization failed: {detail}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def _rasterize_with_pymupdf(source: Path, target: Path) -> None:
    try:
        import pymupdf
    except ImportError:
        import fitz as pymupdf

    source_document = pymupdf.open(source)
    normalized_document = pymupdf.open()
    try:
        if source_document.page_count == 0:
            raise RuntimeError("Cannot normalize a PDF with no pages")
        for page in source_document:
            pixmap = page.get_pixmap(dpi=150, alpha=False)
            normalized_page = normalized_document.new_page(
                width=page.rect.width,
                height=page.rect.height,
            )
            normalized_page.insert_image(normalized_page.rect, pixmap=pixmap)
        normalized_document.save(target, garbage=4, deflate=True)
    finally:
        normalized_document.close()
        source_document.close()
