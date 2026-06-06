"""
Entry point for the hiring agent.

Flow:
  1. Email trigger  — detect a "New Application" notification in the inbox
  2. CareerPlug scraper — pull new applicants from the ATS
  3. Claude scorer  — score each resume against the job title
  4. Supabase logger — persist every result
  5. Twilio SMS     — alert when score >= SCORE_NOTIFY_THRESHOLD
"""

import logging
import sys

import config
from agents.resume_scorer import score_resume
from db.supabase_logger import log_applicant
from notifications.sms_sender import send_candidate_alert
from scrapers.careerplug import fetch_new_applicants
from triggers.email_trigger import poll_once

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def run() -> None:
    logger.info("Hiring agent starting")

    # ── Step 1: check email inbox for trigger emails ──────────────────────────
    trigger_emails = list(poll_once(mark_seen=True))

    if not trigger_emails:
        logger.info("No trigger emails found — nothing to do")
        return

    logger.info("%d trigger email(s) found, fetching applicants…", len(trigger_emails))

    # ── Step 2: scrape CareerPlug for new applicants ─────────────────────────
    applicants = fetch_new_applicants(headless=True)

    if not applicants:
        logger.info("No new applicants found in CareerPlug")
        return

    logger.info("Processing %d applicant(s)…", len(applicants))

    for applicant in applicants:
        logger.info("Scoring: %s — %s", applicant.name, applicant.job_title)

        # ── Step 3: score with Claude ─────────────────────────────────────────
        result = score_resume(
            resume_text=applicant.resume_text,
            job_title=applicant.job_title,
        )

        # ── Step 4: log to Supabase ───────────────────────────────────────────
        trigger_subject = trigger_emails[0].subject if trigger_emails else ""
        sms_sid = ""

        # ── Step 5: SMS if score meets threshold ──────────────────────────────
        if result.score >= config.SCORE_NOTIFY_THRESHOLD:
            logger.info(
                "Score %d >= threshold %d — sending SMS", result.score, config.SCORE_NOTIFY_THRESHOLD
            )
            sms_sid = send_candidate_alert(
                candidate_name=applicant.name,
                job_title=applicant.job_title,
                score=result.score,
                rationale=result.rationale,
                profile_url=applicant.profile_url,
            )

        log_applicant(
            name=applicant.name,
            email=applicant.email,
            job_title=applicant.job_title,
            applied_at=applicant.applied_at,
            resume_text=applicant.resume_text,
            profile_url=applicant.profile_url,
            score=result.score,
            rationale=result.rationale,
            score_model=result.model,
            sms_sid=sms_sid,
            trigger_subject=trigger_subject,
        )

    logger.info("Hiring agent finished")


if __name__ == "__main__":
    run()
