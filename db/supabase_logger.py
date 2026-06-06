"""
Persists applicant records and scoring results to Supabase.

Expected table schema (run once in the Supabase SQL editor):

    create table if not exists applicants (
        id              uuid primary key default gen_random_uuid(),
        created_at      timestamptz default now(),
        name            text not null,
        email           text,
        job_title       text,
        applied_at      text,
        resume_text     text,
        profile_url     text,
        score           int,
        rationale       text,
        score_model     text,
        sms_sid         text,
        trigger_subject text
    );
"""

import logging
from typing import Any

from supabase import create_client, Client

import config

logger = logging.getLogger(__name__)

_client: Client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)

TABLE = "applicants"


def log_applicant(
    name: str,
    email: str,
    job_title: str,
    applied_at: str,
    resume_text: str,
    profile_url: str,
    score: int,
    rationale: str,
    score_model: str,
    sms_sid: str = "",
    trigger_subject: str = "",
) -> dict[str, Any]:
    record = {
        "name": name,
        "email": email,
        "job_title": job_title,
        "applied_at": applied_at,
        "resume_text": resume_text,
        "profile_url": profile_url,
        "score": score,
        "rationale": rationale,
        "score_model": score_model,
        "sms_sid": sms_sid,
        "trigger_subject": trigger_subject,
    }

    response = _client.table(TABLE).insert(record).execute()

    inserted = response.data[0] if response.data else {}
    logger.info("Logged applicant '%s' → row id: %s", name, inserted.get("id"))
    return inserted
