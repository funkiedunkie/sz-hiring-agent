"""
One-shot backfill for applicants missed while CareerPlug's disqualification
filter was blocking notification emails.

Scrapes every application URL from CareerPlug, skips any already in Supabase,
and runs the full pipeline (score → log → SMS + email) for each new one.

Usage:
    python backfill.py              # live run — scores, logs, sends outreach
    python backfill.py --dry-run    # preview only — no writes, no messages
"""

import argparse
import logging
import os
import sys

import config
from db.supabase_logger import check_applicant_exists
from main import process_applicant
from scrapers.careerplug import scrape_all_app_urls

os.makedirs(os.path.join(os.path.dirname(__file__), "logs"), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def run(dry_run: bool) -> None:
    if dry_run:
        logger.info("*** DRY RUN — no SMS, email, or Supabase writes ***")

    logger.info("Fetching all CareerPlug application URLs …")
    all_urls = scrape_all_app_urls(headless=True)
    logger.info("Total applications found: %d", len(all_urls))

    new_urls = [u for u in all_urls if not check_applicant_exists(u)]
    logger.info("Already in Supabase: %d  |  New to process: %d",
                len(all_urls) - len(new_urls), len(new_urls))

    if not new_urls:
        logger.info("Nothing to backfill — all applicants already logged.")
        return

    for i, url in enumerate(new_urls, 1):
        logger.info("--- [%d/%d] %s", i, len(new_urls), url)
        process_applicant(url, dry_run=dry_run, trigger_subject="backfill")

    logger.info("Backfill complete: processed %d applicant(s).", len(new_urls))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview only — no writes, no outreach messages")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
