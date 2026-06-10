"""
Syncs all Twilio SMS messages (inbound and outbound) for known applicant
phone numbers into the messages table.

Run on a schedule or call sync_all() directly.
"""

import logging
from datetime import timezone

from twilio.rest import Client

import config
from db.supabase_logger import _client as db
from db.messages_logger import insert_message
from notifications.sms_sender import _normalize_phone

logger = logging.getLogger(__name__)

_twilio = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)


def _get_applicant_phone_map() -> dict[str, str]:
    """Return {normalized_phone: applicant_id} for all applicants with a phone."""
    resp = db.table("applicants").select("id, phone").execute()
    result = {}
    for row in resp.data or []:
        if row.get("phone"):
            normalized = _normalize_phone(row["phone"])
            result[normalized] = row["id"]
    return result


def _sync_direction(phone_map: dict[str, str], direction: str) -> int:
    """
    Fetch messages from Twilio and upsert into messages table.
    direction: 'inbound' or 'outbound'
    """
    count = 0
    kwargs = {}
    if direction == "inbound":
        kwargs["to"] = config.TWILIO_FROM_NUMBER
    else:
        kwargs["from_"] = config.TWILIO_FROM_NUMBER

    messages = _twilio.messages.list(**kwargs, limit=500)

    for msg in messages:
        # Match the candidate's phone (the non-Stretch-Zone number)
        candidate_phone = (
            _normalize_phone(msg.from_) if direction == "inbound"
            else _normalize_phone(msg.to)
        )
        applicant_id = phone_map.get(candidate_phone)
        if not applicant_id:
            continue

        sent_at = (
            msg.date_sent.replace(tzinfo=timezone.utc).isoformat()
            if msg.date_sent
            else None
        )

        result = insert_message(
            applicant_id=applicant_id,
            channel="sms",
            direction=direction,
            body=msg.body or "",
            external_id=msg.sid,
            sent_at=sent_at,
        )
        if result:
            count += 1

    return count


def sync_all() -> dict[str, int]:
    """Sync inbound and outbound SMS for all known applicants. Returns counts."""
    phone_map = _get_applicant_phone_map()
    if not phone_map:
        logger.info("No applicants with phone numbers — skipping SMS sync")
        return {"inbound": 0, "outbound": 0}

    inbound = _sync_direction(phone_map, "inbound")
    outbound = _sync_direction(phone_map, "outbound")
    logger.info("SMS sync complete — inbound: %d, outbound: %d", inbound, outbound)
    return {"inbound": inbound, "outbound": outbound}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(sync_all())
