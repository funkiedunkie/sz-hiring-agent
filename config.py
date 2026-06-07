import os
from dotenv import load_dotenv
load_dotenv()

def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(f"Missing required env var: {key}")
    return value

ANTHROPIC_API_KEY = _require("ANTHROPIC_API_KEY")
CAREERPLUG_EMAIL = _require("CAREERPLUG_EMAIL")
CAREERPLUG_PASSWORD = _require("CAREERPLUG_PASSWORD")
CAREERPLUG_COMPANY_SLUG = _require("CAREERPLUG_COMPANY_SLUG")
TWILIO_ACCOUNT_SID = _require("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = _require("TWILIO_AUTH_TOKEN")
TWILIO_FROM_NUMBER = _require("TWILIO_FROM_NUMBER")
TWILIO_TO_NUMBER = _require("TWILIO_TO_NUMBER")
SUPABASE_URL = _require("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = _require("SUPABASE_SERVICE_ROLE_KEY")
GRAPH_TENANT_ID = _require("GRAPH_TENANT_ID")
GRAPH_CLIENT_ID = _require("GRAPH_CLIENT_ID")
GRAPH_CLIENT_SECRET = _require("GRAPH_CLIENT_SECRET")
GRAPH_USER_EMAIL = _require("GRAPH_USER_EMAIL")
EMAIL_TRIGGER_SUBJECT = os.getenv("EMAIL_TRIGGER_SUBJECT", "New Application")
CALENDLY_LINK = _require("CALENDLY_LINK")
SCORE_NOTIFY_THRESHOLD = int(os.getenv("SCORE_NOTIFY_THRESHOLD", "2"))  # 1–4 star scale