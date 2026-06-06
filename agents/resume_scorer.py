"""
Scores a resume against a job description using Claude.
Returns a score 1-10 plus a brief rationale.
"""

import json
import logging
from dataclasses import dataclass

import anthropic

import config

logger = logging.getLogger(__name__)

_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

SCORING_MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """\
You are an expert hiring evaluator. Given a job title, job description, and a candidate's resume text, \
score the candidate's fit on a scale of 1 to 10 where:
  1-3 = poor fit
  4-6 = moderate fit
  7-9 = strong fit
  10  = exceptional fit

Respond ONLY with a valid JSON object in this exact shape:
{
  "score": <integer 1-10>,
  "rationale": "<2-4 sentence summary of strengths and gaps>"
}
"""


@dataclass
class ScoreResult:
    score: int
    rationale: str
    model: str


def score_resume(
    resume_text: str,
    job_title: str,
    job_description: str = "",
) -> ScoreResult:
    user_content = (
        f"Job Title: {job_title}\n\n"
        f"Job Description:\n{job_description or '(none provided)'}\n\n"
        f"Resume:\n{resume_text or '(no resume text extracted)'}"
    )

    response = _client.messages.create(
        model=SCORING_MODEL,
        max_tokens=512,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )

    raw = response.content[0].text.strip()

    try:
        data = json.loads(raw)
        score = int(data["score"])
        rationale = str(data["rationale"])
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.error("Failed to parse Claude response: %s\nRaw: %s", exc, raw)
        score = 0
        rationale = f"Parsing error — raw response: {raw[:300]}"

    logger.info("Score for '%s': %d/10", job_title, score)
    return ScoreResult(score=score, rationale=rationale, model=response.model)
