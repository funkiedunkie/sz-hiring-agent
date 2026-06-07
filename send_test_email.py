"""Send a test trigger email to the Outlook inbox via Graph API."""
import requests
from dotenv import load_dotenv
load_dotenv()
import config

TOKEN_URL = f"https://login.microsoftonline.com/{config.GRAPH_TENANT_ID}/oauth2/v2.0/token"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

resp = requests.post(TOKEN_URL, data={
    "grant_type": "client_credentials",
    "client_id": config.GRAPH_CLIENT_ID,
    "client_secret": config.GRAPH_CLIENT_SECRET,
    "scope": "https://graph.microsoft.com/.default",
})
resp.raise_for_status()
token = resp.json()["access_token"]
print("Token OK")

payload = {
    "message": {
        "subject": "New Application",
        "body": {
            "contentType": "Text",
            "content": (
                "You have a new applicant on CareerPlug.\n\n"
                "View application: https://app.careerplug.com/manage/apps/150511565\n"
            ),
        },
        "toRecipients": [{"emailAddress": {"address": config.GRAPH_USER_EMAIL}}],
    },
    "saveToSentItems": "true",
}

r = requests.post(
    f"{GRAPH_BASE}/users/{config.GRAPH_USER_EMAIL}/sendMail",
    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    json=payload,
)
if not r.ok:
    print(f"Error {r.status_code}: {r.text}")
    r.raise_for_status()
print(f"Test email sent to {config.GRAPH_USER_EMAIL} — subject: 'New Application'")
