"""Pipeline notification emails. No-op if SMTP is not configured.

Call via FastAPI's BackgroundTasks so it runs after the response is sent:
    background_tasks.add_task(emailer.send, to, subject, body)

Every message goes out as multipart/alternative: the plain text the caller passes
in, plus an HTML rendering of the same content. HTML alone costs deliverability
and leaves text-only readers with nothing.
"""

import logging
from email.message import EmailMessage
from email.utils import formataddr

import aiosmtplib

from . import email_template
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


def _read_logo() -> bytes | None:
    """The footer logo, or None when the file is missing.

    A missing asset must not stop a decision notice from going out, so the
    template falls back to a wordmark and the send continues.
    """
    try:
        return email_template.LOGO_PATH.read_bytes()
    except OSError:
        logger.warning("email logo not found at %s; sending without it", email_template.LOGO_PATH)
        return None


def build_message(to: str, subject: str, body: str) -> EmailMessage:
    """The message as it will be sent.

    Separate from `send` so the result can be inspected — and tested — without a
    live SMTP server.
    """
    logo = _read_logo()

    message = EmailMessage()
    message["From"] = from_header()
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    message.add_alternative(
        email_template.render(subject, body, with_logo=logo is not None), subtype="html"
    )

    if logo is not None:
        # Attached to the HTML part rather than the message: a related image
        # belongs inside the alternative that references it, or clients showing
        # the plain-text part offer it as a stray download.
        html_part = message.get_payload()[-1]
        html_part.add_related(
            logo,
            maintype="image",
            subtype="jpeg",
            cid=f"<{email_template.LOGO_CID}>",
            filename="simit-logo.jpg",
        )

    return message


async def send(to: str | None, subject: str, body: str) -> None:
    if not settings.smtp_host or not to:
        logger.info("email skipped (smtp not configured): to=%s subject=%s", to, subject)
        return

    try:
        await aiosmtplib.send(
            build_message(to, subject, body),
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
