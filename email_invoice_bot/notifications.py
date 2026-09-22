from __future__ import annotations

import logging
import smtplib
import ssl
from datetime import datetime, time, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr
from html import escape
from typing import TYPE_CHECKING
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from .print_ledger import PrintLedger

if TYPE_CHECKING:
    from .graph_client import GraphClient


LOGGER = logging.getLogger(__name__)


def _outlook_url(value: object) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    allowed_hosts = ("outlook.office.com", "outlook.office365.com")
    if parsed.scheme != "https" or not any(
        hostname == host or hostname.endswith(f".{host}") for host in allowed_hosts
    ):
        return ""
    return url


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
        weekly_recipients: list[str] | None,
        weekly_cc: list[str] | None,
        error_share_path: str,
        report_timezone: str,
        weekly_weekday: int,
        weekly_hour: int,
        smtp_factory=smtplib.SMTP,
        graph_client: GraphClient | None = None,
    ) -> None:
        if not recipients:
            raise ValueError("Print email requires PRINT_ALERT_TO")
        if graph_client is None and (not smtp_host or not from_email):
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
        self.weekly_recipients = weekly_recipients or recipients
        self.weekly_cc = weekly_cc if weekly_cc is not None else cc
        self.error_share_path = error_share_path
        self.report_timezone = ZoneInfo(report_timezone)
        self.weekly_weekday = weekly_weekday
        self.weekly_hour = weekly_hour
        self.smtp_factory = smtp_factory
        self.graph_client = graph_client

    def _send(
        self,
        subject: str,
        body: str,
        *,
        recipients: list[str] | None = None,
        cc: list[str] | None = None,
        html_body: str | None = None,
    ) -> None:
        message_recipients = recipients or self.recipients
        message_cc = self.cc if cc is None else cc
        if self.graph_client is not None:
            self.graph_client.send_mail(
                subject,
                body,
                message_recipients,
                message_cc,
                html_body=html_body,
            )
            return

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = formataddr((self.from_name, self.from_email))
        message["To"] = ", ".join(message_recipients)
        if message_cc:
            message["Cc"] = ", ".join(message_cc)
        message.set_content(body)
        if html_body:
            message.add_alternative(html_body, subtype="html")

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
                "Auch der automatische zweite Druckversuch war nicht erfolgreich."
                if int(job.get("retry_count") or 0) > 0
                else "Der Auftrag wurde nicht automatisch wiederholt, um einen möglichen Doppeldruck zu vermeiden."
            )
            email_subject = str(job.get("email_subject") or "Ohne Betreff")
            email_url = _outlook_url(job.get("email_web_url"))
            body_lines = [
                "Hallo Christian,",
                "",
                "der automatische Druck ist für folgendes Dokument fehlgeschlagen:",
                f"Datei: {job['file_name']}",
                f"E-Mail: {email_subject}",
                "",
                retry_text,
            ]
            if email_url:
                body_lines.extend(["", f"Originale E-Mail öffnen: {email_url}"])
            body_lines.extend(
                [
                    "",
                    "Bitte drucke das Dokument manuell.",
                    f"Eine Kopie liegt zusätzlich hier: {self.error_share_path}",
                    "",
                    "Viele Grüße",
                    "Ebner Druckservice",
                ]
            )
            body = "\n".join(body_lines)
            link_html = (
                f'<p><a href="{escape(email_url, quote=True)}" '
                'style="display:inline-block;padding:10px 16px;background:#176b45;color:#ffffff;'
                'text-decoration:none;border-radius:6px;font-weight:bold">Originale E-Mail öffnen</a></p>'
                if email_url
                else ""
            )
            html_body = (
                '<div style="font-family:Arial,sans-serif;line-height:1.5;color:#1f2933;max-width:640px">'
                '<p>Hallo Christian,</p>'
                '<p>der automatische Druck ist für folgendes Dokument fehlgeschlagen:</p>'
                '<div style="padding:14px 16px;background:#f4f6f5;border-left:4px solid #c2413b">'
                f'<strong>Datei:</strong> {escape(str(job["file_name"]))}<br>'
                f'<strong>E-Mail:</strong> {escape(email_subject)}'
                '</div>'
                f'<p>{escape(retry_text)}</p>{link_html}'
                '<p>Bitte drucke das Dokument manuell.<br>'
                f'Eine Kopie liegt zusätzlich hier:<br><code>{escape(self.error_share_path)}</code></p>'
                '<p>Viele Grüße<br>Ebner Druckservice</p></div>'
            )
            try:
                self._send(
                    f"Druck nicht möglich: {job['file_name']}",
                    body,
                    html_body=html_body,
                )
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
        failures = ledger.period_failures(start_utc, end_utc)
        failure_lines: list[str] = []
        failure_html: list[str] = []
        for job in failures:
            email_subject = str(job.get("email_subject") or "Ohne Betreff")
            email_url = _outlook_url(job.get("email_web_url"))
            failure_lines.append(f"- {job['file_name']} - {email_subject}")
            if email_url:
                failure_lines.append(f"  Originale E-Mail öffnen: {email_url}")
            link_html = (
                f' · <a href="{escape(email_url, quote=True)}">Originale E-Mail öffnen</a>'
                if email_url
                else ""
            )
            failure_html.append(
                f'<li><strong>{escape(str(job["file_name"]))}</strong><br>'
                f'{escape(email_subject)}{link_html}</li>'
            )
        report_end_date = period_end_local.date() - timedelta(days=1)
        body_lines = [
            "Hallo Jan,",
            "",
            f"hier ist die Druckübersicht vom {period_start_local:%d.%m.%Y} bis {report_end_date:%d.%m.%Y}:",
            f"- Verarbeitete Dokumente: {summary['total']}",
            f"- Erfolgreich gedruckt: {summary['successful']}",
        ]
        if summary["pending"]:
            body_lines.append(f"- Noch in Bearbeitung: {summary['pending']}")
        body_lines.extend(["", "Automatisch fehlgeschlagene Drucke dieser Woche:"])
        if failure_lines:
            body_lines.extend(failure_lines)
            body_lines.extend(
                [
                    "",
                    f"Die betroffenen Dateien liegen zusätzlich hier: {self.error_share_path}",
                ]
            )
        else:
            body_lines.append("- Keine")
        body_lines.extend(["", "Viele Grüße", "Ebner Druckservice"])
        body = "\n".join(body_lines)
        pending_row = (
            f'<tr><td style="padding:7px">Noch in Bearbeitung</td><td style="padding:7px;text-align:right">{summary["pending"]}</td></tr>'
            if summary["pending"]
            else ""
        )
        failures_html = (
            f'<ul>{"".join(failure_html)}</ul>'
            '<p>Die betroffenen Dateien liegen zusätzlich hier:<br>'
            f'<code>{escape(self.error_share_path)}</code></p>'
            if failure_html
            else "<p>Keine</p>"
        )
        html_body = (
            '<div style="font-family:Arial,sans-serif;line-height:1.5;color:#1f2933;max-width:640px">'
            '<p>Hallo Jan,</p>'
            f'<p>hier ist die Druckübersicht vom <strong>{period_start_local:%d.%m.%Y}</strong> '
            f'bis <strong>{report_end_date:%d.%m.%Y}</strong>:</p>'
            '<table style="border-collapse:collapse;width:100%;max-width:480px">'
            f'<tr><td style="padding:7px;border-bottom:1px solid #ddd">Verarbeitete Dokumente</td><td style="padding:7px;text-align:right;border-bottom:1px solid #ddd"><strong>{summary["total"]}</strong></td></tr>'
            f'<tr><td style="padding:7px;border-bottom:1px solid #ddd">Erfolgreich gedruckt</td><td style="padding:7px;text-align:right;border-bottom:1px solid #ddd"><strong>{summary["successful"]}</strong></td></tr>'
            f'{pending_row}'
            '</table>'
            '<h3 style="margin-top:24px">Automatisch fehlgeschlagene Drucke dieser Woche</h3>'
            f'{failures_html}'
            '<p>Viele Grüße<br>Ebner Druckservice</p></div>'
        )
        self._send(
            f"Druckübersicht {period_start_local:%d.%m.%Y} bis {report_end_date:%d.%m.%Y}",
            body,
            recipients=self.weekly_recipients,
            cc=self.weekly_cc,
            html_body=html_body,
        )
        ledger.record_report_run(report_key)
        LOGGER.info("Weekly print report sent report_key=%s", report_key)
        return True
