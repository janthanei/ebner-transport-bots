from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class PendingPrintJob:
    job_id: int
    file_path: str
    base_dir: str
    created_utc: str


class PrintJobStore:
    def __init__(self, state_file: Path) -> None:
        self.state_file = state_file
        self._jobs: dict[int, PendingPrintJob] = {}
        self._dirty = False

    def load(self) -> None:
        if not self.state_file.exists():
            return
        data = json.loads(self.state_file.read_text(encoding="utf-8"))
        raw = data.get("pending_jobs", [])
        jobs: dict[int, PendingPrintJob] = {}
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                job = PendingPrintJob(
                    job_id=int(item["job_id"]),
                    file_path=str(item["file_path"]),
                    base_dir=str(item["base_dir"]),
                    created_utc=str(item["created_utc"]),
                )
            except Exception:
                continue
            jobs[job.job_id] = job
        self._jobs = jobs

    def add(self, job: PendingPrintJob) -> None:
        if self._jobs.get(job.job_id) == job:
            return
        self._jobs[job.job_id] = job
        self._dirty = True

    def remove(self, job_id: int) -> None:
        if job_id not in self._jobs:
            return
        self._jobs.pop(job_id, None)
        self._dirty = True

    def items(self) -> list[PendingPrintJob]:
        return list(self._jobs.values())

    def flush(self) -> None:
        if not self._dirty:
            return
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file = self.state_file.with_suffix(self.state_file.suffix + ".tmp")
        payload = {
            "pending_jobs": [asdict(j) for j in sorted(self._jobs.values(), key=lambda x: x.job_id)],
        }
        temp_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temp_file.replace(self.state_file)
        self._dirty = False
