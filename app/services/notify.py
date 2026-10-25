"""Email (SMTP) and Telegram notifications. Emails never contain locations or photos."""
import logging
import smtplib
from email.message import EmailMessage

import httpx

from ..config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()


def send_email(to: str, subject: str, body: str) -> None:
    if not settings.smtp_host:
        if settings.is_prod:
            log.error("SMTP is not configured; email to %s was NOT sent", to)
        else:
            log.info("[DEV EMAIL] to=%s subject=%s\n%s", to, subject, body)
        return
    msg = EmailMessage()
    msg["From"] = settings.mail_from or settings.smtp_user
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        if settings.smtp_port == 465:
            server = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=15)
        else:
            server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15)
            server.starttls()
        with server:
            if settings.smtp_user:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
    except Exception:
        log.exception("Failed to send email to %s", to)


def notify_moderators(text: str) -> None:
    """Telegram alert to the moderator chat. No-op unless configured."""
    if not (settings.telegram_bot_token and settings.telegram_moderator_chat_id):
        log.info("[moderator alert] %s", text)
        return
    try:
        httpx.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            json={"chat_id": settings.telegram_moderator_chat_id, "text": text},
            timeout=10,
        )
    except Exception:
        log.exception("Telegram alert failed")
