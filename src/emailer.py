"""Pipeline notification emails. No-op if SMTP is not configured.

Call via FastAPI's BackgroundTasks so it runs after the response is sent:
    background_tasks.add_task(emailer.send, to, subject, body)
"""

import logging
from email.message import EmailMessage
from email.utils import formataddr

import aiosmtplib

from .settings import settings

logger = logging.getLogger(__name__)


def from_header() -> str:
    """The From header, with a display name when one is configured.

    `formataddr` handles the quoting and RFC 2047 encoding, so a name carrying
    punctuation or non-ASCII characters — "[No-Reply] ... Türkiye 2026" — reaches
    the recipient intact instead of as a malformed header.
    """
    if not settings.smtp_from_name:
        return settings.smtp_from
    return formataddr((settings.smtp_from_name, settings.smtp_from))


async def send(to: str | None, subject: str, body: str) -> None:
    if not settings.smtp_host or not to:
        logger.info("email skipped (smtp not configured): to=%s subject=%s", to, subject)
        return

    message = EmailMessage()
    message["From"] = from_header()
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
            # Pinned to the bare address: the envelope sender is what the server
            # verifies, and it must not carry the display name.
            sender=settings.smtp_from,
            start_tls=True,
        )
    except Exception:
        logger.exception("failed to send email to=%s subject=%s", to, subject)
