"""Entry point for the Stretch Zone hiring agent."""

import argparse
import logging
import os
import sys

import config
from agents.resume_scorer import score_candidate
from db.supabase_logger import (
    log_applicant, check_applicant_exists, find_active_applicant_by_contact,
    check_trigger_subject_processed,
    get_applicant_by_url, update_contact_info, set_invite_sent,
)
from notifications.email_sender import send_outreach_email
from notifications.sms_sender import send_interview_invite
from scrapers.careerplug import scrape_application, scrape_application_by_name
from triggers.email_trigger import poll_once, mark_read

os.makedirs(os.path.join(os.path.dirname(__file__), "logs"), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def process_applicant(name_or_url: str, dry_run: bool, trigger_subject: str = "") -> bool:
    """Scrape, score, and optionally contact a single applicant.

    Returns True if the applicant was fully handled (logged, already logged, or
    dry run) and the trigger email may be marked read; False on a transient
    failure (e.g. the scraper couldn't launch) so the caller leaves the email
    UNREAD and the next cron run retries it instead of dropping the candidate.
    """
    is_url = name_or_url.startswith("https://")

    try:
        if is_url:
            applicant = scrape_application(name_or_url, headless=True)
        else:
            applicant = scrape_application_by_name(name_or_url, headless=True)
    except Exception as exc:
        logger.error("Scrape failed for %r: %s — leaving trigger email unread for retry", name_or_url, exc)
        return False

    logger.info("Scraped: %s | %s | %s", applicant.name, applicant.email, applicant.phone)

    # Dedup: skip if this exact application (profile_url) is already logged
    if not dry_run and check_applicant_exists(applicant.profile_url):
        logger.info("Already processed %s (%s) — skipping", applicant.name, applicant.profile_url)
        return True

    # Dedup: skip if the same person (email/phone) already exists from another
    # posting — otherwise one human gets a second SMS + email invite.
    if not dry_run:
        dup = find_active_applicant_by_contact(applicant.email, applicant.phone)
        if dup:
            logger.info(
                "Duplicate applicant — %s (%s) shares contact with existing '%s' (%s); "
                "skipping outreach and insert to avoid double-contacting the same person",
                applicant.name, applicant.profile_url, dup.get("name"), dup.get("profile_url"),
            )
            return True

    try:
        result = score_candidate(
            application_text=applicant.application_text,
            candidate_name=applicant.name,
        )

        stars = "*" * result.score if result.score else "AUTO-DQ"
        logger.info("Score: %s | auto_dq: %s | %s", stars, result.auto_disqualified, result.reasoning[:120])

        sms_sid = ""
        invite_sent = False
        if not result.auto_disqualified and result.score >= config.SCORE_NOTIFY_THRESHOLD:
            if not applicant.email and not applicant.phone:
                logger.warning("Qualifying candidate %s has no contact info — alerting manager", applicant.name)
                if config.MANAGER_PHONE:
                    from notifications.sms_sender import send_sms
                    send_sms(
                        config.MANAGER_PHONE,
                        f"Hi Duncan — {applicant.name} scored {result.score}★ but I couldn't scrape their contact info from CareerPlug. "
                        f"Please add it manually or run: python main.py --repatch --url {applicant.profile_url} — SZ Agent",
                    )
            elif dry_run:
                logger.info("[DRY RUN] Would send SMS + email to %s (%s / %s)",
                            applicant.name, applicant.phone, applicant.email)
            else:
                logger.info("%s scored %d stars -- sending outreach", applicant.name, result.score)
                sms_sid = send_interview_invite(candidate_name=applicant.name, candidate_phone=applicant.phone)
                send_outreach_email(candidate_name=applicant.name, candidate_email=applicant.email)
                invite_sent = True
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
                trigger_subject=trigger_subject,
                invite_sent=invite_sent,
            )
    except Exception as exc:
        logger.error("Processing failed for %s: %s — leaving trigger email unread for retry", applicant.name, exc)
        return False

    return True


def repatch(url: str) -> None:
    """Re-scrape *url*, update email/phone in Supabase, and send invite if not already sent."""
    try:
        applicant = scrape_application(url, headless=True)
    except Exception as exc:
        logger.error("Re-scrape failed for %r: %s", url, exc)
        return

    logger.info("Re-scraped: %s | email=%r | phone=%r", applicant.name, applicant.email, applicant.phone)

    if not applicant.email and not applicant.phone:
        logger.error("Re-scrape still returned no contact info for %s — check CareerPlug manually", applicant.name)
        return

    existing = get_applicant_by_url(url)
    if not existing:
        logger.error("No existing record for %s — run without --repatch to insert", url)
        return

    update_contact_info(url, email=applicant.email, phone=applicant.phone)

    if (
        not existing.get("invite_sent_at")
        and not existing.get("auto_disqualified")
        and (existing.get("score") or 0) >= config.SCORE_NOTIFY_THRESHOLD
    ):
        logger.info("Sending invite to %s", applicant.name)
        sms_sid = send_interview_invite(candidate_name=applicant.name, candidate_phone=applicant.phone)
        send_outreach_email(candidate_name=applicant.name, candidate_email=applicant.email)
        set_invite_sent(url, sms_sid)
    else:
        logger.info("Invite already sent or candidate doesn't qualify — skipping outreach")


def run(dry_run: bool = False, direct_url: str = "") -> None:
    if dry_run:
        logger.info("*** DRY RUN -- no SMS, email, or Supabase writes ***")
    logger.info("Hiring agent starting")

    if direct_url:
        logger.info("Direct URL mode: %s", direct_url)
        process_applicant(direct_url, dry_run=dry_run, trigger_subject="direct")
        logger.info("Hiring agent finished")
        return

    # Poll without marking anything read. The read flag is a courtesy for the
    # human reading this mailbox, not the work queue — Supabase is the source of
    # truth for what has been handled (see the dedup below).
    trigger_emails = poll_once(mark_seen=False)
    if not trigger_emails:
        logger.info("No trigger emails found -- nothing to do")
        return

    logger.info("%d trigger email(s) found", len(trigger_emails))

    # Deduplicate by applicant name — same person shouldn't appear twice
    seen_names: set[str] = set()
    unique_triggers = []
    for t in trigger_emails:
        key = t.applicant_name.lower()
        if key not in seen_names:
            seen_names.add(key)
            unique_triggers.append(t)
    if len(unique_triggers) < len(trigger_emails):
        logger.info("Deduplicated to %d unique applicant(s)", len(unique_triggers))

    for trigger in unique_triggers:
        # Cheap dedup before the expensive Playwright scrape: if this exact
        # application URL is already in Supabase, there is nothing to do. This is
        # what makes it safe for the trigger to ignore the mailbox read flag.
        if not dry_run and (
            (trigger.app_url and check_applicant_exists(trigger.app_url))
            # No app_url means the CareerPlug link couldn't be resolved and we'd
            # fall back to a name search — dedup on the subject so an already
            # handled candidate isn't re-scraped on every tick.
            or (not trigger.app_url and check_trigger_subject_processed(trigger.subject))
        ):
            logger.info("Already processed %s (%s) — skipping",
                        trigger.applicant_name, trigger.app_url or trigger.subject)
            mark_read(trigger.email_id)
            continue

        name_or_url = trigger.app_url or trigger.applicant_name
        logger.info("Processing: %s -> %s", trigger.subject, name_or_url)
        handled = process_applicant(name_or_url, dry_run=dry_run, trigger_subject=trigger.subject)
        if handled and not dry_run:
            mark_read(trigger.email_id)
        elif not handled:
            logger.warning("Leaving %r unread — will retry next run", trigger.subject)

    logger.info("Hiring agent finished")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Scrape and score but skip SMS, email, and Supabase writes")
    parser.add_argument("--url", default="",
                        help="Process a single CareerPlug app URL directly (bypasses email trigger)")
    parser.add_argument("--repatch", action="store_true",
                        help="Re-scrape --url, update email/phone, and send invite if not already sent")
    args = parser.parse_args()
    if args.repatch:
        if not args.url:
            parser.error("--repatch requires --url")
        repatch(args.url)
    else:
        run(dry_run=args.dry_run, direct_url=args.url)
