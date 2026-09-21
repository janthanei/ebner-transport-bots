from __future__ import annotations

import logging
import smtplib
import ssl
from datetime import datetime, time, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr
from zoneinfo import ZoneInfo

from .print_ledger import PrintLedger


LOGGER = logging.getLogger(__name__)


class PrintNotificationService:
    def __init__(
        self,
        *,
        smtp_host: str,
        smtp_port: int,
        smtp_username: str,
        smtp_password: str,
        smtp_starttls: bool,
        from_email: str,
        from_name: str,
        recipients: list[str],
        cc: list[str],
        error_share_path: str,
        report_timezone: str,
        weekly_weekday: int,
        weekly_hour: int,
        smtp_factory=smtplib.SMTP,
    ) -> None:
        if not smtp_host or not from_email or not recipients:
            raise ValueError(
                "Print email requires SMTP_HOST, SMTP_FROM_EMAIL, and PRINT_ALERT_TO"
            )
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_username = smtp_username
        self.smtp_password = smtp_password
        self.smtp_starttls = smtp_starttls
        self.from_email = from_email
        self.from_name = from_name
        self.recipients = recipients
        self.cc = cc
        self.error_share_path = error_share_path
        self.report_timezone = ZoneInfo(report_timezone)
        self.weekly_weekday = weekly_weekday
        self.weekly_hour = weekly_hour
        self.smtp_factory = smtp_factory

    def _send(self, subject: str, body: str) -> None:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = formataddr((self.from_name, self.from_email))
        message["To"] = ", ".join(self.recipients)
        if self.cc:
            message["Cc"] = ", ".join(self.cc)
        message.set_content(body)

        with self.smtp_factory(self.smtp_host, self.smtp_port, timeout=30) as smtp:
            smtp.ehlo()
            if self.smtp_starttls:
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
            if self.smtp_username:
                smtp.login(self.smtp_username, self.smtp_password)
            smtp.send_message(message)

    def send_unnotified_errors(self, ledger: PrintLedger) -> None:
        for job in ledger.unnotified_errors():
            retry_text = (
                "Der automatische Wiederholungsversuch ist ebenfalls fehlgeschlagen."
                if int(job.get("retry_count") or 0) > 0
                else "Der Auftrag konnte aus Sicherheitsgründen nicht automatisch wiederholt werden."
            )
            body = "\n".join(
                [
                    "Hallo Christian,",
                    "",
                    f"das Dokument {job['file_name']} konnte nicht gedruckt werden.",
                    retry_text,
                    "",
                    f"E-Mail-Betreff: {job.get('email_subject') or '-'}",
                    f"PrintNode-Auftrag: {job.get('printnode_job_id') or '-'}",
                    f"Fehler: {job.get('error_message') or 'Unbekannter Druckfehler'}",
                    f"Fehlerordner: {self.error_share_path}",
                    "",
                    "Bitte den Auftrag prüfen und bei Bedarf manuell drucken.",
                ]
            )
            try:
                self._send(f"Druckfehler: {job['file_name']}", body)
            except Exception as exc:
                LOGGER.exception(
                    "Print failure notification failed record_id=%s error=%s",
                    job["id"],
                    exc,
                )
                continue
            ledger.mark_notified(int(job["id"]))
            LOGGER.info("Print failure notification sent record_id=%s", job["id"])

    def maybe_send_weekly_report(
        self,
        ledger: PrintLedger,
        *,
        now_utc: datetime | None = None,
    ) -> bool:
        current_utc = now_utc or datetime.now(timezone.utc)
        current_local = current_utc.astimezone(self.report_timezone)
        days_since_report_day = (current_local.weekday() - self.weekly_weekday) % 7
        report_day = current_local.date() - timedelta(days=days_since_report_day)
        scheduled_local = datetime.combine(
            report_day,
            time(hour=self.weekly_hour),
            tzinfo=self.report_timezone,
        )
        if current_local < scheduled_local:
            return False

        period_end_local = datetime.combine(
            report_day,
            time.min,
            tzinfo=self.report_timezone,
        )
        period_start_local = period_end_local - timedelta(days=7)
        report_key = f"weekly:{period_start_local.date()}:{period_end_local.date()}"
        if ledger.has_report_run(report_key):
            return False

        start_utc = period_start_local.astimezone(timezone.utc).isoformat()
        end_utc = period_end_local.astimezone(timezone.utc).isoformat()
        summary = ledger.period_summary(start_utc, end_utc)
        unresolved = ledger.unresolved_errors()
        unresolved_lines = [
            f"- {job['file_name']} ({job.get('email_subject') or 'ohne Betreff'})"
            for job in unresolved
        ] or ["- Keine"]
        body = "\n".join(
            [
                "Hallo Christian,",
                "",
                f"Druckübersicht {period_start_local.date()} bis {period_end_local.date()}:",
                f"- Dokumente gesamt: {summary['total']}",
                f"- Erfolgreich: {summary['successful']}",
                f"- Nach Retry erfolgreich: {summary['recovered']}",
                f"- Fehlgeschlagen: {summary['failed']}",
                f"- Noch ausstehend: {summary['pending']}",
                "",
                "Offene Druckfehler:",
                *unresolved_lines,
                "",
                f"Fehlerordner: {self.error_share_path}",
            ]
        )
        self._send(
            f"Wöchentliche Druckübersicht {period_start_local.date()} bis {period_end_local.date()}",
            body,
        )
        ledger.record_report_run(report_key)
        LOGGER.info("Weekly print report sent report_key=%s", report_key)
        return True
