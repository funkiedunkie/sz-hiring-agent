"""
Scrapes a specific CareerPlug application page using Playwright.

Confirmed selectors (verified 2026-06-07 against live app):
  .profile-show__applicant-name   → candidate name
  .profile-show__job-name         → "Applied for: <title>"
  .profile-show__email a          → email address
  .profile-show__phone a          → phone number
  .prescreen-results              → prescreen Q&A (on ?tab=applicant_evaluation)

Apps list: https://app.careerplug.com/manage/apps  (links show applicant names)

Login flow: /user/sign_in → fill email → click #user_continue_action
            → fill password → click input[type=submit] → wait for **/manage**

Usage:
    # By direct URL:
    applicant = scrape_application("https://app.careerplug.com/manage/apps/150726965")
    # By applicant name (searches the apps list):
    applicant = scrape_application_by_name("Duncan Richardson")
"""

import logging
import re
from dataclasses import dataclass, field

from playwright.sync_api import sync_playwright, Page, Browser

import config

logger = logging.getLogger(__name__)

CAREERPLUG_BASE = "https://app.careerplug.com"
APPS_LIST_URL = f"{CAREERPLUG_BASE}/manage/apps"
APPS_LIST_ALL_URL = f"{CAREERPLUG_BASE}/manage/apps?status=all"


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


def _search_apps_page(page: Page, list_url: str, name: str) -> str | None:
    """Search a single apps list page for an applicant by name. Returns URL or None."""
    page.goto(list_url)
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(2500)

    name_lower = name.lower()
    for link in page.query_selector_all('a[href*="/manage/apps/"]'):
        text = link.inner_text().strip()
        href = link.get_attribute("href") or ""
        if name_lower in text.lower() and re.match(r".*/manage/apps/\d+$", href):
            return href if href.startswith("http") else CAREERPLUG_BASE + href
    return None


def _find_app_url_by_name(page: Page, name: str) -> str | None:
    """Navigate to the apps list and return the application URL for the named applicant.
    Tries active apps first, then falls back to all apps (catches non-active statuses)."""
    logger.info("Searching apps list for: %s", name)

    for list_url in (APPS_LIST_URL, APPS_LIST_ALL_URL):
        url = _search_apps_page(page, list_url, name)
        if url:
            logger.info("Found application URL for %s: %s", name, url)
            return url

    logger.warning("No application URL found for name: %s", name)
    return None


def _scrape_from_url(page: Page, application_url: str) -> "Applicant":
    """Scrape a single application page (assumes already logged in)."""
    base_url = application_url.split("?")[0]

    logger.info("Navigating to application: %s", base_url)
    page.goto(base_url)
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(2000)

    # Overview tab: captures the resume PDF text (letter-by-letter but readable)
    raw_body = page.inner_text("body").strip()

    # Take only the first line — CareerPlug sometimes appends status badges
    name = _text(page, ".profile-show__applicant-name").split("\n")[0].strip()
    email = _text(page, ".profile-show__email a")
    phone = _text(page, ".profile-show__phone a")

    job_raw = _text(page, ".profile-show__job-name")
    job_title = job_raw.removeprefix("Applied for:").strip()

    # ── Applicant evaluation tab: prescreen Q&A ───────────────────────
    eval_url = base_url + "?tab=applicant_evaluation"
    page.goto(eval_url)
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(1500)

    prescreen = _text(page, ".prescreen-results")

    # Combine resume (from overview body) with prescreen answers so the scorer
    # has the full picture. The resume renders letter-by-letter in CareerPlug's
    # DOM but inner_text() reassembles it well enough for Claude to parse.
    if prescreen and raw_body:
        application_text = f"PRESCREEN Q&A:\n{prescreen}\n\nRESUME:\n{raw_body}"
    elif prescreen:
        application_text = prescreen
    else:
        application_text = raw_body
        logger.warning("No .prescreen-results found for %s — using body text only", name)

    logger.info("Scraped: %s | %s | %s | %s", name, email, phone, job_title)
    return Applicant(
        name=name,
        email=email,
        phone=phone,
        job_title=job_title,
        application_text=application_text,
        profile_url=base_url,
        raw_body=raw_body,
    )


def scrape_application(application_url: str, headless: bool = True) -> Applicant:
    """Log in to CareerPlug and scrape the application at *application_url*."""
    with sync_playwright() as pw:
        browser: Browser = pw.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()
        try:
            _login(page)
            return _scrape_from_url(page, application_url)
        finally:
            browser.close()


def scrape_application_by_name(applicant_name: str, headless: bool = True) -> Applicant:
    """Log in, find the application URL by applicant name, then scrape it."""
    with sync_playwright() as pw:
        browser: Browser = pw.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()
        try:
            _login(page)
            app_url = _find_app_url_by_name(page, applicant_name)
            if not app_url:
                raise ValueError(f"No application found in CareerPlug for: {applicant_name!r}")
            return _scrape_from_url(page, app_url)
        finally:
            browser.close()
