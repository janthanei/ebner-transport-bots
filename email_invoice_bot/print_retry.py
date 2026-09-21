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
    if ghostscript is None:
        raise RuntimeError("Ghostscript is required to normalize a failed PDF")

    temporary = file_path.with_name(f".{file_path.stem}.normalized.tmp.pdf")
    try:
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
        if not temporary.exists() or temporary.stat().st_size == 0:
            raise RuntimeError("Ghostscript produced no normalized PDF")
        temporary.replace(file_path)
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Ghostscript normalization failed: {detail}") from exc
    finally:
        temporary.unlink(missing_ok=True)
