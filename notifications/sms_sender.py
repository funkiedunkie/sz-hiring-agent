"""
Sends SMS alerts via Twilio when a candidate crosses the score threshold.
"""

import logging

from twilio.rest import Client

import config

logger = logging.getLogger(__name__)

_client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)


def send_candidate_alert(
    candidate_name: str,
    job_title: str,
    score: int,
    rationale: str,
    profile_url: str = "",
) -> str:
    """Send an SMS alert and return the Twilio message SID."""
    body_lines = [
        f"New Applicant Alert",
        f"Name: {candidate_name}",
        f"Role: {job_title}",
        f"Score: {score}/10",
        f"Notes: {rationale[:120]}",
    ]
    if profile_url:
        body_lines.append(f"Profile: {profile_url}")

    body = "\n".join(body_lines)

    message = _client.messages.create(
        body=body,
        from_=config.TWILIO_FROM_NUMBER,
        to=config.TWILIO_TO_NUMBER,
    )

    logger.info("SMS sent for %s (score %d), SID: %s", candidate_name, score, message.sid)
    return message.sid
