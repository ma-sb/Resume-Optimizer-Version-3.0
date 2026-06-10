"""Context-aware interview and resume copilot helpers."""

from __future__ import annotations

import re
from typing import Iterable

import requests

from .matcher import _make_client, _build_resume_text
from .reader import Resume


_COPILOT_SYSTEM = """You are an experienced recruiter, hiring manager, and career coach.

You are helping a candidate prepare their resume, recruiter screen, and interviews.
You already know the candidate's resume, the target job description, the target company context, and any prior analysis performed in the app.

When analyzing the user's profile, always:
- Think from the perspective of a recruiter evaluating a candidate.
- Translate experiences, projects, and responsibilities into business impact, business value, and problem-solving outcomes.
- Focus on outcomes, measurable results, stakeholder impact, customer impact, revenue impact, efficiency gains, risk reduction, or other business benefits.
- Reframe technical work in terms of the business problems it solved and the value it created.
- Connect the user's experience to the requirements of the target role whenever possible.
- Provide personalized responses based on the user's resume, the job description, and company context.
- Be specific and evidence-based rather than generic.
- Do not make up experience, education, projects, achievements, or skills.
- You may rephrase existing experience, but you must not invent new facts.

If important information is missing:
- Ask targeted follow-up questions before providing a final answer.
- Gather enough context to produce a highly personalized and accurate response.
- Never make assumptions when additional information would significantly improve the answer.

Response style:
- Be concise, direct, and practical.
- When useful, structure the answer so the candidate can reuse it in interviews or resume edits.
- Explicitly tie recommendations back to evidence from the resume, job description, or company context.
"""


def _normalize_company_name(company_name: str) -> str:
    return re.sub(r"\s+", " ", company_name).strip()


def fetch_company_context(company_name: str, timeout: int = 10) -> str:
    """Fetch a short public company summary from Wikipedia when available."""
    name = _normalize_company_name(company_name)
    if not name:
        return ""

    try:
        search = requests.get(
            "https://en.wikipedia.org/w/rest.php/v1/search/title",
            params={"q": name, "limit": 1},
            timeout=timeout,
            headers={"User-Agent": "ResumePolisher/1.0"},
        )
        search.raise_for_status()
        pages = search.json().get("pages", [])
        if not pages:
            return ""

        title = pages[0].get("title")
        if not title:
            return ""

        summary = requests.get(
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(title)}",
            timeout=timeout,
            headers={"User-Agent": "ResumePolisher/1.0"},
        )
        summary.raise_for_status()
        data = summary.json()

        parts = [f"Company: {name}"]
        extract = data.get("extract")
        description = data.get("description")
        content_urls = data.get("content_urls", {})
        desktop_url = content_urls.get("desktop", {}).get("page")

        if description:
            parts.append(f"Short description: {description}")
        if extract:
            parts.append(f"Public summary: {extract}")
        if desktop_url:
            parts.append(f"Source: {desktop_url}")

        return "\n".join(parts)
    except Exception:
        return ""


def build_analysis_context(
    match_results: dict | None = None,
    improvements: dict | None = None,
    optimized: dict | None = None,
) -> str:
    parts: list[str] = []

    if match_results:
        parts.append("MATCH RESULTS")
        best = match_results.get("best_resume")
        recommendation = match_results.get("recommendation")
        if best:
            parts.append(f"Best resume: {best}")
        if recommendation:
            parts.append(f"Recommendation: {recommendation}")
        for result in match_results.get("results", []):
            filename = result.get("filename", "Unknown")
            score = result.get("score", "?")
            explanation = result.get("explanation", "")
            parts.append(f"- {filename}: {score} | {explanation}")

    if improvements:
        parts.append("\nIMPROVEMENT ANALYSIS")
        keywords = improvements.get("keywords", {})
        for key in ["hard_skills", "soft_skills", "domain_terms", "action_verbs"]:
            items = keywords.get(key, [])
            if items:
                parts.append(f"{key}: {', '.join(items)}")
        if improvements.get("overall_tips"):
            parts.append(f"Overall tips: {improvements['overall_tips']}")
        for item in improvements.get("improvements", [])[:8]:
            parts.append(
                f"Rewrite suggestion | Section: {item.get('section', '')} | "
                f"Original: {item.get('original', '')} | Rewritten: {item.get('rewritten', '')}"
            )

    if optimized:
        parts.append("\nOPTIMIZED RESUME ANALYSIS")
        score = optimized.get("job_fit_score")
        summary = optimized.get("job_fit_summary")
        if score is not None:
            parts.append(f"Optimized job fit score: {score}")
        if summary:
            parts.append(f"Optimized fit summary: {summary}")

    return "\n".join(parts).strip()


def _format_conversation(history: Iterable[dict]) -> str:
    lines: list[str] = []
    for item in history:
        role = item.get("role", "assistant").upper()
        content = (item.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n\n".join(lines)


def answer_follow_up(
    question: str,
    job_description: str,
    resume: Resume | None,
    api_key: str,
    model: str,
    base_url: str = "",
    company_name: str = "",
    company_context: str = "",
    analysis_context: str = "",
    conversation_history: list[dict] | None = None,
) -> str:
    """Answer a follow-up question using persistent job, resume, company, and analysis context."""
    client = _make_client(api_key, base_url)
    resume_text = _build_resume_text(resume) if resume is not None else ""
    history_text = _format_conversation(conversation_history or [])

    context_blocks = [
        "TARGET JOB DESCRIPTION",
        job_description.strip() or "Missing",
        "\nCANDIDATE RESUME",
        resume_text or "Missing",
        "\nCOMPANY CONTEXT",
        company_context or (f"Company name: {company_name}" if company_name else "Missing"),
        "\nPREVIOUS APP ANALYSIS",
        analysis_context or "None yet.",
        "\nCONVERSATION SO FAR",
        history_text or "No prior conversation.",
        "\nCURRENT USER QUESTION",
        question.strip(),
    ]

    response = client.chat.completions.create(
        model=model,
        temperature=0.3,
        messages=[
            {"role": "system", "content": _COPILOT_SYSTEM},
            {"role": "user", "content": "\n".join(context_blocks)},
        ],
    )
    return (response.choices[0].message.content or "").strip()
