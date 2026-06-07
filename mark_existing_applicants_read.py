"""Mark all existing 'Applicant for' emails as read.
Run once to ensure the pipeline only picks up NEW applicants going forward.
"""
from dotenv import load_dotenv; load_dotenv()
import requests, config

def get_token():
    r = requests.post(
        f"https://login.microsoftonline.com/{config.GRAPH_TENANT_ID}/oauth2/v2.0/token",
        data={"grant_type":"client_credentials","client_id":config.GRAPH_CLIENT_ID,
              "client_secret":config.GRAPH_CLIENT_SECRET,"scope":"https://graph.microsoft.com/.default"}
    )
    return r.json()["access_token"]

token = get_token()
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

# Search for all unread applicant notification emails
resp = requests.get(
    f"https://graph.microsoft.com/v1.0/users/{config.GRAPH_USER_EMAIL}/mailFolders/inbox/messages",
    headers=headers,
    params={"$search": '"subject:Applicant for"', "$select": "id,subject,isRead", "$top": 50}
)
msgs = resp.json().get("value", [])
unread = [m for m in msgs if not m.get("isRead", True)]
print(f"Found {len(msgs)} 'Applicant for' emails, {len(unread)} unread.")

marked = 0
for m in unread:
    patch = requests.patch(
        f"https://graph.microsoft.com/v1.0/users/{config.GRAPH_USER_EMAIL}/messages/{m['id']}",
        headers=headers,
        json={"isRead": True}
    )
    if patch.status_code in (200, 204):
        print(f"  Marked read: {m['subject']}")
        marked += 1
    else:
        print(f"  FAILED ({patch.status_code}): {m['subject']}")

print(f"\nDone — {marked} email(s) marked read. Pipeline will only process NEW applicants from here.")
