"""Transactional notifications through a durable outbox.

A notification is written to the ``notifications`` table in the same transaction
as the state change that caused it, then delivered afterwards. Two properties
follow from that, and both matter for a lost property desk:

* a delivery failure never loses the message (the row stays ``pending``);
* a retry never sends a duplicate, because ``dedupe_key`` is unique.

The default transport writes to the structured log, so the demo needs no mail
account. Setting ``SMTP_HOST`` switches to real e-mail with no code change.
"""

from __future__ import annotations

import smtplib
import ssl
import uuid
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

from . import store
from .logging import get_logger
from .settings import settings

log = get_logger("lostfound.notify")


class Transport(Protocol):
    name: str

    def send(self, message: dict) -> None: ...


@dataclass(frozen=True)
class LogTransport:
    """Delivery to the application log: always available, never leaks data."""

    name: str = "log"

    def send(self, message: dict) -> None:
        log.info(
            "notification delivered",
            extra={
                "event": "notification",
                "report_id": message.get("report_id"),
                "audience": message.get("audience"),
                "recipient": message.get("recipient"),
                "subject": message.get("subject"),
            },
        )


@dataclass(frozen=True)
class SmtpTransport:
    """Plain SMTP submission, compatible with campus relays and hosted senders."""

    host: str
    port: int = 587
    username: str | None = None
    password: str | None = None
    sender: str = "lostfound@localhost"
    starttls: bool = True
    name: str = "smtp"

    def send(self, message: dict) -> None:
        recipient = message.get("recipient")
        if not recipient:
            raise ValueError("notification has no recipient address")
        email = EmailMessage()
        email["From"] = self.sender
        email["To"] = recipient
        email["Subject"] = message["subject"]
        email.set_content(message["body"])
        with smtplib.SMTP(self.host, self.port, timeout=15) as client:
            if self.starttls:
                client.starttls(context=ssl.create_default_context())
            if self.username:
                client.login(self.username, self.password or "")
            client.send_message(email)


def resolve_transport() -> Transport:
    if settings.smtp_host:
        return SmtpTransport(
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            sender=settings.smtp_from,
            starttls=settings.smtp_starttls,
        )
    return LogTransport()


def queue(
    con,
    *,
    dedupe_key: str,
    subject: str,
    body: str,
    audience: str = "owner",
    report_id: str | None = None,
    recipient: str | None = None,
) -> bool:
    """Queue one message; returns False when it was already queued."""
    return store.add_notification(
        con,
        notification_id=uuid.uuid4().hex,
        dedupe_key=dedupe_key,
        subject=subject,
        body=body,
        audience=audience,
        report_id=report_id,
        recipient=recipient,
    )


def dispatch(con, transport: Transport | None = None, limit: int = 50) -> dict[str, int]:
    """Try to deliver every pending message once. Never raises."""
    sender = transport or resolve_transport()
    outcome = {"sent": 0, "failed": 0, "skipped": 0}
    for message in store.pending_notifications(con, limit=limit):
        if sender.name == "smtp" and not message.get("recipient"):
            store.mark_notification(con, message["id"], "suppressed", error="no recipient address")
            outcome["skipped"] += 1
            continue
        try:
            sender.send(message)
        except Exception as exc:  # noqa: BLE001 - delivery must not break the request
            log.warning(
                "notification delivery failed",
                extra={"event": "notification_failed", "report_id": message.get("report_id")},
            )
            store.mark_notification(con, message["id"], "failed", error=str(exc)[:300])
            outcome["failed"] += 1
        else:
            store.mark_notification(con, message["id"], "sent", sent_at=store.iso_utc(store.now_utc()))
            outcome["sent"] += 1
    con.commit()
    return outcome
