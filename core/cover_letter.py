"""Generate and export tailored cover letters."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from docx import Document

from .matcher import _make_client
from .reader import Resume

_COVER_LETTER_SYSTEM = """You are an expert career coach and cover-letter writer.
You will receive:
- job description
- source resume facts
- style preferences
- optional user context fields
- optional existing draft to improve

Write a tailored cover letter body in a modern simplified format (no date/header/signature block).

STRICT RULES (must follow):
- Use only facts from the provided resume/source data.
- Do not hallucinate projects, roles, tools, dates, or achievements.
- Avoid generic filler language and cliches.
- Tailor directly to the job description keywords, with emphasis on soft skills.
- Keep claims specific and grounded.

Return ONLY valid JSON:
{
  "draft": "<cover letter body text only>",
  "soft_skills_used": ["skill1", "skill2"]
}"""

_COVER_LETTER_REVISE_SYSTEM = """You are an expert career coach revising a cover-letter draft.
You will receive:
- job description
- source resume facts
- style preferences
- optional user context fields
- current draft
- manual user feedback
- optional chat history

Revise the draft based on the user's feedback while keeping all claims grounded in provided facts.

STRICT RULES (must follow):
- Use only facts from the provided resume/source data.
- Do not hallucinate projects, roles, tools, dates, or achievements.
- Apply the user feedback directly and concretely.
- Keep the draft professional and concise for the selected length/tone.
- Return only the revised cover letter body text (no date/header/signature block).

Return ONLY valid JSON:
{
  "draft": "<revised cover letter body text only>",
  "soft_skills_used": ["skill1", "skill2"]
}"""


def _parse_json(text: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
    cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    cleaned = re.sub(r"[\x00-\x09\x0b\x0c\x0e-\x1f]", " ", cleaned)
    start = cleaned.index("{")
    obj, _ = json.JSONDecoder().raw_decode(cleaned[start:])
    return obj


def _call(
    client,
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


def _resume_text_from_resume(resume: Resume) -> str:
    lines = [f"CANDIDATE: {resume.name}", f"FILENAME: {resume.filename}", ""]
    if resume.sections:
        for sec in resume.sections:
            lines.append(f"## {sec.heading}")
            for bullet in sec.bullets:
                lines.append(f"- {bullet}")
            lines.append("")
    else:
        lines.append(resume.full_text)
    return "\n".join(lines).strip()


def _resume_text_from_optimized(optimized: dict) -> str:
    lines = [f"CANDIDATE: {optimized.get('name', 'Candidate')}", ""]
    for sec in optimized.get("sections", []):
        heading = sec.get("heading", "").strip()
        if heading:
            lines.append(f"## {heading}")
        content = (sec.get("content") or "").strip()
        if content:
            lines.append(content)
        for bullet in sec.get("bullets", []):
            lines.append(f"- {bullet}")
        lines.append("")
    return "\n".join(lines).strip()


def resume_payload_from_source(
    optimized: dict | None,
    fallback_resume: Resume | None,
) -> tuple[str, str, str]:
    """Return (source_label, candidate_name, resume_text_payload)."""
    if optimized:
        return (
            "optimized_resume_step_5",
            (optimized.get("name", "Candidate") or "Candidate").strip(),
            _resume_text_from_optimized(optimized),
        )
    if fallback_resume:
        return (
            "best_resume_step_3",
            (fallback_resume.name or "Candidate").strip(),
            _resume_text_from_resume(fallback_resume),
        )
    return ("", "", "")


def generate_cover_letter(
    *,
    job_description: str,
    resume_text: str,
    api_key: str,
    model: str,
    base_url: str = "",
    json_mode: bool = True,
    tone: str,
    length: str,
    hiring_manager_name: str = "",
    job_title: str = "",
    company_name: str = "",
    why_company: str = "",
    why_position: str = "",
    existing_draft: str = "",
    improve_mode: bool = False,
) -> dict:
    """Generate or improve a cover letter draft."""
    client = _make_client(api_key, base_url)
    mode_text = "IMPROVE EXISTING DRAFT" if improve_mode else "GENERATE NEW DRAFT"
    length_instruction = {
        "Short (150)": "Target about 150 words.",
        "Standard (250)": "Target about 250 words.",
        "Long (400-500)": "Target 400-500 words.",
    }.get(length, "Target about 250 words.")

    user_prompt = f"""
TASK: {mode_text}

TONE: {tone}
LENGTH: {length} ({length_instruction})

JOB DESCRIPTION:
{job_description}

SOURCE RESUME FACTS:
{resume_text}

USER CONTEXT:
- Hiring Manager Name: {hiring_manager_name or "(not provided)"}
- Job Title: {job_title or "(not provided)"}
- Company Name: {company_name or "(not provided)"}
- Why this company: {why_company or "(not provided)"}
- Why this position: {why_position or "(not provided)"}
""".strip()

    if improve_mode:
        user_prompt += f"\n\nCURRENT USER-EDITED DRAFT:\n{existing_draft}\n"
        user_prompt += (
            "\nRewrite this draft fully if needed, but preserve factual correctness "
            "and keep every claim grounded in provided source facts."
        )

    return _call(
        client=client,
        model=model,
        system=_COVER_LETTER_SYSTEM,
        user=user_prompt,
        temperature=0.4,
        json_mode=json_mode,
    )


def revise_cover_letter_with_feedback(
    *,
    job_description: str,
    resume_text: str,
    current_draft: str,
    feedback: str,
    api_key: str,
    model: str,
    base_url: str = "",
    json_mode: bool = True,
    tone: str,
    length: str,
    hiring_manager_name: str = "",
    job_title: str = "",
    company_name: str = "",
    why_company: str = "",
    why_position: str = "",
    chat_history: list[dict] | None = None,
) -> dict:
    """Revise a cover letter draft using manual user feedback."""
    client = _make_client(api_key, base_url)
    length_instruction = {
        "Short (150)": "Target about 150 words.",
        "Standard (250)": "Target about 250 words.",
        "Long (400-500)": "Target 400-500 words.",
    }.get(length, "Target about 250 words.")

    history_text = ""
    if chat_history:
        turns: list[str] = []
        for msg in chat_history[-8:]:
            role = (msg.get("role") or "").strip().lower()
            content = (msg.get("content") or "").strip()
            if not content:
                continue
            if role == "user":
                turns.append(f"USER: {content}")
            elif role == "assistant":
                turns.append(f"ASSISTANT: {content}")
        history_text = "\n".join(turns)

    user_prompt = f"""
TASK: REVISE EXISTING DRAFT BASED ON USER FEEDBACK

TONE: {tone}
LENGTH: {length} ({length_instruction})

JOB DESCRIPTION:
{job_description}

SOURCE RESUME FACTS:
{resume_text}

USER CONTEXT:
- Hiring Manager Name: {hiring_manager_name or "(not provided)"}
- Job Title: {job_title or "(not provided)"}
- Company Name: {company_name or "(not provided)"}
- Why this company: {why_company or "(not provided)"}
- Why this position: {why_position or "(not provided)"}

CURRENT DRAFT:
{current_draft}

LATEST USER FEEDBACK:
{feedback}
""".strip()

    if history_text:
        user_prompt += f"\n\nRECENT REVISION CHAT HISTORY:\n{history_text}\n"

    return _call(
        client=client,
        model=model,
        system=_COVER_LETTER_REVISE_SYSTEM,
        user=user_prompt,
        temperature=0.4,
        json_mode=json_mode,
    )


def _safe_filename(text: str) -> str:
    return (text or "").strip().replace(" ", "_").replace("/", "_")


def build_export_letter(
    *,
    draft_body: str,
    candidate_name: str,
    hiring_manager_name: str = "",
    job_title: str = "",
    company_name: str = "",
) -> str:
    """Build business-letter format from simplified draft body."""
    today = date.today().strftime("%B %d, %Y")
    recipient = hiring_manager_name.strip() or "Hiring Manager"
    role = job_title.strip()
    company = company_name.strip() or "Company"
    greeting = f"Dear {recipient},"
    closing = "Regards,\n" + (candidate_name.strip() or "Candidate")

    header_lines = [today, "", recipient]
    if role:
        header_lines.append(role)
    header_lines.append(company)
    header_lines.append("")
    header_lines.append(greeting)
    header_lines.append("")

    return "\n".join(header_lines) + draft_body.strip() + "\n\n" + closing


def export_cover_letter_docx(
    *,
    cover_letter_text: str,
    candidate_name: str,
    company_name: str,
    output_dir: str | Path,
) -> Path:
    """Export a cover letter text to a .docx file."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    parts = (candidate_name.strip() or "Candidate").split()
    first, last = (parts[0], parts[-1]) if len(parts) >= 2 else (parts[0] if parts else "Candidate", "")
    stem = f"{_safe_filename(first)}_{_safe_filename(last)}_CoverLetter_{_safe_filename(company_name or 'Company')}"

    doc = Document()
    for block in [p for p in cover_letter_text.split("\n\n") if p.strip()]:
        doc.add_paragraph(block.strip())

    path = output / f"{stem}.docx"
    doc.save(str(path))
    return path
