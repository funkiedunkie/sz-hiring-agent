"""DB helpers for the messages table."""

import logging
from typing import Any

from db.supabase_logger import _client as db

logger = logging.getLogger(__name__)

TABLE = "messages"


def insert_message(
    applicant_id: str,
    channel: str,
    direction: str,
    body: str,
    external_id: str | None = None,
    subject: str | None = None,
    sent_at: str | None = None,
) -> dict[str, Any]:
    """Insert a message row. Returns inserted record or {} on failure."""
    record: dict[str, Any] = {
        "applicant_id": applicant_id,
        "channel": channel,
        "direction": direction,
        "body": body,
    }
    if external_id:
        record["external_id"] = external_id
    if subject:
        record["subject"] = subject
    if sent_at:
        record["sent_at"] = sent_at

    try:
        resp = db.table(TABLE).insert(record).execute()
        return resp.data[0] if resp.data else {}
    except Exception as exc:
        # Unique violation on external_id means already synced — not an error
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
            return {}
        logger.error("insert_message failed: %s", exc)
        return {}


def get_messages_for_applicant(applicant_id: str) -> list[dict[str, Any]]:
    """Return all messages for an applicant, oldest first."""
    resp = (
        db.table(TABLE)
        .select("*")
        .eq("applicant_id", applicant_id)
        .order("sent_at", desc=False)
        .execute()
    )
    return resp.data or []
