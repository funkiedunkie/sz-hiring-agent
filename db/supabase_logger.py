"""
Persists applicant records and scoring results to Supabase.

Expected table schema (run once in the Supabase SQL editor):

    create table if not exists applicants (
        id                uuid primary key default gen_random_uuid(),
        created_at        timestamptz default now(),
        name              text not null,
        email             text,
        phone             text,
        profile_url       text,
        application_text  text,
        score             int,          -- 1–4 stars; 0 = auto-disqualified
        auto_disqualified boolean default false,
        reasoning         text,
        score_model       text,
        sms_sid           text,
        trigger_subject   text
    );
"""

import logging
from typing import Any

from supabase import create_client, Client

import config

logger = logging.getLogger(__name__)

_client: Client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)

TABLE = "applicants"


def check_applicant_exists(profile_url: str) -> bool:
    """Return True if an applicant with this profile_url is already in the database."""
    resp = _client.table(TABLE).select("id").eq("profile_url", profile_url).execute()
    return len(resp.data) > 0


def check_trigger_subject_processed(trigger_subject: str) -> bool:
    """Return True if an applicant was already logged from this trigger email subject.

    Backstop for the profile_url dedup. When the CareerPlug link in a notification
    email can't be resolved (the ProofPoint click-tracking redirect is flaky), the
    trigger has no app_url to dedup on and would re-scrape the same candidate on
    every cron tick. The subject line ('Sage Moore - New Applicant for ...') is
    stored on insert and is stable per notification, so it identifies work already
    done without needing the URL. Blank subjects never match.
    """
    if not trigger_subject:
        return False
    resp = _client.table(TABLE).select("id").eq("trigger_subject", trigger_subject).execute()
    return len(resp.data) > 0


def find_active_applicant_by_contact(email: str, phone: str) -> dict[str, Any] | None:
    """Return an existing non-archived applicant that shares this email or phone.

    Guards against contacting the same person twice when they apply to more than
    one CareerPlug posting: each posting has a distinct profile_url (so the
    profile_url dedup misses it), but the same email/phone means it's one human.
    Returns the earliest matching row, or None. Blank email/phone are ignored.
    """
    filters = []
    if email:
        filters.append(f"email.eq.{email}")
    if phone:
        filters.append(f"phone.eq.{phone}")
    if not filters:
        return None
    resp = (
        _client.table(TABLE)
        .select("*")
        .or_(",".join(filters))
        .neq("archived", True)
        .order("created_at")
        .execute()
    )
    return resp.data[0] if resp.data else None


def get_applicant_by_url(profile_url: str) -> dict[str, Any] | None:
    """Return the applicant row for *profile_url*, or None if not found."""
    resp = _client.table(TABLE).select("*").eq("profile_url", profile_url).execute()
    return resp.data[0] if resp.data else None


def update_contact_info(profile_url: str, email: str, phone: str) -> None:
    """Patch email and phone on an existing applicant row."""
    _client.table(TABLE).update({"email": email, "phone": phone}).eq("profile_url", profile_url).execute()
    logger.info("Patched contact info for %s — email=%s phone=%s", profile_url, email, phone)


def set_invite_sent(profile_url: str, sms_sid: str) -> None:
    """Mark invite as sent (invite_sent_at = now, sms_sid) on an existing row."""
    from datetime import datetime, timezone
    _client.table(TABLE).update({
        "sms_sid": sms_sid,
        "invite_sent_at": datetime.now(timezone.utc).isoformat(),
    }).eq("profile_url", profile_url).execute()
    logger.info("Marked invite sent for %s sms_sid=%s", profile_url, sms_sid)


def log_applicant(
    name: str,
    email: str,
    phone: str,
    profile_url: str,
    application_text: str,
    score: int,
    auto_disqualified: bool,
    reasoning: str,
    score_model: str,
    sms_sid: str = "",
    trigger_subject: str = "",
    invite_sent: bool = False,
) -> dict[str, Any]:
    """Insert a new applicant row and return the inserted record."""
    from datetime import datetime, timezone
    record = {
        "name": name,
        "email": email,
        "phone": phone,
        "profile_url": profile_url,
        "application_text": application_text,
        "score": score,
        "auto_disqualified": auto_disqualified,
        "reasoning": reasoning,
        "score_model": score_model,
        "sms_sid": sms_sid,
        "trigger_subject": trigger_subject,
    }
    if invite_sent:
        record["invite_sent_at"] = datetime.now(timezone.utc).isoformat()

    response = _client.table(TABLE).insert(record).execute()

    inserted = response.data[0] if response.data else {}
    logger.info(
        "Logged applicant '%s' id=%s score=%s auto_dq=%s",
        name,
        inserted.get("id"),
        score,
        auto_disqualified,
    )
    return inserted
