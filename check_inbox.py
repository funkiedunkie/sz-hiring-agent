"""Quick check of unread inbox emails."""
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
resp = requests.get(
    f"https://graph.microsoft.com/v1.0/users/{config.GRAPH_USER_EMAIL}/mailFolders/inbox/messages",
    headers={"Authorization": f"Bearer {token}"},
    params={"$filter":"isRead eq false","$select":"subject,receivedDateTime,from","$orderby":"receivedDateTime desc","$top":10}
)
msgs = resp.json().get("value", [])
print(f"Unread messages: {len(msgs)}")
for m in msgs:
    print(f"  [{m['receivedDateTime']}] {m['subject']}")
