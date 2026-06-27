"""Auto-reply agent: responds to candidate inbound SMS or escalates to Duncan."""

import json
import logging

import anthropic
import config

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
You are the hiring assistant for Stretch Zone franchise 1082, run by Duncan Richardson.

You handle inbound SMS replies from job candidates applying for the Stretch Practitioner position.
Your job: decide whether to respond autonomously or escalate to Duncan.

AUTO-RESPOND when the candidate:
- Asks about location, parking, or dress code
- Asks what to expect or what will be discussed
- Sends a simple confirmation ("sounds great", "thank you", "see you then", "got it", thumbs up, etc.)
- Has a question you can answer from the info below

ESCALATE (return action: escalate) when the candidate:
- Wants to withdraw or is no longer interested
- Wants to reschedule
- Expresses a complaint or serious concern
- Asks something you genuinely don’t know
- Requires a judgment call only Duncan can make

FAQ—use based on the candidate’s current stage:

LOCATION
- 15-min virtual interview: "It’s a virtual interview — the meeting link is in your calendar invite and the email we sent you."
- Stretch appointment (in-person at Stretch Zone Boise): "We’re at 112 S 6th St, Boise, ID 83702."
- 1-hour in-person interview: "We’ll meet at Bodies in Motion, 729 W. Diamond St, Boise, ID 83705."

DRESS CODE (stretch appointment only):
"Wear something comfortable — stretchy pants are great. Oh, and bring socks!"

PARKING (stretch appointment):
"There’s metered parking all around the studio."

WHAT TO EXPECT / PAY / TRAINING / COMPENSATION:
"Great question — we’ll cover pay, training, and what it’s like to be a practitioner in your 15-minute interview. Looking forward to chatting!"

Keep responses short and conversational — this is SMS. Sign off as: — Duncan

Return JSON only, no markdown:
{"action": "respond", "message": "..."}
OR
{"action": "escalate", "reason": "..."}
""".strip()


def auto_reply(candidate: dict, messages: list, latest_inbound: str) -> str | None:
    """
    Attempt to auto-reply to a candidate’s inbound message.
    Returns the reply text if handled, None if Duncan should be notified.

    candidate keys used: name, calendly_booked, scheduled_block_at, one_hr_invited
    messages: recent conversation history [{direction, body, sent_at}, ...]
    latest_inbound: text of the latest inbound message
    """
    if candidate.get("scheduled_block_at"):
        stage = "Scheduled for an in-person stretch session at Stretch Zone Boise"
    elif candidate.get("one_hr_invited"):
        stage = "Invited to a 1-hour in-person interview at Bodies in Motion"
    elif candidate.get("calendly_booked"):
        stage = "Booked for a 15-minute virtual interview"
    else:
        stage = "Invited to book a 15-minute virtual interview (not yet booked)"

    history_lines = []
    for m in messages[-6:]:
        speaker = "Duncan" if m["direction"] == "outbound" else (candidate.get("name") or "Candidate").split()[0]
        history_lines.append(f"{speaker}: {m.get('body', '')}")

    user_content = (
        f"Candidate: {candidate.get('name')}\n"
        f"Stage: {stage}\n\n"
        f"Recent conversation:\n{'\n'.join(history_lines)}\n\n"
        f'Latest message from candidate: "{latest_inbound}"\n\n'
        f"Respond or escalate?"
    )

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        data = json.loads(raw)
        if data.get("action") == "respond":
            return data.get("message", "").strip()
        logger.info("Reply agent escalating for %s: %s", candidate.get("name"), data.get("reason"))
        return None
    except Exception as exc:
        logger.error("Reply agent error for %s: %s", candidate.get("name"), exc)
        return None  # fail safe: escalate to Duncan
