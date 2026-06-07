"""
Adds Mail.Send application permission to the Azure AD app and grants admin consent.
Requires the app to have Application.ReadWrite.OwnedBy (or .All) in addition to Mail.Read.
If that permission isn't present this script will fail with 403 and print next steps.
"""
import sys
import requests
from dotenv import load_dotenv
load_dotenv()
import config

GRAPH = "https://graph.microsoft.com/v1.0"
TOKEN_URL = f"https://login.microsoftonline.com/{config.GRAPH_TENANT_ID}/oauth2/v2.0/token"

# Well-known IDs (stable across all Azure tenants)
MSGRAPH_APP_ID   = "00000003-0000-0000-c000-000000000000"   # Microsoft Graph
MAIL_SEND_ROLE   = "b633e1c5-b582-4048-a93e-9f11b44c7e96"  # Mail.Send (application)
MAIL_READ_ROLE   = "810c84a8-4a9e-49e6-bf7d-12d183f40d01"  # Mail.Read  (application)

def get_token():
    r = requests.post(TOKEN_URL, data={
        "grant_type": "client_credentials",
        "client_id": config.GRAPH_CLIENT_ID,
        "client_secret": config.GRAPH_CLIENT_SECRET,
        "scope": "https://graph.microsoft.com/.default",
    })
    r.raise_for_status()
    return r.json()["access_token"]

def api(method, path, token, **kwargs):
    r = requests.request(method, f"{GRAPH}{path}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        **kwargs)
    if not r.ok:
        print(f"  {method} {path} -> {r.status_code}: {r.text[:400]}")
        r.raise_for_status()
    return r.json() if r.content else {}

token = get_token()
print(f"Token OK (tenant {config.GRAPH_TENANT_ID})")

# ── Step 1: find the app registration object ID ───────────────────────────────
resp = api("GET", f"/applications?$filter=appId eq '{config.GRAPH_CLIENT_ID}'", token)
apps = resp.get("value", [])
if not apps:
    sys.exit("App not found — check GRAPH_CLIENT_ID in .env")
app = apps[0]
obj_id = app["id"]
print(f"App: {app['displayName']!r}  objectId={obj_id}")

# ── Step 2: check current requiredResourceAccess ──────────────────────────────
current = app.get("requiredResourceAccess", [])
graph_entry = next((e for e in current if e["resourceAppId"] == MSGRAPH_APP_ID), None)

existing_roles = {r["id"] for r in graph_entry.get("resourceAccess", [])} if graph_entry else set()
print(f"Current Graph roles: {existing_roles}")

if MAIL_SEND_ROLE in existing_roles:
    print("Mail.Send already present in requiredResourceAccess — skipping PATCH")
else:
    # Build updated resourceAccess list
    new_roles = list(existing_roles | {MAIL_READ_ROLE, MAIL_SEND_ROLE})
    updated_access = [{"id": rid, "type": "Role"} for rid in new_roles]

    new_required = [e for e in current if e["resourceAppId"] != MSGRAPH_APP_ID]
    new_required.append({"resourceAppId": MSGRAPH_APP_ID, "resourceAccess": updated_access})

    api("PATCH", f"/applications/{obj_id}", token,
        json={"requiredResourceAccess": new_required})
    print("PATCH OK — Mail.Send added to requiredResourceAccess")

# ── Step 3: get the Graph service principal in this tenant ────────────────────
sp_resp = api("GET", f"/servicePrincipals?$filter=appId eq '{MSGRAPH_APP_ID}'", token)
graph_sp_id = sp_resp["value"][0]["id"]
print(f"Graph service principal: {graph_sp_id}")

# ── Step 4: get our app's service principal ───────────────────────────────────
our_sp_resp = api("GET", f"/servicePrincipals?$filter=appId eq '{config.GRAPH_CLIENT_ID}'", token)
our_sp_id = our_sp_resp["value"][0]["id"]
print(f"Our service principal:   {our_sp_id}")

# ── Step 5: check existing appRoleAssignments (admin consent grants) ──────────
grants = api("GET", f"/servicePrincipals/{our_sp_id}/appRoleAssignments", token)
granted_roles = {g["appRoleId"] for g in grants.get("value", [])}
print(f"Already consented roles: {granted_roles}")

if MAIL_SEND_ROLE in granted_roles:
    print("Admin consent for Mail.Send already granted.")
else:
    result = api("POST", f"/servicePrincipals/{our_sp_id}/appRoleAssignments", token, json={
        "principalId": our_sp_id,
        "resourceId":  graph_sp_id,
        "appRoleId":   MAIL_SEND_ROLE,
    })
    print(f"Admin consent granted — assignment id: {result.get('id')}")

print("\nDone. Mail.Send is now active — re-run send_test_email.py to verify.")
