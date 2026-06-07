"""
Sends a Twilio SMS interview invite directly to the candidate.

Message template:
  "Hi {first_name}, this is Duncan with Stretch Zone. I'd love to set up a
   quick 15-minute virtual interview — here's a link to grab a time: {calendly_link}"
"""

import logging

from twilio.rest import Client

import config

logger = logging.getLogger(__name__)

_client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)

MESSAGE_TEMPLATE = (
    "Hi {first_name}, this is Duncan with Stretch Zone. I'd love to set up a "
    "quick 15-minute virtual interview — here's a link to grab a time: {calendly_link}"
)


def send_interview_invite(candidate_name: str, candidate_phone: str) -> str:
    """
    Send the standard interview invite SMS to *candidate_phone*.
    Returns the Twilio message SID, or an empty string on failure.
    """
    if not candidate_phone:
        logger.warning("No phone number for '%s' — skipping SMS", candidate_name)
        return ""

    first_name = candidate_name.split()[0] if candidate_name else "there"

    body = MESSAGE_TEMPLATE.format(
        first_name=first_name,
        calendly_link=config.CALENDLY_LINK,
    )

    try:
        message = _client.messages.create(
            body=body,
            from_=config.TWILIO_FROM_NUMBER,
            to=candidate_phone,
        )
        logger.info(
            "Interview invite sent to %s (%s), SID: %s",
            candidate_name,
            candidate_phone,
            message.sid,
        )
        return message.sid
    except Exception as exc:
        logger.error("SMS failed for %s: %s", candidate_name, exc)
        return ""
