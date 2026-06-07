"""
Scrapes a specific CareerPlug application page using Playwright.

Confirmed selectors (verified 2026-06-07 against live app):
  .profile-show__applicant-name   → candidate name
  .profile-show__job-name         → "Applied for: <title>"
  .profile-show__email a          → email address
  .profile-show__phone a          → phone number
  .prescreen-results              → prescreen Q&A (on ?tab=applicant_evaluation)

Login flow: /user/sign_in → fill email → click #user_continue_action
            → fill password → click input[type=submit] → wait for **/manage**

Usage:
    applicant = scrape_application("https://app.careerplug.com/manage/apps/150511565")
"""

import logging
from dataclasses import dataclass, field

from playwright.sync_api import sync_playwright, Page, Browser

import config

logger = logging.getLogger(__name__)

CAREERPLUG_BASE = "https://app.careerplug.com"


@dataclass
class Applicant:
    name: str
    email: str
    phone: str
    job_title: str
    application_text: str       # prescreen Q&A from applicant_evaluation tab
    profile_url: str
    raw_body: str = field(default_factory=str, repr=False)


def _login(page: Page) -> None:
    page.goto(f"{CAREERPLUG_BASE}/user/sign_in")
    page.wait_for_selector('input[name="user[login]"]', timeout=15_000)
    page.fill('input[name="user[login]"]', config.CAREERPLUG_EMAIL)
    page.click('input[id="user_continue_action"]')
    page.wait_for_selector('input[name="user[password]"]', timeout=15_000)
    page.fill('input[name="user[password]"]', config.CAREERPLUG_PASSWORD)
    page.click('input[type="submit"]')
    page.wait_for_url("**/manage**", timeout=20_000)
    logger.info("Logged in to CareerPlug")


def _text(page: Page, selector: str) -> str:
    el = page.query_selector(selector)
    return el.inner_text().strip() if el else ""


def scrape_application(application_url: str, headless: bool = True) -> Applicant:
    """
    Log in to CareerPlug and scrape the application at *application_url*.
    Returns an Applicant with name, email, phone, job_title, and prescreen Q&A.
    """
    with sync_playwright() as pw:
        browser: Browser = pw.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()

        try:
            _login(page)

            # ── Overview tab: name, email, phone, job title ───────────────────
            logger.info("Navigating to application: %s", application_url)
            page.goto(application_url)
            page.wait_for_load_state("networkidle")

            raw_body = page.inner_text("body").strip()

            name = _text(page, ".profile-show__applicant-name")
            email = _text(page, ".profile-show__email a")
            phone = _text(page, ".profile-show__phone a")

            job_raw = _text(page, ".profile-show__job-name")
            # Strip the "Applied for: " prefix CareerPlug prepends
            job_title = job_raw.removeprefix("Applied for:").strip()

            # ── Applicant evaluation tab: prescreen Q&A ───────────────────────
            eval_url = application_url.split("?")[0] + "?tab=applicant_evaluation"
            page.goto(eval_url)
            page.wait_for_load_state("networkidle")

            application_text = _text(page, ".prescreen-results")
            if not application_text:
                # Fallback: use overview body text (will include garbled PDF chars)
                logger.warning("No .prescreen-results found for %s — using body text fallback", name)
                application_text = raw_body

            logger.info("Scraped: %s | %s | %s | %s", name, email, phone, job_title)
            return Applicant(
                name=name,
                email=email,
                phone=phone,
                job_title=job_title,
                application_text=application_text,
                profile_url=application_url,
                raw_body=raw_body,
            )

        finally:
            browser.close()
