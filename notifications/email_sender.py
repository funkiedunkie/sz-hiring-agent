"""
Sends the outreach email to a qualified candidate via Microsoft Graph API.

Endpoint: POST /users/{GRAPH_USER_EMAIL}/sendMail
Auth:     same client-credentials token used by triggers/email_trigger.py

NOT SMTP. NOT Gmail. No smtplib.
"""

import logging

import requests

import config

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
TOKEN_URL = (
    f"https://login.microsoftonline.com/{config.GRAPH_TENANT_ID}/oauth2/v2.0/token"
)

SUBJECT_TEMPLATE = "Next step — Stretch Practitioner interview"

BODY_TEMPLATE = (
    "{first_name}, thank you for your interest in the Stretch Practitioner position. "
    "After reviewing your application, I'd like to schedule a 15-minute virtual interview. "
    "You can grab a time here: {calendly_link}. "
    "Thanks, Duncan Richardson"
)


def _get_access_token() -> str:
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": config.GRAPH_CLIENT_ID,
            "client_secret": config.GRAPH_CLIENT_SECRET,
            "scope": "https://graph.microsoft.com/.default",
        },
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def send_email(to_email: str, subject: str, body: str) -> str:
    """
    Send a custom email to *to_email* via Graph API (draft → send).
    Returns the Graph message ID on success, or "" on failure.
    Using draft→send instead of sendMail so we capture the message ID
    for deduplication in the messages table.
    """
    if not to_email:
        logger.warning("send_email called with empty address")
        return ""

    try:
        token = _get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        # Step 1: create draft
        draft_resp = requests.post(
            f"{GRAPH_BASE}/users/{config.GRAPH_USER_EMAIL}/messages",
            headers=headers,
            json={
                "subject": subject,
                "body": {"contentType": "Text", "content": body},
                "toRecipients": [{"emailAddress": {"address": to_email}}],
            },
        )
        draft_resp.raise_for_status()
        message_id = draft_resp.json()["id"]

        # Step 2: send the draft
        send_resp = requests.post(
            f"{GRAPH_BASE}/users/{config.GRAPH_USER_EMAIL}/messages/{message_id}/send",
            headers=headers,
        )
        send_resp.raise_for_status()

        logger.info("Email sent to %s — %s (id: %s)", to_email, subject, message_id)
        return message_id
    except requests.HTTPError as exc:
        logger.error(
            "Graph send_email failed to %s: %s — %s",
            to_email,
            exc,
            exc.response.text if exc.response is not None else "",
        )
        return ""
    except Exception as exc:
        logger.error("send_email failed to %s: %s", to_email, exc)
        return ""


def send_outreach_email(candidate_name: str, candidate_email: str) -> bool:
    """
    Send the interview-invite email to *candidate_email*.
    Returns True on success, False on failure.
    """
    if not candidate_email:
        logger.warning("No email address for '%s' — skipping outreach email", candidate_name)
        return False

    first_name = candidate_name.split()[0] if candidate_name else "there"

    body_text = BODY_TEMPLATE.format(
        first_name=first_name,
        calendly_link=config.CALENDLY_LINK,
    )

    payload = {
        "message": {
            "subject": SUBJECT_TEMPLATE,
            "body": {
                "contentType": "Text",
                "content": body_text,
            },
            "toRecipients": [
                {"emailAddress": {"address": candidate_email}}
            ],
        },
        "saveToSentItems": "true",
    }

    try:
        token = _get_access_token()
        resp = requests.post(
            f"{GRAPH_BASE}/users/{config.GRAPH_USER_EMAIL}/sendMail",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        resp.raise_for_status()
        logger.info(
            "Outreach email sent to %s (%s)", candidate_name, candidate_email
        )
        return True
    except requests.HTTPError as exc:
        logger.error(
            "Graph API sendMail failed for %s: %s — %s",
            candidate_name,
            exc,
            exc.response.text if exc.response is not None else "",
        )
        return False
    except Exception as exc:
        logger.error("Outreach email failed for %s: %s", candidate_name, exc)
        return False
