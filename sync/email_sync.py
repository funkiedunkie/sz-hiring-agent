"""
Syncs email replies to/from known applicant email addresses into the
messages table using Microsoft Graph API.

Searches both the inbox (inbound) and sent items (outbound) folders.
Deduplication is handled by external_id (Graph message ID).
"""

import logging

import requests

import config
from db.supabase_logger import _client as db
from db.messages_logger import insert_message
from notifications.email_sender import _get_access_token

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def _get_applicant_email_map() -> dict[str, str]:
    """Return {lower_email: applicant_id} for all applicants with an email."""
    resp = db.table("applicants").select("id, email").execute()
    return {
        row["email"].lower(): row["id"]
        for row in (resp.data or [])
        if row.get("email")
    }


def _fetch_messages(token: str, folder: str, filter_clause: str) -> list[dict]:
    """Page through a mail folder with a filter, returning all message objects."""
    url = (
        f"{GRAPH_BASE}/users/{config.GRAPH_USER_EMAIL}"
        f"/mailFolders/{folder}/messages"
        f"?$filter={filter_clause}"
        f"&$select=id,subject,body,from,toRecipients,sentDateTime,receivedDateTime"
        f"&$top=50"
    )
    headers = {"Authorization": f"Bearer {token}"}
    results = []

    while url:
        resp = requests.get(url, headers=headers)
        if resp.status_code != 200:
            logger.error("Graph messages fetch failed: %s — %s", resp.status_code, resp.text)
            break
        data = resp.json()
        results.extend(data.get("value", []))
        url = data.get("@odata.nextLink")

    return results


def _sync_folder(
    token: str,
    email_map: dict[str, str],
    folder: str,
    direction: str,
    address_field: str,
) -> int:
    """
    Sync one mail folder for all known applicant addresses.
    address_field: 'from' for inbox (inbound), 'toRecipients' for sentitems (outbound).
    """
    count = 0
    for email, applicant_id in email_map.items():
        if direction == "inbound":
            filter_clause = f"from/emailAddress/address eq '{email}'"
        else:
            filter_clause = f"toRecipients/any(r: r/emailAddress/address eq '{email}')"

        messages = _fetch_messages(token, folder, filter_clause)

        for msg in messages:
            ts = msg.get("sentDateTime") or msg.get("receivedDateTime")
            body = (msg.get("body") or {}).get("content", "")
            subject = msg.get("subject", "")

            result = insert_message(
                applicant_id=applicant_id,
                channel="email",
                direction=direction,
                body=body,
                subject=subject,
                external_id=msg["id"],
                sent_at=ts,
            )
            if result:
                count += 1

    return count


def sync_all() -> dict[str, int]:
    """Sync inbox replies (inbound) and sent items (outbound). Returns counts."""
    email_map = _get_applicant_email_map()
    if not email_map:
        logger.info("No applicants with emails — skipping email sync")
        return {"inbound": 0, "outbound": 0}

    try:
        token = _get_access_token()
    except Exception as exc:
        logger.error("Could not get Graph token for email sync: %s", exc)
        return {"inbound": 0, "outbound": 0}

    inbound = _sync_folder(token, email_map, "inbox", "inbound", "from")
    outbound = _sync_folder(token, email_map, "sentitems", "outbound", "toRecipients")
    logger.info("Email sync complete — inbound: %d, outbound: %d", inbound, outbound)
    return {"inbound": inbound, "outbound": outbound}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(sync_all())
