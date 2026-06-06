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

EMAIL_HOST = os.getenv("EMAIL_HOST", "imap.gmail.com")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "993"))
EMAIL_USERNAME = _require("EMAIL_USERNAME")
EMAIL_PASSWORD = _require("EMAIL_PASSWORD")
EMAIL_FOLDER = os.getenv("EMAIL_FOLDER", "INBOX")
EMAIL_TRIGGER_SUBJECT = os.getenv("EMAIL_TRIGGER_SUBJECT", "New Application")

SCORE_NOTIFY_THRESHOLD = int(os.getenv("SCORE_NOTIFY_THRESHOLD", "7"))
