from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path


SAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9._-]+")


class DailyPdfStorage:
    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root

    @staticmethod
    def sanitize(value: str, fallback: str = "file") -> str:
        cleaned = SAFE_CHARS_RE.sub("_", value.strip())
        cleaned = cleaned.strip("._")
        return cleaned or fallback

    @classmethod
    def normalize_pdf_filename(cls, original_name: str) -> str:
        original_base = cls.sanitize(Path(original_name).stem, fallback="attachment")[:120]
        return f"{original_base}.pdf"

    def get_day_dir(self, received_at: datetime) -> Path:
        day_dir = self.output_root / "Rechnungen" / received_at.strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        return day_dir

    @staticmethod
    def unique_path(target: Path) -> Path:
        if not target.exists():
            return target
        for index in range(2, 10000):
            candidate = target.with_name(f"{target.stem}_{index}{target.suffix}")
            if not candidate.exists():
                return candidate
        raise RuntimeError(f"Could not allocate unique filename for {target}")

    def build_filename(self, original_name: str) -> str:
        return self.normalize_pdf_filename(original_name)

    def write_pdf_bytes(
        self,
        pdf_bytes: bytes,
        received_at: datetime,
        original_name: str,
    ) -> Path:
        day_dir = self.get_day_dir(received_at)
        target = self.unique_path(day_dir / self.build_filename(original_name))

        tmp_path = target.with_suffix(".pdf.tmp")
        tmp_path.write_bytes(pdf_bytes)
        tmp_path.replace(target)
        return target
