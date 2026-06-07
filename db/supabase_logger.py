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
) -> dict[str, Any]:
    """Insert a new applicant row and return the inserted record."""
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
