"""
Polls an IMAP mailbox for emails whose subject contains the configured
trigger string (default: "New Application").  Returns structured dicts
so the caller can decide what to do with each match.
"""

import email
import logging
from dataclasses import dataclass, field
from email.header import decode_header
from typing import Generator

from imapclient import IMAPClient

import config

logger = logging.getLogger(__name__)


@dataclass
class TriggerEmail:
    uid: int
    subject: str
    sender: str
    body: str
    raw_headers: dict = field(default_factory=dict)


def _decode_header_value(raw: str) -> str:
    parts = decode_header(raw)
    return "".join(
        fragment.decode(charset or "utf-8") if isinstance(fragment, bytes) else fragment
        for fragment, charset in parts
    )


def _extract_body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get("Content-Disposition"):
                payload = part.get_payload(decode=True)
                return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        return ""
    payload = msg.get_payload(decode=True)
    if payload:
        return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
    return ""


def poll_once(mark_seen: bool = True) -> Generator[TriggerEmail, None, None]:
    """Connect, fetch unseen trigger emails, yield each one, then disconnect."""
    with IMAPClient(config.EMAIL_HOST, port=config.EMAIL_PORT, ssl=True) as client:
        client.login(config.EMAIL_USERNAME, config.EMAIL_PASSWORD)
        client.select_folder(config.EMAIL_FOLDER)

        uids = client.search(["UNSEEN", "SUBJECT", config.EMAIL_TRIGGER_SUBJECT])
        logger.info("Found %d unseen trigger email(s)", len(uids))

        if not uids:
            return

        messages = client.fetch(uids, ["RFC822", "ENVELOPE"])

        for uid, data in messages.items():
            raw = data[b"RFC822"]
            msg = email.message_from_bytes(raw)

            subject = _decode_header_value(msg.get("Subject", ""))
            sender = _decode_header_value(msg.get("From", ""))
            body = _extract_body(msg)

            if mark_seen:
                client.add_flags(uid, [b"\\Seen"])

            yield TriggerEmail(uid=uid, subject=subject, sender=sender, body=body)
