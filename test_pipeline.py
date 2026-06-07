"""
Direct pipeline test — skips the email trigger and runs scrape → score → (dry) log.
Usage: python test_pipeline.py [app_url]
"""
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

import config
from scrapers.careerplug import scrape_application
from agents.resume_scorer import score_candidate

APP_URL = sys.argv[1] if len(sys.argv) > 1 else "https://app.careerplug.com/manage/apps/150511565"

logger.info("=== PIPELINE DRY-RUN (no Supabase / no SMS / no email) ===")
logger.info("Target: %s", APP_URL)

logger.info("Step 1: Scraping CareerPlug...")
applicant = scrape_application(APP_URL, headless=True)
logger.info("  Name:  %s", applicant.name)
logger.info("  Email: %s", applicant.email)
logger.info("  Phone: %s", applicant.phone)
logger.info("  Job:   %s", applicant.job_title)
logger.info("  App text (%d chars):\n%s", len(applicant.application_text), applicant.application_text[:600])

logger.info("Step 2: Scoring with Claude...")
result = score_candidate(
    application_text=applicant.application_text,
    candidate_name=applicant.name,
)
stars = "*" * result.score if result.score else "AUTO-DQ"
logger.info("  Score:          %s (%d/4)", stars, result.score)
logger.info("  Auto-DQ:        %s", result.auto_disqualified)
logger.info("  Reasoning:      %s", result.reasoning)
logger.info("  Model:          %s", result.model)

threshold_met = not result.auto_disqualified and result.score >= config.SCORE_NOTIFY_THRESHOLD
logger.info("Step 3: Outreach decision (threshold=%d)", config.SCORE_NOTIFY_THRESHOLD)
if threshold_met:
    logger.info("  [DRY RUN] Would SMS %s at %s", applicant.name, applicant.phone)
    logger.info("  [DRY RUN] Would email %s at %s", applicant.name, applicant.email)
else:
    reason = "auto-disqualified" if result.auto_disqualified else f"score {result.score} < threshold {config.SCORE_NOTIFY_THRESHOLD}"
    logger.info("  Skipping outreach: %s", reason)

logger.info("Step 4: [DRY RUN] Would log to Supabase: %s | score=%s | auto_dq=%s",
            applicant.name, result.score, result.auto_disqualified)
logger.info("=== DONE ===")
