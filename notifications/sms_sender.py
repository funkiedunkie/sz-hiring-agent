"""
Sends a Twilio SMS interview invite directly to the candidate.

Message template:
  "Hi {first_name}, this is Duncan with Stretch Zone. I'd love to set up a
   quick 15-minute virtual interview — here's a link to grab a time: {calendly_link}"
"""

import logging
import re
from datetime import datetime, timezone, timedelta

from twilio.rest import Client

import config

logger = logging.getLogger(__name__)

_client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)

PER_CANDIDATE_DAILY_CAP = 3   # max outbound SMS to one number per 24h
GLOBAL_DAILY_CAP = 30         # max total outbound SMS per calendar day
DUPLICATE_WINDOW_HOURS = 1    # block identical body to same number within this window


def _rate_limit_check(normalized_phone: str, body: str) -> str | None:
    """
    Returns a block reason string if the send should be suppressed, else None.
    Fails open: any DB error allows the send through (logged as warning).
    """
    try:
        from db.supabase_logger import _client as db
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        since_24h = (now - timedelta(hours=24)).isoformat()
        dup_window = (now - timedelta(hours=DUPLICATE_WINDOW_HOURS)).isoformat()

        # 1. Global daily cap
        global_resp = (
            db.table("messages")
            .select("id", count="exact")
            .eq("channel", "sms")
            .eq("direction", "outbound")
            .gte("sent_at", today_start)
            .execute()
        )
        if (global_resp.count or 0) >= GLOBAL_DAILY_CAP:
            return f"global daily cap reached ({global_resp.count}/{GLOBAL_DAILY_CAP})"

        # Look up applicant by phone for per-candidate checks
        phone_resp = (
            db.table("applicants")
            .select("id")
            .or_(f"phone.eq.{normalized_phone},phone.eq.{normalized_phone.lstrip('+1')}")
            .limit(1)
            .execute()
        )
        if not phone_resp.data:
            return None  # unknown number — let Twilio validate

        applicant_id = phone_resp.data[0]["id"]

        # 2. Per-candidate daily cap
        cand_resp = (
            db.table("messages")
            .select("id", count="exact")
            .eq("applicant_id", applicant_id)
            .eq("channel", "sms")
            .eq("direction", "outbound")
            .gte("sent_at", since_24h)
            .execute()
        )
        if (cand_resp.count or 0) >= PER_CANDIDATE_DAILY_CAP:
            return f"per-candidate daily cap reached ({cand_resp.count}/{PER_CANDIDATE_DAILY_CAP})"

        # 3. Duplicate body guard
        dup_resp = (
            db.table("messages")
            .select("id", count="exact")
            .eq("applicant_id", applicant_id)
            .eq("channel", "sms")
            .eq("direction", "outbound")
            .eq("body", body)
            .gte("sent_at", dup_window)
            .execute()
        )
        if (dup_resp.count or 0) > 0:
            return f"duplicate suppressed (identical message sent within last {DUPLICATE_WINDOW_HOURS}h)"

    except Exception as exc:
        logger.warning("Rate limit check failed (allowing send): %s", exc)

    return None

MESSAGE_TEMPLATE = (
    "Hi {first_name}, this is Duncan with Stretch Zone Meridian. I'd love to set up a "
    "quick 15-minute virtual interview about your Stretch Practitioner application — "
    "grab a time: {calendly_link}\n\n"
    "Reply STOP to opt out. Msg & data rates may apply."
)


def _normalize_phone(phone) -> str:
    """Normalize a US phone number to E.164 format (+1XXXXXXXXXX)."""
    if not isinstance(phone, str):
        return ""
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits[0] == "1":
        return f"+{digits}"
    return phone  # pass through and let Twilio validate


def _create_kwargs(to: str, body: str) -> dict:
    """Build Twilio message.create kwargs, routing via Messaging Service when configured."""
    kwargs = {"body": body, "to": to}
    if config.TWILIO_MESSAGING_SERVICE_SID:
        kwargs["messaging_service_sid"] = config.TWILIO_MESSAGING_SERVICE_SID
    else:
        kwargs["from_"] = config.TWILIO_FROM_NUMBER
    return kwargs


def send_sms(phone: str, body: str) -> str:
    """
    Send a custom SMS to *phone*.
    Returns the Twilio message SID, or an empty string on failure.
    """
    if not config.SMS_ENABLED:
        logger.warning("SMS_ENABLED=false — skipping send_sms to %s", phone)
        return ""
    if not phone:
        logger.warning("send_sms called with empty phone")
        return ""

    normalized = _normalize_phone(phone)
    block_reason = _rate_limit_check(normalized, body)
    if block_reason:
        logger.warning("SMS to %s blocked: %s", normalized, block_reason)
        return ""
    try:
        message = _client.messages.create(**_create_kwargs(normalized, body))
        logger.info("SMS sent to %s, SID: %s", normalized, message.sid)
        return message.sid
    except Exception as exc:
        logger.error("SMS failed to %s: %s", phone, exc)
        return ""


def send_interview_invite(candidate_name: str, candidate_phone: str) -> str:
    """
    Send the standard interview invite SMS to *candidate_phone*.
    Returns the Twilio message SID, or an empty string on failure.
    """
    if not config.SMS_ENABLED:
        logger.warning("SMS_ENABLED=false — skipping invite SMS for %s", candidate_name)
        return ""
    if not candidate_phone:
        logger.warning("No phone number for '%s' — skipping SMS", candidate_name)
        return ""

    normalized = _normalize_phone(candidate_phone)
    first_name = candidate_name.split()[0] if candidate_name else "there"

    body = MESSAGE_TEMPLATE.format(
        first_name=first_name,
        calendly_link=config.CALENDLY_LINK,
    )

    block_reason = _rate_limit_check(normalized, body)
    if block_reason:
        logger.warning("Invite SMS to %s blocked: %s", normalized, block_reason)
        return ""

    try:
        message = _client.messages.create(**_create_kwargs(normalized, body))
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
