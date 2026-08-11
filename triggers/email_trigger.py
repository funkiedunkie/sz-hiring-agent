import requests
import time
import os
import re
import logging
from datetime import datetime, timedelta, timezone
from config import (
    GRAPH_TENANT_ID,
    GRAPH_CLIENT_ID,
    GRAPH_CLIENT_SECRET,
    GRAPH_USER_EMAIL,
    EMAIL_TRIGGER_SUBJECT,
)

# How far back to consider CareerPlug notification emails. The agent no longer
# uses the mailbox read flag as its work queue (see fetch_new_applications),
# so this window plus the Supabase dedup in main.py bounds the work per run.
TRIGGER_LOOKBACK_DAYS = int(os.getenv("TRIGGER_LOOKBACK_DAYS", "7"))

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


def _received_at(message) -> datetime:
    """Parse a Graph receivedDateTime ('2026-08-10T22:15:31Z') into an aware datetime."""
    raw = message.get("receivedDateTime", "")
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        # Unparseable timestamp: treat as brand new so we process rather than drop.
        return datetime.now(timezone.utc)


def fetch_new_applications(token):
    """Return recent CareerPlug applicant-notification emails.

    CareerPlug uses two subject templates:
      - 'Name - New Applicant for <job>'            (EMAIL_TRIGGER_SUBJECT)
      - 'Name - New Fast Track Applicant for <job>' (always searched)
    Both are searched and merged so neither template is missed.

    This deliberately does NOT filter on ``isRead``. The mailbox read flag is
    shared state with a human: when Duncan opened a CareerPlug notification in
    Outlook before the next cron tick, the email became invisible to the agent
    permanently and the candidate was silently dropped (this lost Sage Moore on
    2026-08-10). Idempotency now comes from Supabase instead — ``main.py`` skips
    any application URL already logged — so re-seeing a handled email is a
    cheap no-op while an unhandled one keeps getting retried.

    Results are restricted to genuine applicant notifications (subject must
    parse to a name) received within TRIGGER_LOOKBACK_DAYS.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=TRIGGER_LOOKBACK_DAYS)
    seen_ids = set()
    messages = []
    for term in [EMAIL_TRIGGER_SUBJECT, "New Fast Track Applicant"]:
        for m in _search_inbox(token, term):
            if m["id"] in seen_ids:
                continue
            seen_ids.add(m["id"])
            # The Graph $search is fuzzy and also matches digests such as
            # 'New text messages from your applicants'. Requiring the subject to
            # parse into an applicant name filters those out silently.
            if not extract_applicant_name(m.get("subject", "")):
                continue
            if _received_at(m) < cutoff:
                continue
            messages.append(m)
    return messages


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


def _resolve_wrapped_app_url(body_content: str) -> str | None:
    """Follow ProofPoint / CareerPlug click-tracking links to recover the app URL.

    CareerPlug notification emails to this mailbox are rewritten by ProofPoint
    URL Defense, so the raw https://app.careerplug.com/manage/apps/<id> link is
    never present in the body — only wrapped redirects are. We GET each wrapped
    link (they 302 through email.reply.careerplug.com to the real app URL before
    landing on the sign-in page) and pick the manage/apps/<id> URL seen anywhere
    in the redirect chain. Best-effort: any failure returns None so the caller
    falls back to name search.
    """
    body = body_content.replace("&amp;", "&")
    wrapped = re.findall(r"https://urldefense\.proofpoint\.com/v2/url\?[^\s\"'<]+", body)
    wrapped += re.findall(r"https://email\.reply\.careerplug\.com/c/[^\s\"'<&]+", body)
    for link in wrapped:
        try:
            resp = requests.get(link, allow_redirects=True, timeout=20)
        except Exception as e:
            logger.warning("Could not follow wrapped CareerPlug link: %s", e)
            continue
        chain = " ".join([h.url for h in resp.history] + [resp.url])
        match = re.search(r"https://app\.careerplug\.com/manage/apps/\d+", chain)
        if match:
            return match.group(0)
    return None


def extract_app_url(body_content: str) -> str | None:
    """Extract the CareerPlug application URL from an email body (HTML or plain text).

    Tries a direct match first; if the URL is ProofPoint-wrapped (the usual case
    for this mailbox), follows the redirect chain to recover it.
    """
    match = re.search(r"https://app\.careerplug\.com/manage/apps/(\d+)", body_content)
    if match:
        return match.group(0)
    return _resolve_wrapped_app_url(body_content)


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
