"""
Sends a Twilio SMS interview invite directly to the candidate.

Message template:
  "Hi {first_name}, this is Duncan with Stretch Zone. I'd love to set up a
   quick 15-minute virtual interview — here's a link to grab a time: {calendly_link}"
"""

import logging
import re

from twilio.rest import Client

import config

logger = logging.getLogger(__name__)

_client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)

MESSAGE_TEMPLATE = (
    "Hi {first_name}, this is Duncan with Stretch Zone. I'd love to set up a "
    "quick 15-minute virtual interview — here's a link to grab a time: {calendly_link}"
)


def _normalize_phone(phone: str) -> str:
    """Normalize a US phone number to E.164 format (+1XXXXXXXXXX)."""
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits[0] == "1":
        return f"+{digits}"
    return phone  # pass through and let Twilio validate


def send_interview_invite(candidate_name: str, candidate_phone: str) -> str:
    """
    Send the standard interview invite SMS to *candidate_phone*.
    Returns the Twilio message SID, or an empty string on failure.
    """
    if not candidate_phone:
        logger.warning("No phone number for '%s' — skipping SMS", candidate_name)
        return ""

    normalized = _normalize_phone(candidate_phone)
    first_name = candidate_name.split()[0] if candidate_name else "there"

    body = MESSAGE_TEMPLATE.format(
        first_name=first_name,
        calendly_link=config.CALENDLY_LINK,
    )

    try:
        message = _client.messages.create(
            body=body,
            from_=config.TWILIO_FROM_NUMBER,
            to=normalized,
        )
        logger.info(
            "Interview invite sent to %s (%s -> %s), SID: %s",
            candidate_name,
            candidate_phone,
            normalized,
            message.sid,
        )
        return message.sid
    except Exception as exc:
        logger.error("SMS failed for %s: %s", candidate_name, exc)
        return ""
