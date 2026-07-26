"""Pipeline notification emails. No-op if SMTP is not configured.

Call via FastAPI's BackgroundTasks so it runs after the response is sent:
    background_tasks.add_task(emailer.send, to, subject, body)
"""

import logging
from email.message import EmailMessage

import aiosmtplib

from .settings import settings

logger = logging.getLogger(__name__)


async def send(to: str | None, subject: str, body: str) -> None:
    if not settings.smtp_host or not to:
        logger.info("email skipped (smtp not configured): to=%s subject=%s", to, subject)
        return

    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    try:
        await aiosmtplib.send(
            message,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user or None,
            password=settings.smtp_password or None,
            start_tls=True,
        )
    except Exception:
        logger.exception("failed to send email to=%s subject=%s", to, subject)
