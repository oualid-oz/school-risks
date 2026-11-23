"""Sending email: "log" backend (development) or SMTP, one connection per run."""
from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class Email:
    to: str
    subject: str
    text: str
    html: str | None = None


def build_message(email: Email) -> EmailMessage:
    message = EmailMessage()
    message["From"] = settings.mail_from
    message["To"] = email.to
    message["Subject"] = email.subject
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid(domain="school-risk-tracker")
    message.set_content(email.text)
    if email.html:
        message.add_alternative(email.html, subtype="html")
    return message


class LogMailer:
    """Writes the email to the logs: what you get without SMTP settings."""

    def __enter__(self) -> LogMailer:
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def send(self, email: Email) -> None:
        logger.info(
            "email (log backend, not sent)",
            extra={"to": email.to, "subject": email.subject, "body": email.text},
        )


class SmtpMailer:
    def __init__(self) -> None:
        if not settings.smtp_host:
            raise RuntimeError("SRT_MAIL_BACKEND=smtp needs SRT_SMTP_HOST")
        self._smtp: smtplib.SMTP | None = None

    def __enter__(self) -> SmtpMailer:
        context = ssl.create_default_context()
        timeout = settings.smtp_timeout_seconds
        if settings.smtp_security == "ssl":
            self._smtp = smtplib.SMTP_SSL(
                settings.smtp_host, settings.smtp_port, timeout=timeout, context=context
            )
        else:
            self._smtp = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=timeout)
            if settings.smtp_security == "starttls":
                self._smtp.starttls(context=context)
        if settings.smtp_username:
            self._smtp.login(settings.smtp_username, settings.smtp_password or "")
        return self

    def __exit__(self, *exc: object) -> None:
        if self._smtp is not None:
            try:
                self._smtp.quit()
            except smtplib.SMTPException:
                pass

    def send(self, email: Email) -> None:
        assert self._smtp is not None, "use the mailer as a context manager"
        self._smtp.send_message(build_message(email))


def get_mailer() -> LogMailer | SmtpMailer:
    if settings.mail_backend == "smtp":
        return SmtpMailer()
    if settings.mail_backend != "log":
        raise RuntimeError(f"Unknown SRT_MAIL_BACKEND {settings.mail_backend!r} (log or smtp)")
    return LogMailer()
