"""
Claude-powered parser: extracts date/time availability from a candidate's reply.

Returns a list of {"date": "M/D/YYYY", "time": "H:MM AM"} dicts.
Times are normalized to the hour or half-hour.
"""

import json
import logging
from datetime import datetime

import anthropic

import config

logger = logging.getLogger(__name__)

_CLIENT = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

SYSTEM_PROMPT = """\
You extract date and time availability from casual text messages or emails.
Return ONLY a JSON array of objects with keys "date" (M/D/YYYY) and "time" (H:MM AM or H:MM PM).
Normalize times to the nearest hour or half-hour. No explanations, no markdown, just the array.

Rules:
- "morning" → 10:00 AM
- "afternoon" → 2:00 PM
- "evening" → 5:00 PM
- "anytime" or no specific time → omit the entry (return [])
- If vague day given (e.g. "Tuesday"), use the soonest upcoming Tuesday
- If no clear availability is stated, return []
"""


def parse_availability(reply_text: str, reference_date: datetime | None = None) -> list[dict]:
    """
    Parse a candidate's reply into specific date/time slots.

    Args:
        reply_text: The candidate's message.
        reference_date: "Today" for resolving relative dates (defaults to now).

    Returns:
        List of {"date": "6/15/2026", "time": "10:00 AM"} dicts.
    """
    if reference_date is None:
        reference_date = datetime.now()

    today_str = reference_date.strftime("%A, %B %d, %Y")

    user_prompt = (
        f"Today is {today_str}.\n\n"
        f"Candidate reply:\n\"{reply_text}\"\n\n"
        f"Extract all specific availability. Return JSON array only."
    )

    try:
        response = _CLIENT.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=512,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = response.content[0].text.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        slots = json.loads(raw)
        if not isinstance(slots, list):
            return []
        valid = []
        for s in slots:
            if isinstance(s, dict) and "date" in s and "time" in s:
                valid.append({"date": str(s["date"]).strip(), "time": str(s["time"]).strip()})
        logger.info("Parsed %d availability slot(s) from reply", len(valid))
        return valid
    except Exception:
        logger.exception("availability_parser failed on reply: %r", reply_text[:100])
        return []
