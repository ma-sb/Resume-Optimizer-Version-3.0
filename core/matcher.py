"""AI-powered resume matching, scoring, and improvement recommendations."""

import json
import re
from openai import OpenAI

from .reader import Resume

PROVIDERS = {
    "OpenAI": {
        "base_url": "",
        "models": ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo"],
        "default": "gpt-4o-mini",
        "key_help": "Get a key at https://platform.openai.com/api-keys",
        "json_mode": True,
    },
    "Google Gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "models": ["gemini-2.0-flash", "gemini-2.0-pro", "gemini-1.5-pro", "gemini-1.5-flash"],
        "default": "gemini-2.0-flash",
        "key_help": "Get a key at https://aistudio.google.com/apikey",
        "json_mode": True,
    },
    "Anthropic (Claude)": {
        "base_url": "https://api.anthropic.com/v1/",
        "models": ["claude-sonnet-4-20250514", "claude-3-5-haiku-20241022"],
        "default": "claude-sonnet-4-20250514",
        "key_help": "Get a key at https://console.anthropic.com/settings/keys",
        "json_mode": False,
    },
}

_MATCH_SYSTEM = """You are an expert career consultant and resume reviewer.
You will receive a job description and one or more resumes.
For each resume, provide:
1. A match score from 0-100 representing how well the resume fits the job.
2. A short explanation (2-3 sentences) of why this score was given.

Return ONLY valid JSON in this exact schema (no markdown fences):
{
  "results": [
    {
      "filename": "<filename>",
      "score": <int 0-100>,
      "explanation": "<string>"
    }
  ],
  "best_resume": "<filename of the highest scoring resume>",
  "recommendation": "<1-2 sentence recommendation>"
}"""

_IMPROVE_SYSTEM = """You are an expert resume writer and career coach.
You will receive a job description and a single resume.

STEP 1 — KEYWORD EXTRACTION
First, extract the most important keywords and phrases from the job description.
Group them into:
  - hard_skills: technical tools, software, languages, frameworks, methodologies
  - soft_skills: leadership, communication, teamwork, etc.
  - domain_terms: industry-specific terminology, certifications, concepts
  - action_verbs: strong verbs the job posting uses or implies (e.g. "spearheaded", "optimized")

STEP 2 — BULLET-POINT ANALYSIS
For each bullet point in the resume, decide:
  - Is it already well-aligned with the job? -> skip it.
  - Can it be reworded to incorporate extracted keywords? -> rewrite it.

REWRITE RULES (follow strictly):
  - Do NOT add information the candidate did not already describe.
  - Do NOT make the bullet longer. Keep it the same length or shorter.
  - Only change wording and phrasing so that the keywords and terminology from
    the job description appear naturally in the existing experience.
  - Preserve any metrics, numbers, and specific achievements exactly.
  - Each rewrite must sound natural, not keyword-stuffed.

STEP 3 — BULLET REMOVAL (only if needed)
If and only if a new bullet point truly needs to be added to cover a critical
gap, you MUST remove the single least relevant existing bullet from the same
section so the total bullet count stays identical. Otherwise leave bullets_to_remove
as an empty list.

You MUST use exactly these key names in your JSON. Do not rename them.

Return ONLY valid JSON (no markdown fences):
{
  "keywords": {
    "hard_skills": ["keyword"],
    "soft_skills": ["keyword"],
    "domain_terms": ["keyword"],
    "action_verbs": ["verb"]
  },
  "improvements": [
    {
      "section": "section heading",
      "original": "original bullet text",
      "rewritten": "improved bullet text",
      "reason": "which keywords were woven in and why"
    }
  ],
  "bullets_to_remove": [
    {
      "section": "section heading",
      "bullet": "bullet text to remove",
      "reason": "why this is the least relevant"
    }
  ],
  "overall_tips": "1-3 general tips for this resume vs. this job"
}"""

_OPTIMIZE_SYSTEM = """You are an expert resume optimizer.
You receive a job description and a resume (with sections and bullets).
Produce an optimized version of the ENTIRE resume tailored to the job.

PROCESS:
1. Extract the critical keywords and phrases from the job description
   (technical skills, tools, methodologies, soft skills, domain terms).
2. Go through every bullet point and paragraph in the resume.
   Reword each one so that relevant keywords from the job description are
   incorporated naturally into the candidate's existing experience.
3. Assign a job fit score from 0-100 and write a single-sentence job fit summary that describes how well the
   optimized resume now fits the target position.
4. Output the full optimized resume.

STRICT RULES:
- NEVER fabricate experience, projects, or skills the candidate does not already have.
- NEVER add new bullet points. Only rewrite existing ones.
  If a bullet truly must be added, remove the least relevant bullet in the same
  section so the count per section stays identical.
- NEVER make bullets longer than the original. Same length or shorter.
- Preserve all metrics, numbers, dates, company names, and job titles exactly.
- Keep the same section structure and section order.
- Keep the EXACT same number of bullets per section as the original.
- Wording changes only: adjust phrasing, terminology, and action verbs to
  mirror the job description while keeping the meaning truthful.
- Lines that are company names, job titles, dates, or university names
  should be returned AS-IS without modification.
- Do NOT include tab characters or other control characters in your output.

You MUST use exactly these key names in your JSON. Do not rename them.

Return ONLY valid JSON (no markdown fences):
{
  "name": "candidate name",
  "job_fit_score": 0,
  "job_fit_summary": "one sentence summarizing how well this optimized resume fits the job",
  "sections": [
    {
      "heading": "section heading",
      "content": "",
      "bullets": ["bullet 1", "bullet 2"]
    }
  ]
}"""


def _build_resume_text(resume: Resume) -> str:
    parts = [f"FILENAME: {resume.filename}", f"CANDIDATE: {resume.name}", ""]
    for sec in resume.sections:
        parts.append(f"## {sec.heading}")
        for b in sec.bullets:
            parts.append(f"  - {b}")
        parts.append("")
    if not resume.sections:
        parts.append(resume.full_text)
    return "\n".join(parts)


def _make_client(api_key: str, base_url: str = "") -> OpenAI:
    kwargs: dict = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


def _parse_json(text: str) -> dict:
    """Parse JSON from a response, stripping markdown fences and control chars."""
    cleaned = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
    cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    cleaned = re.sub(r"[\x00-\x09\x0b\x0c\x0e-\x1f]", " ", cleaned)
    return json.loads(cleaned)


def _call(
    client: OpenAI,
    model: str,
    system: str,
    user: str,
    temperature: float,
    json_mode: bool,
) -> dict:
    kwargs: dict = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = client.chat.completions.create(**kwargs)
    return _parse_json(resp.choices[0].message.content)


def match_resumes(
    job_description: str,
    resumes: list[Resume],
    api_key: str,
    model: str = "gpt-4o-mini",
    base_url: str = "",
    json_mode: bool = True,
) -> dict:
    """Score and rank resumes against a job description."""
    client = _make_client(api_key, base_url)
    resume_texts = "\n---\n".join(_build_resume_text(r) for r in resumes)
    return _call(
        client, model, _MATCH_SYSTEM,
        f"JOB DESCRIPTION:\n{job_description}\n\nRESUMES:\n{resume_texts}",
        temperature=0.2, json_mode=json_mode,
    )


def get_improvements(
    job_description: str,
    resume: Resume,
    api_key: str,
    model: str = "gpt-4o-mini",
    base_url: str = "",
    json_mode: bool = True,
) -> dict:
    """Get bullet-point improvement suggestions for a single resume."""
    client = _make_client(api_key, base_url)
    return _call(
        client, model, _IMPROVE_SYSTEM,
        f"JOB DESCRIPTION:\n{job_description}\n\nRESUME:\n{_build_resume_text(resume)}",
        temperature=0.3, json_mode=json_mode,
    )


def optimize_resume(
    job_description: str,
    resume: Resume,
    api_key: str,
    model: str = "gpt-4o-mini",
    base_url: str = "",
    json_mode: bool = True,
    approved_rewrites: list[dict] | None = None,
) -> dict:
    """Produce a fully optimized resume tailored to the job."""
    client = _make_client(api_key, base_url)
    user_parts = [
        f"JOB DESCRIPTION:\n{job_description}",
        f"RESUME:\n{_build_resume_text(resume)}",
    ]
    if approved_rewrites:
        lines = ["APPROVED REWRITES (apply these exact changes; for bullets not listed, optimize as you see fit):"]
        for rw in approved_rewrites:
            lines.append(f"  Section: {rw['section']}")
            lines.append(f"  Original: {rw['original']}")
            lines.append(f"  Rewrite to: {rw['rewritten']}")
            lines.append("")
        user_parts.append("\n".join(lines))
    return _call(
        client, model, _OPTIMIZE_SYSTEM,
        "\n\n".join(user_parts),
        temperature=0.3, json_mode=json_mode,
    )
