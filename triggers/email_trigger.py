import requests
import time
import re
from config import (
    GRAPH_TENANT_ID,
    GRAPH_CLIENT_ID,
    GRAPH_CLIENT_SECRET,
    GRAPH_USER_EMAIL,
    EMAIL_TRIGGER_SUBJECT,
)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
TOKEN_URL = f"https://login.microsoftonline.com/{GRAPH_TENANT_ID}/oauth2/v2.0/token"


def get_access_token():
    resp = requests.post(TOKEN_URL, data={
        "grant_type": "client_credentials",
        "client_id": GRAPH_CLIENT_ID,
        "client_secret": GRAPH_CLIENT_SECRET,
        "scope": "https://graph.microsoft.com/.default",
    })
    resp.raise_for_status()
    return resp.json()["access_token"]


def fetch_new_applications(token):
    """Poll inbox for unread CareerPlug notification emails."""
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "$filter": "isRead eq false",
        "$select": "id,subject,body,receivedDateTime",
        "$orderby": "receivedDateTime desc",
        "$top": 25,
    }
    resp = requests.get(
        f"{GRAPH_BASE}/users/{GRAPH_USER_EMAIL}/mailFolders/inbox/messages",
        headers=headers,
        params=params,
    )
    resp.raise_for_status()
    messages = resp.json().get("value", [])
    # Graph API doesn't support contains() in $filter for subject, so filter here
    return [m for m in messages if EMAIL_TRIGGER_SUBJECT.lower() in m.get("subject", "").lower()]


def mark_as_read(token, message_id):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    requests.patch(
        f"{GRAPH_BASE}/users/{GRAPH_USER_EMAIL}/messages/{message_id}",
        headers=headers,
        json={"isRead": True},
    )


def extract_application_link(email_body):
    """Pull the CareerPlug application URL out of the email body."""
    match = re.search(r"https://app\.careerplug\.com/[^\s\"'<]+", email_body)
    return match.group(0) if match else None


def poll_once(mark_seen: bool = True) -> list:
    """
    Single-shot check: fetch unread trigger emails, optionally mark them read,
    and return a list of simple objects with .subject, .app_url, .email_id.
    """
    from dataclasses import dataclass

    @dataclass
    class TriggerEmail:
        subject: str
        app_url: str
        email_id: str

    results = []
    try:
        token = get_access_token()
        emails = fetch_new_applications(token)
        for email in emails:
            body = email.get("body", {}).get("content", "")
            app_url = extract_application_link(body)
            if app_url:
                results.append(
                    TriggerEmail(
                        subject=email.get("subject", ""),
                        app_url=app_url,
                        email_id=email["id"],
                    )
                )
                if mark_seen:
                    mark_as_read(token, email["id"])
    except Exception as e:
        print(f"poll_once error: {e}")
    return results


def poll_for_applications(callback, interval=60):
    """
    Continuously poll for new application emails.
    Calls callback(candidate_name, application_url, email_id) for each new one.
    """
    print("Email trigger running — polling every 60s...")
    while True:
        try:
            token = get_access_token()
            emails = fetch_new_applications(token)
            for email in emails:
                body = email.get("body", {}).get("content", "")
                app_url = extract_application_link(body)
                subject = email.get("subject", "")
                email_id = email["id"]

                if app_url:
                    print(f"New application found: {subject}")
                    callback(subject, app_url, email_id)
                    mark_as_read(token, email_id)
                else:
                    print(f"No application link found in: {subject}")

        except Exception as e:
            print(f"Email trigger error: {e}")

        time.sleep(interval)