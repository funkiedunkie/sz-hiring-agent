"""
Handles the availability-request → ClubReady-booking pipeline.

Flow:
  1. send_availability_request(applicant)
     - Sends Option D SMS/email asking for availability
     - Sets scheduling_requested_at on applicant

  2. process_scheduling_replies()  [cron]
     - Finds applicants with scheduling_requested_at set, no booking yet
     - Reads their inbound messages after scheduling_requested_at
     - Parses with Claude → date/time list
     - Calls scrapers/clubready.py to block the slot
     - Sends confirmation to candidate, or fallback email to boise@stretchzone.com
"""

import logging
from datetime import datetime, timezone

import config
from db.supabase_logger import _client as db
from db.messages_logger import insert_message, get_messages_for_applicant
from notifications.sms_sender import send_sms
from notifications.email_sender import send_email
from agents.availability_parser import parse_availability
from scrapers.clubready import block_time

logger = logging.getLogger(__name__)

AVAIL_REQUEST_SUBJECT = "Scheduling your stretch — Stretch Zone"

AVAIL_SMS = (
    "Thanks for taking the time to meet with me. To get you scheduled, could you "
    "share a couple days and times that work for you? Mon-Fri mornings or afternoons "
    "work best. Thanks, Duncan"
)

AVAIL_EMAIL = (
    "{first_name}, thanks for taking the time to meet with me.\n\n"
    "To get you scheduled for a stretch, would you mind sharing some times that work for you? "
    "We have morning and afternoon openings Monday through Friday. "
    "Two or three options works great — I can usually find something that lines up.\n\n"
    "Thanks,\nDuncan Richardson"
)

CONFIRM_SMS = (
    "Great news — you're all set for {day_date} at {time}. "
    "Looking forward to seeing you! — Duncan"
)

CONFIRM_EMAIL = (
    "{first_name}, you're confirmed for {day_date} at {time}.\n\n"
    "Looking forward to seeing you. Let me know if anything comes up.\n\n"
    "Thanks,\nDuncan Richardson"
)

FALLBACK_EMAIL_SUBJECT = "Prospective Practitioner — {name}"
FALLBACK_EMAIL_BODY = (
    "Howdy. Could you please reach out to {name} to try and find a time for a stretch? "
    "I wasn't able to find a time that worked for them.\n\n"
    "Candidate information:\n"
    "Name: {name}\n"
    "Phone: {phone}\n"
    "Email: {email}"
)


def send_availability_request(applicant: dict, preferred_channel: str | None = None) -> bool:
    """
    Send Option D availability request to the candidate.
    Sets scheduling_requested_at. Returns True if a message was sent.
    """
    applicant_id = applicant["id"]
    name = applicant.get("name") or ""
    first_name = name.split()[0] if name else "there"
    phone = applicant.get("phone") or ""
    email = applicant.get("email") or ""
    now = datetime.now(timezone.utc).isoformat()

    channel = preferred_channel or ("sms" if phone else "email")
    sent = False

    if channel == "sms" and phone:
        sid = send_sms(phone, AVAIL_SMS)
        if sid:
            insert_message(applicant_id=applicant_id, channel="sms", direction="outbound",
                           body=AVAIL_SMS, external_id=sid, sent_at=now)
            sent = True
    elif channel == "email" and email:
        body = AVAIL_EMAIL.format(first_name=first_name)
        msg_id = send_email(email, AVAIL_REQUEST_SUBJECT, body)
        if msg_id:
            insert_message(applicant_id=applicant_id, channel="email", direction="outbound",
                           body=body, subject=AVAIL_REQUEST_SUBJECT, external_id=msg_id, sent_at=now)
            sent = True

    if sent:
        db.table("applicants").update({"scheduling_requested_at": now}).eq("id", applicant_id).execute()
        logger.info("Availability request sent to %s via %s", name, channel)

    return sent


def _format_booking_strings(date_str: str, time_str: str) -> dict:
    """Return {day_date, time} for confirmation messages."""
    try:
        dt = datetime.strptime(date_str, "%m/%d/%Y")
        day_date = dt.strftime("%A, %B %d")
    except Exception:
        day_date = date_str
    return {"day_date": day_date, "time": time_str}


def _send_confirmation(applicant: dict, date_str: str, time_str: str,
                        preferred_channel: str | None) -> None:
    """Send booking confirmation to candidate via preferred channel."""
    applicant_id = applicant["id"]
    name = applicant.get("name") or ""
    first_name = name.split()[0] if name else "there"
    phone = applicant.get("phone") or ""
    email = applicant.get("email") or ""
    now = datetime.now(timezone.utc).isoformat()
    fmt = _format_booking_strings(date_str, time_str)

    channel = preferred_channel or ("sms" if phone else "email")

    if channel == "sms" and phone:
        body = CONFIRM_SMS.format(**fmt)
        sid = send_sms(phone, body)
        if sid:
            insert_message(applicant_id=applicant_id, channel="sms", direction="outbound",
                           body=body, external_id=sid, sent_at=now)
    elif channel == "email" and email:
        body = CONFIRM_EMAIL.format(first_name=first_name, **fmt)
        msg_id = send_email(email, "You're confirmed — Stretch Zone", body)
        if msg_id:
            insert_message(applicant_id=applicant_id, channel="email", direction="outbound",
                           body=body, subject="You're confirmed — Stretch Zone",
                           external_id=msg_id, sent_at=now)


def _send_fallback(applicant: dict) -> None:
    """Send fallback email to boise@stretchzone.com when no slot matched."""
    name = applicant.get("name") or "Unknown"
    phone = applicant.get("phone") or ""
    email = applicant.get("email") or ""
    subject = FALLBACK_EMAIL_SUBJECT.format(name=name)
    body = FALLBACK_EMAIL_BODY.format(name=name, phone=phone, email=email)
    send_email(config.CLUBREADY_FALLBACK_EMAIL, subject, body)
    now = datetime.now(timezone.utc).isoformat()
    db.table("applicants").update({"scheduling_fallback_sent_at": now}).eq("id", applicant["id"]).execute()
    logger.info("Fallback email sent to %s for %s", config.CLUBREADY_FALLBACK_EMAIL, name)


def _get_inbound_after(applicant_id: str, after_ts: str) -> list[dict]:
    """Return inbound messages received after after_ts."""
    resp = (
        db.table("messages")
        .select("body, channel, direction, sent_at, created_at")
        .eq("applicant_id", applicant_id)
        .eq("direction", "inbound")
        .gt("created_at", after_ts)
        .order("created_at", desc=False)
        .execute()
    )
    return resp.data or []


def _first_inbound_channel(applicant_id: str) -> str | None:
    """Return channel of the first inbound message for this applicant."""
    resp = (
        db.table("messages")
        .select("channel")
        .eq("applicant_id", applicant_id)
        .eq("direction", "inbound")
        .order("created_at", desc=False)
        .limit(1)
        .execute()
    )
    if resp.data:
        return resp.data[0]["channel"]
    return None


def process_scheduling_replies() -> int:
    """
    For all candidates awaiting scheduling:
    - Find their inbound reply after scheduling_requested_at
    - Parse with Claude
    - Try ClubReady booking
    - Confirm or send fallback
    Returns count of processed candidates.
    """
    resp = (
        db.table("applicants")
        .select("id, name, email, phone, scheduling_requested_at")
        .not_.is_("scheduling_requested_at", "null")
        .is_("scheduled_block_at", "null")
        .is_("scheduling_fallback_sent_at", "null")
        .neq("archived", True)
        .execute()
    )
    candidates = resp.data or []
    if not candidates:
        return 0

    processed = 0
    for c in candidates:
        replies = _get_inbound_after(c["id"], c["scheduling_requested_at"])
        if not replies:
            continue

        # Use the most recent reply
        reply_text = replies[-1]["body"] or ""
        if not reply_text.strip():
            continue

        pref_ch = _first_inbound_channel(c["id"])
        slots = parse_availability(reply_text)

        booked = False
        for slot in slots:
            date_str = slot.get("date", "")
            time_str = slot.get("time", "")
            if not date_str or not time_str:
                continue
            try:
                ok = block_time(
                    date_str=date_str,
                    time_str=time_str,
                    candidate_name=c.get("name") or "",
                    phone=c.get("phone") or "",
                    email=c.get("email") or "",
                )
            except Exception:
                logger.exception("block_time raised for %s slot %s %s", c["id"], date_str, time_str)
                ok = False

            if ok:
                now = datetime.now(timezone.utc).isoformat()
                db.table("applicants").update({
                    "scheduled_block_at": now,
                    "calendly_booked": True,
                }).eq("id", c["id"]).execute()
                _send_confirmation(c, date_str, time_str, pref_ch)
                logger.info("Booked %s for %s at %s", c.get("name"), date_str, time_str)
                booked = True
                break

        if not booked:
            _send_fallback(c)

        processed += 1

    logger.info("Scheduling run complete: %d processed", processed)
    return processed
