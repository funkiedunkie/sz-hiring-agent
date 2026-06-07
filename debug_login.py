"""Verify two-step CareerPlug login and show what the app page looks like."""
import os
from dotenv import load_dotenv
load_dotenv()

from playwright.sync_api import sync_playwright

EMAIL = os.getenv("CAREERPLUG_EMAIL")
PASSWORD = os.getenv("CAREERPLUG_PASSWORD")
APP_URL = "https://app.careerplug.com/manage/apps/150511565"

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page()

    # Step 1: email
    print("Navigating to sign_in...")
    page.goto("https://app.careerplug.com/user/sign_in")
    page.wait_for_selector('input[name="user[login]"]', timeout=15000)
    page.fill('input[name="user[login]"]', EMAIL)
    page.click('input[id="user_continue_action"]')

    # Step 2: password
    print("Waiting for password field...")
    page.wait_for_selector('input[name="user[password]"]', timeout=15000)
    page.fill('input[name="user[password]"]', PASSWORD)
    page.click('input[type="submit"]')

    print("Waiting for dashboard...")
    page.wait_for_url("**/manage*", timeout=20000)
    print(f"Logged in! URL: {page.url}")

    # Navigate to the app overview
    print(f"\nNavigating to: {APP_URL}")
    page.goto(APP_URL)
    page.wait_for_load_state("networkidle")
    print(f"Landed on: {page.url}")

    with open("debug_output.txt", "w", encoding="utf-8") as f:
        # Overview tab
        f.write("=== OVERVIEW PAGE TEXT ===\n")
        f.write(page.inner_text("body").strip())

        # Specific selectors
        f.write("\n\n=== SELECTOR RESULTS ===\n")
        for sel in [".profile-show__applicant-name", ".profile-show__job-name",
                    ".profile-show__email a", ".profile-show__phone a"]:
            el = page.query_selector(sel)
            val = el.inner_text().strip() if el else "NOT FOUND"
            f.write(f"{sel}: {val!r}\n")

        # Applicant evaluation tab (prescreen answers)
        eval_url = APP_URL + "?tab=applicant_evaluation"
        print(f"Navigating to eval tab: {eval_url}")
        page.goto(eval_url)
        page.wait_for_load_state("networkidle")
        f.write("\n\n=== APPLICANT EVALUATION TAB TEXT ===\n")
        f.write(page.inner_text("body").strip())

        # Try candidate selectors for the prescreen section
        f.write("\n\n=== EVAL TAB SELECTOR PROBES ===\n")
        eval_selectors = [
            ".prescreen-results",
            ".prescreen-response",
            ".applicant-evaluation",
            "[data-testid='prescreen-results']",
            ".question-response",
            ".applicant-answers",
            ".prescreen",
            ".prescreen-answer",
            ".response-text",
            "#applicant_evaluation",
            ".profile-tabs-content",
            ".tab-content",
            ".tab-pane.active",
            ".tab-pane",
        ]
        for sel in eval_selectors:
            els = page.query_selector_all(sel)
            if els:
                text = " | ".join(e.inner_text().strip()[:100] for e in els[:3])
                f.write(f"FOUND {sel}: {text!r}\n")
            else:
                f.write(f"not found: {sel}\n")

    print("Output written to debug_output.txt")
    browser.close()
