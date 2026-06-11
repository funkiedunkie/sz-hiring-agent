"""
Scores a candidate against the Stretch Zone hiring rubric using Claude.

Rubric
──────
Auto-disqualify:
  • High school students
  • Job hoppers — consistent 6–7 month stints across multiple employers
  • Unrelated work history with under 2 years total tenure

⭐⭐⭐⭐ (4)  2+ recent relevant jobs  OR  pursuing / completed exercise
             science / kinesiology degree
⭐⭐⭐  (3)  2+ recent relevant jobs, no degree
⭐⭐   (2)  Unrelated but consistent tenure (1 + year / role);
             healthcare-adjacent roles; single long-tenure employer
⭐    (1)  No relevance, no notable tenure

Borderline tiebreaker: resume substance — sparse text → do not pursue;
school achievements, sports, GPA → pursue.

Returns ScoreResult(score=1–4, reasoning=str, auto_disqualified=bool).
"""

import json
import logging
from dataclasses import dataclass

import anthropic

import config

logger = logging.getLogger(__name__)

SCORING_MODEL = "claude-sonnet-4-20250514"

_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

SYSTEM_PROMPT = """\
You are a hiring evaluator for Stretch Zone, a guided-stretching studio in Meridian, ID.
Your job is to score a job applicant using the rubric below and return ONLY a JSON object.

─── RUBRIC ────────────────────────────────────────────────────────────────────

AUTO-DISQUALIFY (set auto_disqualified=true, score=0) if ANY of:
  • Currently a high school student
  • Job hopper: 3+ employers with consistently short stints (6–7 months each)
  • Completely unrelated work history AND total work experience under 2 years

STAR SCORES (auto_disqualified=false):
  4 — Two or more RECENT jobs in fitness / wellness / athletics / personal training /
      physical therapy / sports coaching  OR  applicant is pursuing or has completed
      an exercise science, kinesiology, or related degree
  3 — Two or more recent relevant jobs (as above), but no degree in the field
  2 — Unrelated field BUT consistent tenure (≥1 year per role); OR healthcare-adjacent
      role (nursing, CNA, massage therapy, etc.); OR single employer with long tenure (≥2 yr)
  1 — No relevant experience, no notable tenure

BORDERLINE TIEBREAKER (between 1 and 2, or 2 and 3):
  Sparse resume with little detail → round down
  School achievements, sports participation, high GPA, or strong extracurriculars → round up

─── OUTPUT FORMAT ─────────────────────────────────────────────────────────────

Respond with ONLY this JSON — no prose, no markdown:
{
  "score": <integer 0–4>,
  "auto_disqualified": <true|false>,
  "reasoning": "<2–4 sentences citing specific resume evidence>"
}
"""


@dataclass
class ScoreResult:
    score: int                 # 1–4 stars; 0 = auto-disqualified
    auto_disqualified: bool
    reasoning: str
    model: str


def score_candidate(application_text: str, candidate_name: str = "") -> ScoreResult:
    """
    Score *application_text* against the Stretch Zone rubric.
    *candidate_name* is included for readable logging only.
    """
    user_content = (
        f"Candidate: {candidate_name or 'Unknown'}\n\n"
        f"Application / Resume Text:\n{application_text or '(no text provided)'}"
    )

    response = _client.messages.create(
        model=SCORING_MODEL,
        max_tokens=512,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )

    raw = response.content[0].text.strip()
    # Strip optional ```json ... ``` fences Claude sometimes adds
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        data = json.loads(raw)
        score = int(data["score"])
        auto_disqualified = bool(data["auto_disqualified"])
        reasoning = str(data["reasoning"])
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.error("Failed to parse Claude response: %s\nRaw: %s", exc, raw)
        score = 0
        auto_disqualified = True
        reasoning = f"Parsing error — raw response: {raw[:300]}"

    stars = "*" * score if score else "AUTO-DQ"
    logger.info(
        "Score for '%s': %s (%d/4) | auto_dq=%s",
        candidate_name or "unknown",
        stars,
        score,
        auto_disqualified,
    )
    return ScoreResult(
        score=score,
        auto_disqualified=auto_disqualified,
        reasoning=reasoning,
        model=response.model,
    )
