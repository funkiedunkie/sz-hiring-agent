import requests
import time
import re
import logging
from config import (
    GRAPH_TENANT_ID,
    GRAPH_CLIENT_ID,
    GRAPH_CLIENT_SECRET,
    GRAPH_USER_EMAIL,
    EMAIL_TRIGGER_SUBJECT,
)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
TOKEN_URL = f"https://login.microsoftonline.com/{GRAPH_TENANT_ID}/oauth2/v2.0/token"

logger = logging.getLogger(__name__)


def get_access_token():
    resp = requests.post(TOKEN_URL, data={
        "grant_type": "client_credentials",
        "client_id": GRAPH_CLIENT_ID,
        "client_secret": GRAPH_CLIENT_SECRET,
        "scope": "https://graph.microsoft.com/.default",
    })
    resp.raise_for_status()
    return resp.json()["access_token"]


def _search_inbox(token, subject_term):
    """Fetch inbox messages matching a subject search term, including body for URL extraction."""
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "$search": f'"subject:{subject_term}"',
        "$select": "id,subject,receivedDateTime,isRead,body",
        "$top": 25,
    }
    resp = requests.get(
        f"{GRAPH_BASE}/users/{GRAPH_USER_EMAIL}/mailFolders/inbox/messages",
        headers=headers,
        params=params,
    )
    resp.raise_for_status()
    return resp.json().get("value", [])


def fetch_new_applications(token):
    """Poll inbox for unread CareerPlug notification emails.

    CareerPlug uses two subject templates:
      - 'Name - New Applicant for <job>'           (EMAIL_TRIGGER_SUBJECT)
      - 'Name - New Fast Track Applicant for <job>' (always searched)
    Both are searched and merged so neither template is missed.
    """
    seen_ids = set()
    messages = []
    for term in [EMAIL_TRIGGER_SUBJECT, "New Fast Track Applicant"]:
        for m in _search_inbox(token, term):
            if m["id"] not in seen_ids:
                seen_ids.add(m["id"])
                messages.append(m)
    # Filter to unread only
    return [m for m in messages if not m.get("isRead", True)]


def mark_as_read(token, message_id):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    resp = requests.patch(
        f"{GRAPH_BASE}/users/{GRAPH_USER_EMAIL}/messages/{message_id}",
        headers=headers,
        json={"isRead": True},
    )
    if resp.status_code not in (200, 204):
        logger.warning(
            "Could not mark email %s as read (status %s). "
            "Add Mail.ReadWrite permission to the Azure app to enable this.",
            message_id, resp.status_code
        )


def mark_read(email_id: str) -> None:
    """Mark a single email as read after its applicant is fully handled.

    Called by main.py only once the applicant has been logged (or was already
    logged). Keeping the read-flag until success means a transient failure —
    e.g. the CareerPlug scraper can't launch — leaves the email UNREAD so the
    next cron run retries it, instead of silently dropping the candidate.
    """
    try:
        token = get_access_token()
        mark_as_read(token, email_id)
    except Exception as e:
        logger.error("mark_read error for %s: %s", email_id, e)


def extract_applicant_name(subject: str) -> str | None:
    """Extract 'John Doe' from 'John Doe - New Applicant for Job Title'."""
    match = re.match(r"^(.+?) - New (?:Fast Track )?Applicant for", subject, re.IGNORECASE)
    return match.group(1).strip() if match else None


def extract_app_url(body_content: str) -> str | None:
    """Extract the CareerPlug application URL from an email body (HTML or plain text)."""
    match = re.search(r"https://app\.careerplug\.com/manage/apps/(\d+)", body_content)
    return match.group(0) if match else None


def poll_once(mark_seen: bool = True) -> list:
    """
    Single-shot check: fetch unread trigger emails, optionally mark them read,
    and return a list of TriggerEmail objects with .subject, .applicant_name,
    .app_url, .email_id.  app_url is extracted from the email body so the
    pipeline can use a direct URL instead of a name-based scraper search.
    """
    from dataclasses import dataclass

    @dataclass
    class TriggerEmail:
        subject: str
        applicant_name: str
        email_id: str
        app_url: str | None = None

    results = []
    try:
        token = get_access_token()
        emails = fetch_new_applications(token)
        for email in emails:
            subject = email.get("subject", "")
            name = extract_applicant_name(subject)
            if not name:
                logger.warning("Could not extract applicant name from: %r", subject)
                continue
            body_content = (email.get("body") or {}).get("content", "")
            app_url = extract_app_url(body_content)
            if not app_url:
                logger.warning("No CareerPlug URL found in email body for %r — will fall back to name search", name)
            results.append(TriggerEmail(subject=subject, applicant_name=name, email_id=email["id"], app_url=app_url))
            if mark_seen:
                mark_as_read(token, email["id"])
    except Exception as e:
        logger.error("poll_once error: %s", e)
    return results


def poll_for_applications(callback, interval=60):
    """Continuously poll for new application emails."""
    logger.info("Email trigger running — polling every %ds...", interval)
    while True:
        try:
            token = get_access_token()
            emails = fetch_new_applications(token)
            for email in emails:
                subject = email.get("subject", "")
                email_id = email["id"]
                name = extract_applicant_name(subject)
                if name:
                    logger.info("New application found: %s", subject)
                    callback(subject, name, email_id)
                    mark_as_read(token, email_id)
                else:
                    logger.warning("No applicant name found in: %s", subject)
        except Exception as e:
            logger.error("Email trigger error: %s", e)
        time.sleep(interval)
