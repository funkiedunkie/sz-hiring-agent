"""Entry point for the Stretch Zone 1082 hiring agent."""

import argparse
import logging
import os
import sys

import config
from agents.resume_scorer import score_candidate
from db.supabase_logger import log_applicant
from notifications.email_sender import send_outreach_email
from notifications.sms_sender import send_interview_invite
from scrapers.careerplug import scrape_application
from triggers.email_trigger import poll_once

_log_dir = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(_log_dir, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(_log_dir, "agent.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


def run(dry_run: bool = False) -> None:
    if dry_run:
        logger.info("*** DRY RUN -- no SMS, email, or Supabase writes ***")
    logger.info("Hiring agent starting")

    trigger_emails = poll_once(mark_seen=not dry_run)
    if not trigger_emails:
        logger.info("No trigger emails found -- nothing to do")
        return

    logger.info("%d trigger email(s) found", len(trigger_emails))

    # Deduplicate by URL — multiple emails can point to the same application
    seen_urls: set[str] = set()
    unique_triggers = []
    for t in trigger_emails:
        if t.app_url not in seen_urls:
            seen_urls.add(t.app_url)
            unique_triggers.append(t)
    if len(unique_triggers) < len(trigger_emails):
        logger.info("Deduplicated to %d unique URL(s)", len(unique_triggers))
    trigger_emails = unique_triggers

    for trigger in trigger_emails:
        logger.info("Processing: %s -> %s", trigger.subject, trigger.app_url)

        try:
            applicant = scrape_application(trigger.app_url, headless=True)
        except Exception as exc:
            logger.error("Scrape failed for %s: %s", trigger.app_url, exc)
            continue

        logger.info("Scraped: %s | %s | %s", applicant.name, applicant.email, applicant.phone)

        result = score_candidate(
            application_text=applicant.application_text,
            candidate_name=applicant.name,
        )

        stars = "*" * result.score if result.score else "AUTO-DQ"  # avoid emoji for Windows cp1252
        logger.info("Score: %s | auto_dq: %s | %s", stars, result.auto_disqualified, result.reasoning[:120])

        sms_sid = ""
        if not result.auto_disqualified and result.score >= config.SCORE_NOTIFY_THRESHOLD:
            if dry_run:
                logger.info("[DRY RUN] Would send SMS + email to %s (%s / %s)",
                            applicant.name, applicant.phone, applicant.email)
            else:
                logger.info("%s scored %d stars -- sending outreach", applicant.name, result.score)
                sms_sid = send_interview_invite(candidate_name=applicant.name, candidate_phone=applicant.phone)
                send_outreach_email(candidate_name=applicant.name, candidate_email=applicant.email)
        else:
            reason = "auto-disqualified" if result.auto_disqualified else "score %d < threshold %d" % (result.score, config.SCORE_NOTIFY_THRESHOLD)
            logger.info("Skipping outreach for %s (%s)", applicant.name, reason)

        if dry_run:
            logger.info("[DRY RUN] Would log to Supabase: %s | score=%s | auto_dq=%s",
                        applicant.name, result.score, result.auto_disqualified)
        else:
            log_applicant(
                name=applicant.name,
                email=applicant.email,
                phone=applicant.phone,
                profile_url=applicant.profile_url,
                application_text=applicant.application_text,
                score=result.score,
                auto_disqualified=result.auto_disqualified,
                reasoning=result.reasoning,
                score_model=result.model,
                sms_sid=sms_sid,
                trigger_subject=trigger.subject,
            )

    logger.info("Hiring agent finished")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Scrape and score but skip SMS, email, and Supabase writes")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
