# Resume Optimizer 3.0

An AI-powered job-search assistant that helps candidates evaluate resume fit, improve weak content, generate a tailored resume, and prepare for interviews.

> **Status:** Current flagship release. This builds on the earlier [Resume Polisher prototype](https://github.com/ma-sb/resume-polisher).

## The problem

Tailoring a resume for each role is repetitive and fragmented. Candidates move among job boards, resume files, keyword tools, writing assistants, and interview resources without a clear view of which resume best fits a role.

## The solution

Resume Optimizer 3.0 brings the workflow into one product. A user can find or add a job, compare resumes, identify gaps, improve weak sections, export a tailored PDF, and prepare for interviews.

## Core workflow

1. Find a role through live search or paste a job description.
2. Compare available resumes against the role.
3. Surface missing skills, weak bullets, and relevant keywords.
4. Generate targeted recommendations and revisions.
5. Export a clean, role-specific PDF.
6. Reuse the job context for interview preparation.

## Key capabilities

- Live job search through Adzuna
- Resume parsing, comparison, and AI-assisted match scoring
- Support for OpenAI, Gemini, and Claude
- Tailored PDF export
- Streamlit interface and command-line workflow
- Credentials entered at runtime rather than stored

## Product decisions and tradeoffs

### One connected workflow

Combining discovery, evaluation, optimization, and export means users do not have to rebuild context across several tools.

**Tradeoff:** A broader workflow creates more complexity than a single-purpose editor, so every step needs a clear next action.

### Multiple AI providers

Supporting several providers gives users choice and avoids model lock-in.

**Tradeoff:** Outputs vary by provider, increasing configuration and evaluation work.

### Streamlit for rapid iteration

Streamlit enabled fast testing of the complete workflow in Python.

**Tradeoff:** A production version would benefit from accounts, persistent projects, background processing, and greater control over interaction design.

## What changed from Version 2

- Added live job search and interview preparation
- Broadened provider support
- Improved the application and CLI structure
- Expanded the concept into an end-to-end application workflow

## What I learned

- A strong AI feature still needs a clear user workflow.
- Recommendations are more useful when tied to a specific job.
- Provider choice requires clear evaluation criteria.
- Export quality matters because the final document is what users submit.

## Current limitations

- Users provide their own API credentials.
- Results depend on the selected model.
- Resume files are local rather than attached to persistent accounts.
- AI-generated changes require human review.

## Next steps

- [ ] Add screenshots and a short demo
- [ ] Create a repeatable match-quality evaluation set
- [ ] Explain the reasoning behind recommendations
- [ ] Improve onboarding
- [ ] Add persistent projects and application tracking

## Quick start

    pip install -r requirements.txt
    streamlit run app.py

## Built with

Python · Streamlit · Typer · OpenAI · Gemini · Claude · Adzuna

---

Built by [Milica Andric](https://github.com/ma-sb) · [LinkedIn](https://www.linkedin.com/in/milica-andric-02863114a/)
