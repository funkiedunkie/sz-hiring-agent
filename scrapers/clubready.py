"""
ClubReady scheduling automation — blocks time on the practitioner grid.

Confirmed selectors (verified 2026-06-11 against live app):
  selectfree(date, time, staffId, staffId)  — available time cells
  #blockOutSomeTime                          — "Block Out Some Time" button
  #startHour, #startMinute, #startPeriod    — start time selects (pre-filled)
  #endHour, #endMinute, #endPeriod          — end time selects
  choosecolor(N)                            — color picker (4 = blue)
  #comm                                     — detail textarea
  #make-unavailable-btn                     — submit button

Grid URL: https://app.clubready.com/admin/schedulinggridviewall.asp
Login:    https://stretchzone.clubready.com/
Location: select "Boise" then "Select Location"
"""

import logging
import re
from datetime import datetime, timedelta

from playwright.sync_api import sync_playwright, Page

import config

logger = logging.getLogger(__name__)

GRID_URL = "https://app.clubready.com/admin/schedulinggridviewall.asp"
LOGIN_URL = "https://stretchzone.clubready.com/"

# choosecolor index → color (verified from screenshot 2026-06-11)
COLOR_BLUE = 4


def _login(page: Page) -> None:
    page.goto(LOGIN_URL)
    page.wait_for_load_state("networkidle")
    page.fill("input[name='uid']", config.CLUBREADY_USERNAME)
    page.fill("input[name='pw']", config.CLUBREADY_PASSWORD)
    page.click("input[name='Submit']")
    page.wait_for_load_state("networkidle")
    page.locator("option:has-text('Boise')").first.click()
    page.locator("input[value='Select Location'], button:has-text('Select Location')").first.click()
    page.wait_for_load_state("networkidle")


def _time_to_dt(time_str: str) -> datetime:
    for fmt in ["%I:%M %p", "%I:%M:%S %p", "%H:%M"]:
        try:
            return datetime.strptime(time_str.strip().upper(), fmt)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse time: {time_str!r}")


def _find_selectfree_call(html: str, time_str: str) -> str | None:
    """Return the first selectfree(...) call matching time_str (any staff)."""
    target = _time_to_dt(time_str)
    pattern = r"selectfree\('([^']+)',\s*'([^']+)',\s*(\d+),\s*'(\d+)'\s*\)"
    for cell_date, cell_time, staff_id, _ in re.findall(pattern, html):
        try:
            cell_dt = _time_to_dt(cell_time)
        except ValueError:
            continue
        if abs((cell_dt - target).total_seconds()) < 60:
            return f"selectfree('{cell_date}', '{cell_time}', {staff_id}, '{staff_id}')"
    return None


def _load_grid(page: Page, date_str: str) -> None:
    """Navigate to grid and jump to date_str (e.g. "6/15/2026")."""
    page.goto(GRID_URL)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(2000)
    js_date = f"{date_str} 8:00:00 AM"
    page.evaluate(f"updategrid('{js_date}')")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(3000)


def block_time(
    date_str: str,
    time_str: str,
    candidate_name: str,
    phone: str,
    email: str,
    headless: bool = True,
) -> bool:
    """
    Block 30 minutes in ClubReady for a prospective practitioner.

    date_str: "M/D/YYYY" e.g. "6/15/2026"
    time_str: "H:MM AM/PM" e.g. "10:00 AM"
    Returns True on success.
    """
    if not config.CLUBREADY_USERNAME or not config.CLUBREADY_PASSWORD:
        logger.error("CLUBREADY_USERNAME / CLUBREADY_PASSWORD not set")
        return False

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless, slow_mo=300)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        try:
            _login(page)
            _load_grid(page, date_str)

            html = page.inner_html("body")
            call = _find_selectfree_call(html, time_str)
            if not call:
                logger.warning("No available slot on %s at %s", date_str, time_str)
                return False

            logger.info("Clicking: %s", call)
            page.evaluate(call)
            page.wait_for_timeout(1500)

            # Click "Block Out Some Time"
            page.locator("#blockOutSomeTime").click()
            page.wait_for_timeout(2000)

            # Compute end time (+30 min)
            start_dt = _time_to_dt(time_str)
            end_dt = start_dt + timedelta(minutes=30)
            end_hour = str(int(end_dt.strftime("%I")))   # "10"
            end_minute = str(end_dt.minute)              # "0" or "30"
            end_period = end_dt.strftime("%p").upper()   # "AM" or "PM"

            page.select_option("#endHour", end_hour)
            page.select_option("#endMinute", end_minute)
            page.select_option("#endPeriod", end_period)

            # Choose blue
            page.evaluate(f"choosecolor({COLOR_BLUE})")
            page.wait_for_timeout(300)

            # Fill in candidate detail
            detail = f"Prospective Practitioner- {candidate_name}, {phone}, {email}"
            page.locator("#comm").fill(detail)

            # Submit
            page.locator("#make-unavailable-btn").click()
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(2000)

            logger.info(
                "Booked ClubReady block for %s on %s at %s",
                candidate_name, date_str, time_str,
            )
            return True

        except Exception:
            logger.exception("block_time failed for %s on %s at %s", candidate_name, date_str, time_str)
            return False
        finally:
            browser.close()


def has_available_slot(date_str: str, time_str: str, headless: bool = True) -> bool:
    """Return True if a selectfree cell exists at date_str + time_str."""
    if not config.CLUBREADY_USERNAME or not config.CLUBREADY_PASSWORD:
        return False
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless, slow_mo=200)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        try:
            _login(page)
            _load_grid(page, date_str)
            html = page.inner_html("body")
            return _find_selectfree_call(html, time_str) is not None
        except Exception:
            logger.exception("has_available_slot failed")
            return False
        finally:
            browser.close()
