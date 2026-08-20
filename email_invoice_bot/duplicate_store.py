from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse, urlunparse


WHITESPACE_RE = re.compile(r"\s+")
GENERIC_FILENAME_STEMS = {
    "attachment",
    "attachments",
    "cmr",
    "document",
    "documents",
    "faktura",
    "inv",
    "invoice",
    "pod",
    "rechnung",
    "zalaczniki",
    "za_czniki",
}
GENERIC_SUBJECTS = {
    "attachment",
    "attachments",
    "cmr",
    "d",
    "doc",
    "docs",
    "document",
    "documents",
    "faktura",
    "invoice",
    "pod",
    "rechnung",
    "scan",
    "scans",
}
REPLY_PREFIX_RE = re.compile(r"^(aw|fw|fwd|odp|re|wg):\s*", re.IGNORECASE)


@dataclass(frozen=True)
class DuplicateRecord:
    processed_at_utc: str
    subject_key: str
    filename_key: str
    url_key: str = ""


class DuplicateStore:
    def __init__(self, state_file: Path, lookback_days: int = 7) -> None:
        self.state_file = state_file
        self.lookback_days = lookback_days
        self._records: list[DuplicateRecord] = []
        self._dirty = False

    @staticmethod
    def normalize_subject(value: str) -> str:
        return WHITESPACE_RE.sub(" ", value.strip()).lower()

    @staticmethod
    def normalize_filename(value: str) -> str:
        return value.strip().lower()

    @staticmethod
    def normalize_url(value: str) -> str:
        cleaned = value.strip().strip(".,;)")
        if not cleaned:
            return ""
        parsed = urlparse(cleaned)
        if not parsed.scheme or not parsed.netloc:
            return cleaned.lower()
        netloc = parsed.netloc.lower()
        path = parsed.path.rstrip("/").lower()
        query = parsed.query
        return urlunparse((parsed.scheme.lower(), netloc, path, "", query, ""))

    @classmethod
    def is_generic_subject(cls, value: str) -> bool:
        key = cls.normalize_subject(value)
        while True:
            stripped = REPLY_PREFIX_RE.sub("", key, count=1)
            if stripped == key:
                break
            key = stripped.strip()
        if not key:
            return False
        return len(key) <= 3 or key in GENERIC_SUBJECTS

    @classmethod
    def is_generic_filename(cls, value: str) -> bool:
        key = cls.normalize_filename(value)
        if not key:
            return False
        return Path(key).stem in GENERIC_FILENAME_STEMS

    def _cutoff(self, now: datetime) -> datetime:
        return now.astimezone(timezone.utc) - timedelta(days=self.lookback_days)

    def _prune(self, now: datetime) -> None:
        cutoff = self._cutoff(now)
        kept: list[DuplicateRecord] = []
        for record in self._records:
            try:
                processed_at = datetime.fromisoformat(record.processed_at_utc)
            except ValueError:
                self._dirty = True
                continue
            if processed_at.tzinfo is None:
                processed_at = processed_at.replace(tzinfo=timezone.utc)
            if processed_at.astimezone(timezone.utc) >= cutoff:
                kept.append(record)
            else:
                self._dirty = True
        self._records = kept

    def load(self, now: datetime | None = None) -> None:
        self._records = []
        if self.state_file.exists():
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            raw_records = data.get("records", [])
            for item in raw_records:
                if not isinstance(item, dict):
                    continue
                try:
                    self._records.append(
                        DuplicateRecord(
                            processed_at_utc=str(item["processed_at_utc"]),
                            subject_key=str(item["subject_key"]),
                            filename_key=str(item["filename_key"]),
                            url_key=str(item.get("url_key", "")),
                        )
                    )
                except Exception:
                    continue
        self._prune(now or datetime.now(timezone.utc))

    def has_subject(self, subject: str, now: datetime | None = None) -> bool:
        self._prune(now or datetime.now(timezone.utc))
        key = self.normalize_subject(subject)
        if not key:
            return False
        return any(record.subject_key == key for record in self._records)

    def has_filename(self, filename: str, now: datetime | None = None) -> bool:
        self._prune(now or datetime.now(timezone.utc))
        key = self.normalize_filename(filename)
        if not key or self.is_generic_filename(key):
            return False
        return any(record.filename_key == key for record in self._records)

    def has_url(self, url: str, now: datetime | None = None) -> bool:
        self._prune(now or datetime.now(timezone.utc))
        key = self.normalize_url(url)
        if not key:
            return False
        return any(record.url_key == key for record in self._records)

    def add(
        self,
        subject: str,
        filename: str,
        processed_at: datetime | None = None,
        url: str = "",
    ) -> None:
        record = DuplicateRecord(
            processed_at_utc=(processed_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
            subject_key=self.normalize_subject(subject),
            filename_key=self.normalize_filename(filename),
            url_key=self.normalize_url(url),
        )
        self._records.append(record)
        self._dirty = True

    def add_url(self, subject: str, url: str, processed_at: datetime | None = None) -> None:
        self.add(subject, "", processed_at=processed_at, url=url)

    def flush(self) -> None:
        if not self._dirty:
            return
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file = self.state_file.with_suffix(self.state_file.suffix + ".tmp")
        payload = {"records": [asdict(record) for record in self._records]}
        temp_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temp_file.replace(self.state_file)
        self._dirty = False
