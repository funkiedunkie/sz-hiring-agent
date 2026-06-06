"""
Scrapes new applicant data from CareerPlug using Playwright.
Logs in, navigates to the applicant list, and returns structured applicant dicts.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from playwright.sync_api import sync_playwright, Page, Browser

import config

logger = logging.getLogger(__name__)

CAREERPLUG_BASE = "https://app.careerplug.com"


@dataclass
class Applicant:
    name: str
    email: str
    job_title: str
    applied_at: str
    resume_text: str
    profile_url: str
    raw: dict = field(default_factory=dict)


def _login(page: Page) -> None:
    page.goto(f"{CAREERPLUG_BASE}/login")
    page.fill('input[name="email"]', config.CAREERPLUG_EMAIL)
    page.fill('input[name="password"]', config.CAREERPLUG_PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_url(f"**/{config.CAREERPLUG_COMPANY_SLUG}/**", timeout=15_000)
    logger.info("Logged in to CareerPlug")


def _scrape_resume_text(page: Page, profile_url: str) -> str:
    """Navigate to applicant profile and extract any visible resume text."""
    page.goto(profile_url)
    page.wait_for_load_state("networkidle")

    # CareerPlug renders resume content inside a scrollable container.
    # Adjust the selector if the markup changes.
    resume_section = page.query_selector(".resume-content, [data-testid='resume-text']")
    if resume_section:
        return resume_section.inner_text().strip()

    # Fallback: grab all visible body text on the profile page.
    return page.inner_text("body").strip()


def fetch_new_applicants(headless: bool = True) -> list[Applicant]:
    applicants: list[Applicant] = []

    with sync_playwright() as pw:
        browser: Browser = pw.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()

        try:
            _login(page)

            # Navigate to the company applicant inbox (new/unreviewed).
            inbox_url = (
                f"{CAREERPLUG_BASE}/{config.CAREERPLUG_COMPANY_SLUG}"
                "/applicants?status=new"
            )
            page.goto(inbox_url)
            page.wait_for_load_state("networkidle")

            # Each applicant row — adjust selector to match live markup.
            rows = page.query_selector_all("[data-testid='applicant-row'], .applicant-list-item")
            logger.info("Found %d applicant row(s) on page", len(rows))

            for row in rows:
                name_el = row.query_selector(".applicant-name, [data-testid='applicant-name']")
                email_el = row.query_selector(".applicant-email, [data-testid='applicant-email']")
                job_el = row.query_selector(".job-title, [data-testid='job-title']")
                date_el = row.query_selector(".applied-date, [data-testid='applied-date']")
                link_el = row.query_selector("a[href*='applicants']")

                name = name_el.inner_text().strip() if name_el else "Unknown"
                applicant_email = email_el.inner_text().strip() if email_el else ""
                job_title = job_el.inner_text().strip() if job_el else ""
                applied_at = date_el.inner_text().strip() if date_el else ""
                profile_url = ""

                if link_el:
                    href = link_el.get_attribute("href") or ""
                    profile_url = href if href.startswith("http") else f"{CAREERPLUG_BASE}{href}"

                resume_text = ""
                if profile_url:
                    try:
                        resume_text = _scrape_resume_text(page, profile_url)
                    except Exception as exc:
                        logger.warning("Could not scrape resume for %s: %s", name, exc)

                applicants.append(
                    Applicant(
                        name=name,
                        email=applicant_email,
                        job_title=job_title,
                        applied_at=applied_at,
                        resume_text=resume_text,
                        profile_url=profile_url,
                    )
                )

        finally:
            browser.close()

    return applicants
