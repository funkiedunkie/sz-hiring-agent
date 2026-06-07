"""Mark all unread 'New Application' emails as read to clear the queue."""
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
headers = {"Authorization": f"Bearer {token}"}

resp = requests.get(
    f"https://graph.microsoft.com/v1.0/users/{config.GRAPH_USER_EMAIL}/mailFolders/inbox/messages",
    headers=headers,
    params={"$filter":"isRead eq false","$select":"id,subject","$top":25}
)
msgs = resp.json().get("value", [])
target = "New Application"
marked = 0
for m in msgs:
    if target.lower() in m.get("subject","").lower():
        requests.patch(
            f"https://graph.microsoft.com/v1.0/users/{config.GRAPH_USER_EMAIL}/messages/{m['id']}",
            headers={**headers,"Content-Type":"application/json"},
            json={"isRead": True}
        )
        print(f"Marked read: {m['subject']}")
        marked += 1
print(f"Done — {marked} email(s) marked read.")
