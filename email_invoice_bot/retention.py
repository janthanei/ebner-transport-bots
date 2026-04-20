from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path


LOGGER = logging.getLogger(__name__)
DAY_FORMAT = "%Y-%m-%d"
PENDING_BUCKET = "druck_ausstehend"


@dataclass(frozen=True)
class RetentionSummary:
    deleted_days: int = 0
    skipped_pending_days: int = 0


def _parse_day_name(value: str) -> date | None:
    try:
        return datetime.strptime(value, DAY_FORMAT).date()
    except ValueError:
        return None


def _extract_day_from_path(value: str | None) -> str | None:
    if not value:
        return None
    for part in Path(value).parts:
        if _parse_day_name(part) is not None:
            return part
    return None


def _load_pending_days_from_state(pending_jobs_file: Path) -> set[str]:
    if not pending_jobs_file.exists():
        return set()

    try:
        data = json.loads(pending_jobs_file.read_text(encoding="utf-8"))
    except Exception as exc:
        LOGGER.warning("Retention pending job state unreadable file=%s error=%s", pending_jobs_file, exc)
        return set()

    days: set[str] = set()
    for item in data.get("pending_jobs", []):
        if not isinstance(item, dict):
            continue
        for key in ("file_path", "base_dir"):
            day_name = _extract_day_from_path(item.get(key))
            if day_name is not None:
                days.add(day_name)
    return days


def _find_pending_bucket_days(rechnungen_dir: Path) -> set[str]:
    pending_days: set[str] = set()
    if not rechnungen_dir.exists():
        return pending_days

    for day_dir in rechnungen_dir.iterdir():
        if not day_dir.is_dir():
            continue
        pending_dir = day_dir / PENDING_BUCKET
        if not pending_dir.is_dir():
            continue
        if any(child.is_file() for child in pending_dir.iterdir()):
            pending_days.add(day_dir.name)
    return pending_days


def purge_old_output(
    output_root: Path,
    delete_after_days: int,
    *,
    today: date | None = None,
    pending_jobs_file: Path | None = None,
) -> RetentionSummary:
    if delete_after_days <= 0:
        return RetentionSummary()

    rechnungen_dir = output_root / "Rechnungen"
    if not rechnungen_dir.exists():
        return RetentionSummary()

    current_day = today or date.today()
    pending_days = _find_pending_bucket_days(rechnungen_dir)
    if pending_jobs_file is not None:
        pending_days.update(_load_pending_days_from_state(pending_jobs_file))

    deleted_days = 0
    skipped_pending_days = 0

    for day_dir in sorted(rechnungen_dir.iterdir()):
        if not day_dir.is_dir():
            continue

        day = _parse_day_name(day_dir.name)
        if day is None:
            continue

        age_days = (current_day - day).days
        if age_days < delete_after_days:
            continue

        if day_dir.name in pending_days:
            skipped_pending_days += 1
            LOGGER.info("Retention skipped pending day=%s", day_dir.name)
            continue

        shutil.rmtree(day_dir)
        deleted_days += 1
        LOGGER.info("Retention deleted day=%s", day_dir.name)

    return RetentionSummary(
        deleted_days=deleted_days,
        skipped_pending_days=skipped_pending_days,
    )
