"""Push GitHub Actions secrets for this repo, reading values from .env.

This script deliberately contains NO secret values. It used to hardcode live
credentials (Anthropic key, CareerPlug password, Twilio auth token, Graph client
secret, Supabase service role key), which meant rotating anything required
editing source, and one `git add .` would have published all of them.

Values come from .env (gitignored). To rotate a credential: change it in .env,
run this script, and — if the dashboard uses it — update Streamlit Cloud too.

Usage:
    python set_github_secrets.py <github_token>          # push all
    python set_github_secrets.py <github_token> NAME...  # push only these

Create a token at https://github.com/settings/tokens/new with `repo` scope
(or just `secrets` on a fine-grained token).
"""
import base64
import os
import sys

import requests
from dotenv import dotenv_values
from nacl import public

REPO = "funkiedunkie/sz-hiring-agent"

# Names only — values are read from .env. Keep in sync with config.py and the
# `env:` blocks in .github/workflows/hiring-agent.yml.
SECRET_NAMES = [
    "ANTHROPIC_API_KEY",
    "CAREERPLUG_EMAIL",
    "CAREERPLUG_PASSWORD",
    "CAREERPLUG_COMPANY_SLUG",
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_FROM_NUMBER",
    "TWILIO_TO_NUMBER",
    "TWILIO_MESSAGING_SERVICE_SID",
    "SMS_ENABLED",
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "GRAPH_TENANT_ID",
    "GRAPH_CLIENT_ID",
    "GRAPH_CLIENT_SECRET",
    "GRAPH_USER_EMAIL",
    "EMAIL_TRIGGER_SUBJECT",
    "CALENDLY_LINK",
    "CALENDLY_LINK_1HR",
    "CLUBREADY_USERNAME",
    "CLUBREADY_PASSWORD",
    "CLUBREADY_FALLBACK_EMAIL",
    "MANAGER_PHONE",
    "SCORE_NOTIFY_THRESHOLD",
]


def encrypt_secret(public_key_b64: str, secret_value: str) -> str:
    box = public.SealedBox(public.PublicKey(base64.b64decode(public_key_b64)))
    return base64.b64encode(box.encrypt(secret_value.encode("utf-8"))).decode("utf-8")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    token = sys.argv[1]
    only = set(sys.argv[2:])

    env = {**dotenv_values(".env"), **os.environ}

    names = [n for n in SECRET_NAMES if n in only] if only else list(SECRET_NAMES)
    if only:
        unknown = only - set(SECRET_NAMES)
        if unknown:
            print(f"Not in SECRET_NAMES: {', '.join(sorted(unknown))}")
            sys.exit(1)

    missing = [n for n in names if not env.get(n)]
    if missing and only:
        # Explicitly requested but absent — abort rather than report a rotation
        # that didn't actually happen.
        print("Requested but missing from .env:")
        for n in missing:
            print(f"  - {n}")
        sys.exit(1)

    # Bulk mode: some secrets legitimately live only in GitHub (e.g. CI-only
    # ClubReady creds). Skip them loudly instead of aborting the whole push.
    names = [n for n in names if env.get(n)]
    for n in missing:
        print(f"  SKIP {n} (not in .env — unchanged in GitHub)")

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    r = requests.get(
        f"https://api.github.com/repos/{REPO}/actions/secrets/public-key", headers=headers
    )
    r.raise_for_status()
    key_data = r.json()

    failures = []
    for name in names:
        payload = {
            "encrypted_value": encrypt_secret(key_data["key"], env[name]),
            "key_id": key_data["key_id"],
        }
        r = requests.put(
            f"https://api.github.com/repos/{REPO}/actions/secrets/{name}",
            headers=headers,
            json=payload,
        )
        if r.status_code in (201, 204):
            print(f"  OK   {name}")
        else:
            print(f"  FAIL {name}: {r.status_code} {r.text}")
            failures.append(name)

    if failures:
        print(f"\n{len(failures)} failed: {', '.join(failures)}")
        sys.exit(1)
    print(f"\nDone — {len(names)} secret(s) pushed.")


if __name__ == "__main__":
    main()
