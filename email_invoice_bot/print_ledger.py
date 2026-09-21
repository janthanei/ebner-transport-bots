from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PrintLedger:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS print_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    printnode_job_id INTEGER UNIQUE,
                    original_job_id INTEGER,
                    retry_of_job_id INTEGER,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    email_uid TEXT NOT NULL DEFAULT '',
                    email_subject TEXT NOT NULL DEFAULT '',
                    file_name TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    printer_id INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    error_message TEXT NOT NULL DEFAULT '',
                    submitted_utc TEXT NOT NULL,
                    updated_utc TEXT NOT NULL,
                    resolved_utc TEXT,
                    notified_utc TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_print_jobs_status ON print_jobs(status)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_print_jobs_submitted ON print_jobs(submitted_utc)"
            )

    def record_submission(
        self,
        *,
        job_id: int,
        file_path: Path,
        printer_id: int,
        email_uid: str = "",
        email_subject: str = "",
        retry_count: int = 0,
        original_job_id: int | None = None,
        retry_of_job_id: int | None = None,
        submitted_utc: str | None = None,
    ) -> None:
        timestamp = submitted_utc or _utc_now()
        root_job_id = original_job_id or job_id
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO print_jobs (
                    printnode_job_id, original_job_id, retry_of_job_id, retry_count,
                    email_uid, email_subject, file_name, file_path, printer_id,
                    status, submitted_utc, updated_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'submitted', ?, ?)
                ON CONFLICT(printnode_job_id) DO UPDATE SET
                    file_path = excluded.file_path,
                    updated_utc = excluded.updated_utc
                """,
                (
                    job_id,
                    root_job_id,
                    retry_of_job_id,
                    retry_count,
                    email_uid,
                    email_subject,
                    file_path.name,
                    str(file_path),
                    printer_id,
                    timestamp,
                    timestamp,
                ),
            )

    def record_submission_failure(
        self,
        *,
        file_path: Path,
        printer_id: int,
        error_message: str,
        email_uid: str = "",
        email_subject: str = "",
    ) -> None:
        timestamp = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO print_jobs (
                    email_uid, email_subject, file_name, file_path, printer_id,
                    status, error_message, submitted_utc, updated_utc
                ) VALUES (?, ?, ?, ?, ?, 'error', ?, ?, ?)
                """,
                (
                    email_uid,
                    email_subject,
                    file_path.name,
                    str(file_path),
                    printer_id,
                    error_message,
                    timestamp,
                    timestamp,
                ),
            )

    def update_status(
        self,
        job_id: int,
        status: str,
        *,
        file_path: Path | None = None,
        error_message: str = "",
    ) -> None:
        timestamp = _utc_now()
        resolved_utc = timestamp if status == "done" else None
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE print_jobs
                SET status = ?,
                    file_path = COALESCE(?, file_path),
                    error_message = ?,
                    updated_utc = ?,
                    resolved_utc = COALESCE(?, resolved_utc)
                WHERE printnode_job_id = ?
                """,
                (
                    status,
                    str(file_path) if file_path is not None else None,
                    error_message,
                    timestamp,
                    resolved_utc,
                    job_id,
                ),
            )

    def get_job(self, job_id: int) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM print_jobs WHERE printnode_job_id = ?",
                (job_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def summary(self, start_utc: str, end_utc: str) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM print_jobs
                WHERE submitted_utc >= ? AND submitted_utc < ?
                GROUP BY status
                """,
                (start_utc, end_utc),
            ).fetchall()
        counts = {str(row["status"]): int(row["count"]) for row in rows}
        counts["total"] = sum(counts.values())
        return counts

    def unresolved_errors(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM print_jobs
                WHERE status = 'error' AND resolved_utc IS NULL
                ORDER BY submitted_utc ASC
                """
            ).fetchall()
        return [dict(row) for row in rows]
