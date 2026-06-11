"""Follow-up outreach for candidates who haven't replied within 2 business days."""

import logging
from datetime import datetime, timezone, timedelta

import config
from db.supabase_logger import _client as db
from db.messages_logger import insert_message, get_preferred_channel
from notifications.sms_sender import send_sms
from notifications.email_sender import send_email

logger = logging.getLogger(__name__)

FOLLOW_UP_SUBJECT = "Following up — Stretch Practitioner position"


def _sms_body(first_name: str) -> str:
    return (
        f"Hi {first_name}, still interested in Stretch Zone? "
        f"Grab a time here: {config.CALENDLY_LINK} "
        f"or just reply to let me know either way. Thanks, Duncan"
    )


def _email_body(first_name: str) -> str:
    return (
        f"Hi {first_name}, wanted to check in. Are you still open to a quick "
        f"15-minute chat about the Stretch Practitioner role at Stretch Zone? "
        f"Here's my calendar if you'd like to pick a time: {config.CALENDLY_LINK}\n\n"
        f"No pressure, just reply either way and I'll know where things stand.\n\n"
        f"Thanks, Duncan Richardson"
    )


def _add_business_days(start: datetime, days: int) -> datetime:
    current = start
    added = 0
    while added < days:
        current += timedelta(days=1)
        if current.weekday() < 5:  # Mon-Fri only
            added += 1
    return current


def _business_days_since(ts_str: str) -> int:
    """Count business days (Mon-Fri) from ts_str to today."""
    if not ts_str:
        return 0
    try:
        ts = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        days = 0
        current = ts.date()
        end = datetime.now(timezone.utc).date()
        while current < end:
            current += timedelta(days=1)
            if current.weekday() < 5:
                days += 1
        return days
    except Exception:
        return 0


def _stale_days_for_archive(applicant: dict, messages: list) -> int:
    """Business days the ball has been in the candidate's court. Mirrors dashboard logic."""
    if applicant.get("calendly_booked"):
        return 0
    if messages:
        most_recent = messages[-1]
        if most_recent["direction"] == "inbound":
            inbound_ts = most_recent.get("sent_at") or most_recent.get("created_at")
            inbound_age = _business_days_since(inbound_ts)
            if inbound_age <= 5:
                return 0  # recently replied; give Duncan time to respond
            # After 5 days with no reply from us, clock runs from their message
            start = inbound_ts
        else:
            start = most_recent.get("sent_at") or most_recent.get("created_at")
    elif applicant.get("invite_sent_at"):
        start = applicant["invite_sent_at"]
    else:
        start = applicant.get("created_at")
    return _business_days_since(start)


def _is_eligible(applicant: dict, replied_ids: set) -> bool:
    if applicant.get("archived"):
        return False
    if applicant.get("auto_disqualified"):
        return False
    if applicant["id"] in replied_ids:
        return False

    invite_sent = datetime.fromisoformat(applicant["invite_sent_at"].replace("Z", "+00:00"))
    eligible_after = _add_business_days(invite_sent, 2)
    return datetime.now(timezone.utc) >= eligible_after


def send_follow_up(applicant: dict) -> bool:
    """Send a follow-up to one applicant. Returns True if a message was sent."""
    first_name = str(applicant.get("name", "")).split()[0] or "there"
    applicant_id = applicant["id"]
    phone = applicant.get("phone") or ""
    email = applicant.get("email") or ""
    now = datetime.now(timezone.utc).isoformat()

    pref = get_preferred_channel(applicant_id)
    channel = pref or ("sms" if phone else "email")

    sent = False

    if channel == "sms" and phone:
        body = _sms_body(first_name)
        sid = send_sms(phone, body)
        if sid:
            insert_message(applicant_id=applicant_id, channel="sms", direction="outbound",
                           body=body, external_id=sid, sent_at=now)
            sent = True
            logger.info("Follow-up SMS sent to %s (%s)", applicant.get("name"), applicant_id)
    elif channel == "email" and email:
        body = _email_body(first_name)
        msg_id = send_email(email, FOLLOW_UP_SUBJECT, body)
        if msg_id:
            insert_message(applicant_id=applicant_id, channel="email", direction="outbound",
                           body=body, subject=FOLLOW_UP_SUBJECT,
                           external_id=msg_id, sent_at=now)
            sent = True
            logger.info("Follow-up email sent to %s (%s)", applicant.get("name"), applicant_id)

    if sent:
        db.table("applicants").update({"followup_sent_at": now}).eq("id", applicant_id).is_("followup_sent_at", "null").execute()

    return sent


def run_follow_ups() -> int:
    """Send follow-ups to all eligible candidates. Returns count sent."""
    inbound_resp = db.table("messages").select("applicant_id").eq("direction", "inbound").execute()
    replied_ids = {row["applicant_id"] for row in (inbound_resp.data or [])}

    resp = (
        db.table("applicants")
        .select("id, name, email, phone, invite_sent_at, followup_sent_at, archived, auto_disqualified")
        .not_.is_("invite_sent_at", "null")
        .is_("followup_sent_at", "null")
        .neq("archived", True)
        .execute()
    )

    sent_count = 0
    for applicant in (resp.data or []):
        if _is_eligible(applicant, replied_ids):
            if send_follow_up(applicant):
                sent_count += 1

    logger.info("Follow-up run complete: %d sent", sent_count)
    return sent_count


def run_reply_notifications() -> int:
    """
    Text the manager (MANAGER_PHONE) when a candidate replied and the agent
    couldn't handle it autonomously.

    Dedup: only notifies when the most recent inbound message is newer than
    reply_notified_at, so the same reply never triggers two texts.
    Skips candidates who are already fully handled (booked, fallback sent).
    Returns count of notifications sent.
    """
    manager_phone = config.MANAGER_PHONE
    if not manager_phone:
        return 0

    resp = (
        db.table("applicants")
        .select(
            "id, name, reply_notified_at, calendly_booked, scheduled_block_at, "
            "scheduling_fallback_sent_at, auto_disqualified"
        )
        .neq("archived", True)
        .not_.is_("invite_sent_at", "null")
        .execute()
    )
    candidates = resp.data or []
    if not candidates:
        return 0

    ids = [c["id"] for c in candidates]
    msgs_resp = (
        db.table("messages")
        .select("applicant_id, body, sent_at, created_at")
        .in_("applicant_id", ids)
        .eq("direction", "inbound")
        .order("created_at", desc=True)
        .execute()
    )

    # Keep only the most recent inbound per applicant
    latest_inbound: dict[str, dict] = {}
    for m in (msgs_resp.data or []):
        if m["applicant_id"] not in latest_inbound:
            latest_inbound[m["applicant_id"]] = m

    sent_count = 0
    for c in candidates:
        if c.get("auto_disqualified"):
            continue
        if c.get("calendly_booked") or c.get("scheduled_block_at"):
            continue
        if c.get("scheduling_fallback_sent_at"):
            continue

        latest = latest_inbound.get(c["id"])
        if not latest:
            continue

        inbound_ts = latest.get("sent_at") or latest.get("created_at") or ""
        notified_at = c.get("reply_notified_at") or ""

        # Skip if we already notified about this reply (or a newer one)
        if notified_at and inbound_ts <= notified_at:
            continue

        first_name = (str(c.get("name") or "").split()[0]) or "A candidate"
        snippet = (latest.get("body") or "").strip()
        if len(snippet) > 60:
            snippet = snippet[:57] + "..."

        if snippet:
            body = (
                f"Hi Duncan — {first_name} replied: \"{snippet}\" — "
                f"I need your direction. Check the dashboard. — SZ Agent"
            )
        else:
            body = (
                f"Hi Duncan — {first_name} replied and needs a response. "
                f"Check the dashboard. — SZ Agent"
            )

        sid = send_sms(manager_phone, body)
        if sid:
            now = datetime.now(timezone.utc).isoformat()
            db.table("applicants").update({"reply_notified_at": now}).eq("id", c["id"]).execute()
            sent_count += 1
            logger.info("Manager notified about reply from %s", c.get("name"))

    logger.info("Reply notification run complete: %d sent", sent_count)
    return sent_count


def run_auto_archive() -> dict:
    """
    Auto-deactivate and archive candidates stale for 8+ business days.

    - DQ'd candidates: deactivate in CareerPlug ("Did not meet desired qualifications") + archive
    - Invited, unresponsive candidates: deactivate ("Unresponsive") + archive
    Returns {"dq": N, "unresponsive": N}.
    """
    from scrapers.careerplug import deactivate_applicant
    from collections import defaultdict

    # Fetch all non-archived, non-booked candidates
    resp = (
        db.table("applicants")
        .select("id, name, profile_url, created_at, invite_sent_at, auto_disqualified, calendly_booked")
        .neq("archived", True)
        .execute()
    )
    candidates = resp.data or []
    if not candidates:
        return {"dq": 0, "unresponsive": 0}

    # Batch-fetch messages for all candidates
    ids = [c["id"] for c in candidates]
    msgs_resp = (
        db.table("messages")
        .select("applicant_id, direction, sent_at, created_at")
        .in_("applicant_id", ids)
        .order("sent_at", desc=False)
        .execute()
    )
    messages_by_id: dict[str, list] = defaultdict(list)
    for m in (msgs_resp.data or []):
        messages_by_id[m["applicant_id"]].append(m)

    dq_count = unresponsive_count = 0

    for c in candidates:
        is_dq = bool(c.get("auto_disqualified"))
        has_invite = bool(c.get("invite_sent_at"))

        # Skip candidates who were never invited and aren't DQ'd
        if not is_dq and not has_invite:
            continue

        msgs = messages_by_id[c["id"]]
        days = _stale_days_for_archive(c, msgs)

        if days < 8:
            continue

        reason = "Did not meet desired qualifications" if is_dq else "Unresponsive"
        profile_url = c.get("profile_url") or ""

        if profile_url:
            try:
                deactivate_applicant(profile_url, reason)
                logger.info("Auto-deactivated %s (%s) in CareerPlug: %s", c.get("name"), c["id"], reason)
            except Exception as exc:
                logger.error("CareerPlug deactivation failed for %s: %s", c["id"], exc)

        db.table("applicants").update({"archived": True}).eq("id", c["id"]).execute()
        logger.info("Auto-archived %s (%s) after %d business days stale", c.get("name"), c["id"], days)

        if is_dq:
            dq_count += 1
        else:
            unresponsive_count += 1

    logger.info("Auto-archive run complete: %d DQ'd, %d unresponsive", dq_count, unresponsive_count)
    return {"dq": dq_count, "unresponsive": unresponsive_count}
