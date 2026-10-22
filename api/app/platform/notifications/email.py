# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Email channel: SMTP (Mailpit) locally, SES on AWS. Chosen by config, not code.

`attachments` (filename, content_type, bytes) is optional; a proposal PDF is the first user of
it (P4-04). SES has no attachment support in `send_email`, so that path uses `send_raw_email`
with a MIME message instead, only when there is something to attach.
"""

import asyncio
import html
import smtplib
from email.message import EmailMessage
from typing import Protocol

from app.core.config import get_settings

Attachment = tuple[str, str, bytes]  # filename, content_type, bytes


def _build_message(
    to: str,
    subject: str,
    html_body: str,
    text_body: str,
    email_from: str,
    attachments: list[Attachment] | None = None,
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = email_from, to, subject
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    for filename, content_type, data in attachments or []:
        maintype, _, subtype = content_type.partition("/")
        msg.add_attachment(
            data,
            maintype=maintype or "application",
            subtype=subtype or "octet-stream",
            filename=filename,
        )
    return msg


class EmailBackend(Protocol):
    async def send(
        self,
        to: str,
        subject: str,
        html_body: str,
        text_body: str,
        attachments: list[Attachment] | None = None,
    ) -> None: ...


class SmtpBackend:
    async def send(
        self,
        to: str,
        subject: str,
        html_body: str,
        text_body: str,
        attachments: list[Attachment] | None = None,
    ) -> None:
        s = get_settings()
        msg = _build_message(to, subject, html_body, text_body, s.email_from, attachments)

        def _send() -> None:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=10) as smtp:
                smtp.send_message(msg)

        await asyncio.to_thread(_send)


class SesBackend:
    async def send(
        self,
        to: str,
        subject: str,
        html_body: str,
        text_body: str,
        attachments: list[Attachment] | None = None,
    ) -> None:
        import boto3

        s = get_settings()
        client = boto3.client("ses", region_name=s.s3_region)
        if not attachments:
            await asyncio.to_thread(
                client.send_email,
                Source=s.email_from,
                Destination={"ToAddresses": [to]},
                Message={
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": {
                        "Html": {"Data": html_body, "Charset": "UTF-8"},
                        "Text": {"Data": text_body, "Charset": "UTF-8"},
                    },
                },
            )
            return
        msg = _build_message(to, subject, html_body, text_body, s.email_from, attachments)
        await asyncio.to_thread(
            client.send_raw_email,
            Source=s.email_from,
            Destinations=[to],
            RawMessage={"Data": msg.as_bytes()},
        )


class MemoryBackend:
    """Collects messages; used by tests and when no mail server is configured. `.sent` keeps its
    original 3-tuple shape (to, subject, text_body); `.attachments` holds any files alongside,
    indexed the same as `.sent`, so existing callers that unpack 3 values are unaffected."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []
        self.attachments: list[list[Attachment]] = []

    async def send(
        self,
        to: str,
        subject: str,
        html_body: str,
        text_body: str,
        attachments: list[Attachment] | None = None,
    ) -> None:
        self.sent.append((to, subject, text_body))
        self.attachments.append(attachments or [])


_override: EmailBackend | None = None


def set_backend(backend: EmailBackend | None) -> None:
    global _override
    _override = backend


def get_backend() -> EmailBackend:
    if _override is not None:
        return _override
    s = get_settings()
    if s.email_backend == "ses":
        return SesBackend()
    if s.smtp_host:
        return SmtpBackend()
    return MemoryBackend()


def render(title: str, body: str, link: str | None = None) -> tuple[str, str]:
    """Simple HTML + plain-text template shared by every notification type."""
    link_html = f'<p><a href="{html.escape(link)}">Open in Relay CRM</a></p>' if link else ""
    page = (
        '<div style="font-family:Arial,sans-serif;max-width:520px">'
        f'<h2 style="margin:0 0 8px">{html.escape(title)}</h2>'
        f"<p>{html.escape(body)}</p>{link_html}"
        '<p style="color:#888;font-size:12px">Relay CRM</p></div>'
    )
    text = f"{title}\n\n{body}\n" + (f"\n{link}\n" if link else "")
    return page, text
