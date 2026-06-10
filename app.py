"""Resume Polisher – Streamlit application."""

import base64
import streamlit as st
from pathlib import Path

from JobFetch import fetch_jobs
from core.copilot import answer_follow_up, build_analysis_context, fetch_company_context
from core.reader import load_resumes, read_docx_from_bytes, Resume
from core.matcher import PROVIDERS, match_resumes, get_improvements, optimize_resume
from core.exporter import export_docx, _convert_to_pdf

RESUMES_DIR = Path(__file__).parent / "resumes"
OUTPUT_DIR = Path(__file__).parent / "output"
QR_CODE_PATH = Path(__file__).parent / "assets" / "qr-code.png"
APP_NAME = "Resume Polisher 3.0"

STEP_LABELS = [
    "Job Description",
    "Your Resumes",
    "Match & Score",
    "Improvements",
    "Optimize & Review",
    "Export",
]

STEP_TOOLTIPS = {
    1: "Paste the full job posting so the AI knows what to optimize for.",
    2: "Upload one or more .docx resume versions — the AI will compare them.",
    3: "The AI scores each resume against the job and picks the best match.",
    4: "Get bullet-by-bullet rewrite suggestions with keywords from the job.",
    5: "Generate a fully optimized resume and preview it before exporting.",
    6: "Enter the company name and download the final .docx / .pdf files.",
}

SAMPLE_JD = """Data Scientist, New York, NY - BCG X

What You'll Do

Our BCG X teams own the full analytics value-chain end to end: framing new business challenges, designing innovative algorithms, implementing, and deploying scalable solutions, and enabling colleagues and clients to fully embrace AI. Our product offerings span from fully custom-builds to industry specific leading edge AI software solutions. 

As a Data Scientist and Senior Data Scientist, you'll be part of our rapidly growing team. You'll have the chance to apply data science methods and analytics to real-world business situations across a variety of industries to drive significant business impact. You'll have the chance to partner with clients in a variety of BCG regions and industries, and on key topics like climate change, enabling them to design, build, and deploy new and innovative solutions. 

Additional responsibilities will include developing and delivering thought leadership in scientific communities and papers as well as leading conferences on behalf of BCG X. Successful candidates are intellectually curious builders who are biased toward action, scrappy, and communicative. 


We are looking for talented individuals with a passion for data science, statistics, operations research and transforming organizations into AI led innovative companies. Successful candidates possess the following: 

    Comfortable in a client-facing role with the ambition to lead teams 

    Likes to distill complex results or processes into simple, clear visualizations 

    Explain sophisticated data science concepts in an understandable manner 

    Love building things and are comfortable working with modern development tools and writing code collaboratively (bonus points if you have a software development or DevOps experience) 

    Significant experience applying advanced analytics to a variety of business situations and a proven ability to synthesize complex data 

    Deep understanding of modern machine learning techniques and their mathematical underpinnings, and can translate this into business implications for our clients 

    Have strong project management skills 
    Master's degree or PhD in relevant field of study - please provide all academic certificates showing the final grades (A-level, Bachelor, Master) """


def _get_saved_key() -> str:
    try:
        return st.secrets.get("api_key", "")
    except Exception:
        return ""


def _get_saved_adzuna_id() -> str:
    try:
        return st.secrets.get("adzuna_app_id", "")
    except Exception:
        return ""


def _get_saved_adzuna_key() -> str:
    try:
        return st.secrets.get("adzuna_app_key", "")
    except Exception:
        return ""


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _estimate_cost(n_resumes: int, job_tokens: int, resume_tokens: int, model: str) -> str:
    total_input = job_tokens + resume_tokens * n_resumes + 500
    total_output = 800 * n_resumes
    if "gpt-4o-mini" in model or "flash" in model or "haiku" in model:
        cost = total_input * 0.15 / 1_000_000 + total_output * 0.6 / 1_000_000
    elif "gpt-4o" in model or "pro" in model or "sonnet" in model:
        cost = total_input * 2.5 / 1_000_000 + total_output * 10.0 / 1_000_000
    else:
        cost = total_input * 5.0 / 1_000_000 + total_output * 15.0 / 1_000_000
    if cost < 0.01:
        return f"~{total_input + total_output:,} tokens · < $0.01"
    return f"~{total_input + total_output:,} tokens · ~${cost:.2f}"


def _current_step() -> int:
    """Determine the furthest completed step."""
    if st.session_state.get("export_approved"):
        return 6
    if "optimized" in st.session_state:
        return 5
    if "improvements" in st.session_state:
        return 4
    if "match_results" in st.session_state:
        return 3
    return 1


def _image_data_uri(path: Path) -> str:
    if not path.exists():
        return ""
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    suffix = path.suffix.lower().lstrip(".") or "png"
    mime = "image/png" if suffix == "png" else f"image/{suffix}"
    return f"data:{mime};base64,{encoded}"


def _resolve_copilot_resume(selected_resumes: list[Resume], selected_resume: Resume | None) -> Resume | None:
    if selected_resume is not None:
        return selected_resume

    optimized_source = st.session_state.get("optimized_source_resume")
    if optimized_source is not None:
        return optimized_source

    match_results = st.session_state.get("match_results", {})
    best_name = match_results.get("best_resume")
    if best_name:
        for resume in selected_resumes:
            if resume.filename == best_name:
                return resume

    return selected_resumes[0] if selected_resumes else None


def _default_company_name() -> str:
    for key in ["copilot_company_name", "company_name_export"]:
        value = st.session_state.get(key, "")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _store_company_context(company_name: str) -> str:
    normalized = company_name.strip()
    if not normalized:
        st.session_state["copilot_company_context"] = ""
        st.session_state["copilot_company_context_name"] = ""
        return ""

    if (
        st.session_state.get("copilot_company_context")
        and st.session_state.get("copilot_company_context_name") == normalized
    ):
        return st.session_state["copilot_company_context"]

    context = fetch_company_context(normalized)
    st.session_state["copilot_company_context"] = context
    st.session_state["copilot_company_context_name"] = normalized
    return context


# ── Page config ──────────────────────────────────────────────────────────────

st.set_page_config(page_title=APP_NAME, page_icon="📄", layout="wide")

st.markdown(
    """
    <style>
    /* ── Global ──────────────────────────────────────────── */
    .block-container {padding-top: 1.5rem; max-width: 960px;}

    /* ── Header banner ───────────────────────────────────── */
    .app-header {
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
        color: white;
        padding: 2rem 2.5rem;
        border-radius: 12px;
        margin-bottom: 0.5rem;
    }
    .app-header h1 {
        margin: 0; font-size: 2rem; font-weight: 700; letter-spacing: -0.5px;
    }
    .app-header p {
        margin: 0.4rem 0 0 0; opacity: 0.8; font-size: 0.95rem;
    }

    /* ── Progress stepper ────────────────────────────────── */
    .stepper {
        display: flex; justify-content: space-between; align-items: center;
        padding: 1rem 0.5rem; margin-bottom: 1rem;
    }
    .step {
        display: flex; flex-direction: column; align-items: center;
        flex: 1; position: relative;
    }
    .step-circle {
        width: 32px; height: 32px; border-radius: 50%;
        display: flex; align-items: center; justify-content: center;
        font-size: 0.8rem; font-weight: 700;
        border: 2px solid #cbd5e1; color: #94a3b8; background: white;
        transition: all 0.3s ease; z-index: 1;
    }
    .step-circle.active {
        border-color: #0f3460; color: white; background: #0f3460;
    }
    .step-circle.done {
        border-color: #059669; color: white; background: #059669;
    }
    .step-label {
        font-size: 0.7rem; margin-top: 4px; color: #94a3b8;
        text-align: center; white-space: nowrap;
    }
    .step-label.active, .step-label.done { color: #1a1a2e; font-weight: 600; }

    /* connector lines */
    .step:not(:last-child)::after {
        content: ''; position: absolute;
        top: 16px; left: calc(50% + 20px); right: calc(-50% + 20px);
        height: 2px; background: #e2e8f0; z-index: 0;
    }
    .step.done:not(:last-child)::after { background: #059669; }

    /* ── Section step labels ─────────────────────────────── */
    div[data-testid="stSubheader"] > div > p,
    .stSubheader {
        border-left: 4px solid #0f3460;
        padding-left: 0.75rem;
    }

    /* ── Metrics / score cards ───────────────────────────── */
    div[data-testid="stMetric"] {
        background: linear-gradient(135deg, #f0f4ff 0%, #f8f9fb 100%);
        border-radius: 10px; padding: 14px 18px; border: 1px solid #e2e8f0;
    }
    div[data-testid="stMetricValue"] > div { font-weight: 700; color: #1a1a2e; }

    /* ── Expanders ───────────────────────────────────────── */
    details[data-testid="stExpander"] {
        border: 1px solid #e2e8f0 !important;
        border-radius: 8px !important; margin-bottom: 0.5rem;
        transition: box-shadow 0.2s ease;
    }
    details[data-testid="stExpander"]:hover {
        box-shadow: 0 2px 8px rgba(15, 52, 96, 0.08);
    }
    details[data-testid="stExpander"] summary { font-weight: 500; }

    /* ── Buttons ─────────────────────────────────────────── */
    .stButton > button[kind="primary"] {
        border-radius: 8px; font-weight: 600; letter-spacing: 0.3px;
    }

    /* ── Sidebar ─────────────────────────────────────────── */
    section[data-testid="stSidebar"] { background: #fafbfc; color: #1a1a2e; }
    section[data-testid="stSidebar"] p,
    section[data-testid="stSidebar"] span,
    section[data-testid="stSidebar"] li { color: #1a1a2e; }
    section[data-testid="stSidebar"] .stSelectbox label,
    section[data-testid="stSidebar"] .stTextInput label {
        font-size: 0.85rem; font-weight: 600; color: #374151;
    }

    /* ── File uploader ───────────────────────────────────── */
    div[data-testid="stFileUploader"] section {
        border: 2px dashed #cbd5e1 !important; border-radius: 10px !important;
        transition: border-color 0.2s ease;
    }
    div[data-testid="stFileUploader"] section:hover { border-color: #0f3460 !important; }

    /* ── Alerts ──────────────────────────────────────────── */
    div[data-testid="stAlert"] { border-radius: 8px; }

    /* ── Score badge ─────────────────────────────────────── */
    .score-badge {
        display: inline-block; padding: 4px 14px; border-radius: 20px;
        font-weight: 700; font-size: 1.1rem; color: white;
        min-width: 60px; text-align: center;
    }
    .score-high   { background: linear-gradient(135deg, #059669, #10b981); }
    .score-medium { background: linear-gradient(135deg, #d97706, #f59e0b); }
    .score-low    { background: linear-gradient(135deg, #dc2626, #ef4444); }

    /* ── Token estimate badge ────────────────────────────── */
    .token-est {
        display: inline-block; font-size: 0.78rem; color: #64748b;
        background: #f1f5f9; padding: 3px 10px; border-radius: 6px;
        margin-bottom: 0.5rem;
    }

    /* ── Filename preview ────────────────────────────────── */
    .filename-preview {
        font-family: monospace; font-size: 0.85rem; color: #475569;
        background: #f8fafc; border: 1px solid #e2e8f0;
        border-radius: 6px; padding: 6px 12px; margin: 0.3rem 0 0.8rem 0;
    }

    /* ── Two-panel layout ───────────────────────────────── */
    .copilot-panel {
        border: 1px solid #dbe4f0;
        border-radius: 12px;
        padding: 1rem;
        background: linear-gradient(180deg, #fcfdff 0%, #f6f8fc 100%);
    }
    .copilot-panel h3 {
        margin: 0;
        font-size: 1.05rem;
        color: #16213e;
    }
    .copilot-panel p {
        color: #475569;
        font-size: 0.9rem;
        margin: 0.35rem 0 0 0;
    }
    .copilot-meta {
        font-size: 0.78rem;
        color: #64748b;
        background: #eef2ff;
        border-radius: 999px;
        display: inline-block;
        padding: 0.2rem 0.6rem;
        margin: 0.4rem 0.4rem 0 0;
    }
    .chat-hint {
        border-left: 4px solid #0f3460;
        padding: 0.75rem 0.9rem;
        background: rgba(15, 52, 96, 0.04);
        border-radius: 8px;
        color: #334155;
        font-size: 0.88rem;
    }

    /* ── Support card ───────────────────────────────────── */
    .support-card {
        position: fixed;
        right: 18px;
        bottom: 18px;
        width: 200px;
        background: rgba(255, 255, 255, 0.96);
        border: 1px solid rgba(15, 52, 96, 0.12);
        border-radius: 14px;
        box-shadow: 0 16px 40px rgba(15, 23, 42, 0.16);
        padding: 0.85rem;
        z-index: 999;
        backdrop-filter: blur(10px);
    }
    .support-card p {
        margin: 0 0 0.65rem 0;
        font-size: 0.86rem;
        line-height: 1.35;
        color: #1f2937;
        text-align: center;
    }
    .support-card img {
        width: 100%;
        display: block;
        border-radius: 10px;
        border: 1px solid #e2e8f0;
    }
    @media (max-width: 900px) {
        .support-card {
            width: 148px;
            right: 10px;
            bottom: 10px;
            padding: 0.6rem;
        }
        .support-card p {
            font-size: 0.75rem;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── Header ───────────────────────────────────────────────────────────────────

st.markdown(
    '<div class="app-header">'
    f"<h1>{APP_NAME}</h1>"
    "<p>AI-powered resume evaluation, matching, optimization, and interview copilot</p>"
    "</div>",
    unsafe_allow_html=True,
)

# ── Progress stepper ─────────────────────────────────────────────────────────

current = _current_step()
stepper_html = '<div class="stepper">'
for i, label in enumerate(STEP_LABELS, 1):
    if i < current:
        cls = "done"
        circle = "✓"
    elif i == current:
        cls = "active"
        circle = str(i)
    else:
        cls = ""
        circle = str(i)
    stepper_html += (
        f'<div class="step {cls}">'
        f'<div class="step-circle {cls}">{circle}</div>'
        f'<div class="step-label {cls}">{label}</div>'
        f'</div>'
    )
stepper_html += '</div>'
st.markdown(stepper_html, unsafe_allow_html=True)

# ── Sidebar – settings ───────────────────────────────────────────────────────

with st.sidebar:
    st.header("Settings")

    provider_name = st.selectbox("Provider", list(PROVIDERS.keys()), key="provider")
    provider = PROVIDERS[provider_name]

    saved_key = _get_saved_key()
    api_key = st.text_input(
        "API Key",
        value=saved_key,
        type="password",
        help=provider["key_help"],
        key="api_key_input",
    )
    active_model = st.selectbox("Model", provider["models"], index=0, key="model")

    # if not saved_key:
    #     st.divider()
    #     st.markdown("**Save your API keys**")
    #     st.markdown(
    #         "Create a file at\n"
    #         "`Your_Project_Folder/.streamlit/secrets.toml`\n"
    #         "with this content:"
    #     )
    #     st.code(
    #         'api_key = "your-llm-key"\n'
    #         'adzuna_app_id = "your-adzuna-id"\n'
    #         'adzuna_app_key = "your-adzuna-key"',
    #         language="toml",
    #     )
    #     st.caption("Keys stay on your machine and are never uploaded.")

    st.divider()
    st.markdown("**Job Search**")
    adzuna_app_id = st.text_input(
        "Adzuna App ID",
        value=_get_saved_adzuna_id(),
        type="password",
        help="Get a free key at https://developer.adzuna.com",
        key="adzuna_app_id",
    )
    adzuna_app_key = st.text_input(
        "Adzuna API Key",
        value=_get_saved_adzuna_key(),
        type="password",
        key="adzuna_app_key",
    )

    st.divider()
    st.markdown("**How to use**")
    st.markdown(
        "0. (Optional) Specify a job position and search for companies\n"
        "1. Upload your `.docx` resumes\n"
        "2. Paste the job description\n"
        "3. Click **Match Best Resume**\n"
        "4. Get improvement suggestions\n"
        "5. Generate optimized resume & review\n"
        "6. Export"
    )

base_url = provider["base_url"]
json_mode = provider["json_mode"]

main_col, chat_col = st.columns([1.75, 1], gap="large")

with main_col:
    # ── Step 1 — Job Description ─────────────────────────────────────────────

    st.subheader("1 — Job Description", help=STEP_TOOLTIPS[1])

    with st.expander("Search for a job", expanded=False):
        col_pos, col_loc, col_num, col_sort = st.columns([2, 2, 1, 1.5])
        with col_pos:
            search_position = st.text_input("Job position", placeholder='e.g. "Data Scientist"', key="search_position")
        with col_loc:
            search_location = st.text_input("Location", value="united states", key="search_location")
        with col_num:
            search_num = st.number_input("Results", min_value=1, max_value=20, value=5, key="search_num")
        with col_sort:
            search_sort = st.selectbox("Sort by", ["relevance", "date", "salary"], key="search_sort")

        search_btn = st.button(
            "Search",
            disabled=not (adzuna_app_id and adzuna_app_key and search_position),
            key="search_btn",
        )
        if not (adzuna_app_id and adzuna_app_key):
            st.caption("Enter your App ID and API Key in the sidebar to enable job search.")

        if search_btn:
            with st.spinner(f'Searching for "{search_position}"…'):
                try:
                    results = fetch_jobs(
                        search_position,
                        num_results=search_num,
                        location=search_location,
                        sort_by=search_sort,
                        app_id=adzuna_app_id,
                        app_key=adzuna_app_key,
                    )
                    st.session_state["job_search_results"] = results
                except Exception as e:
                    st.error(f"Search failed: {e}")

        if "job_search_results" in st.session_state:
            results = st.session_state["job_search_results"]
            if results:
                for job in results:
                    c1, c2 = st.columns([5, 1])
                    with c1:
                        salary_text = job["salary"].replace("$", "\\$")
                        st.markdown(f"**{job['title']}** — {job['company']}")
                        st.caption(f"{job['location']} · {salary_text}")
                    with c2:
                        st.link_button("Open", job['url'])
            else:
                st.info("No results found.")

    def _fill_sample():
        st.session_state["job_desc_input"] = SAMPLE_JD

    col_jd, col_sample = st.columns([4, 1])
    with col_sample:
        st.button("Try sample", on_click=_fill_sample, use_container_width=True)

    job_desc = st.text_area(
        "Paste the job description below",
        height=220,
        placeholder="Copy-paste the full job posting here…",
        key="job_desc_input",
    )

    # ── Step 2 — Your Resumes ────────────────────────────────────────────────

    st.subheader("2 — Your Resumes", help=STEP_TOOLTIPS[2])

    uploaded_files = st.file_uploader(
        "Upload .docx resumes",
        type=["docx"],
        accept_multiple_files=True,
        help="Drag and drop one or more .docx resume files.",
    )

    all_resumes: list[Resume] = []

    if uploaded_files:
        for uf in uploaded_files:
            try:
                resume = read_docx_from_bytes(uf.getvalue(), uf.name)
                all_resumes.append(resume)
            except Exception as e:
                st.warning(f"Could not parse {uf.name}: {e}")

    folder_resumes = load_resumes(RESUMES_DIR)
    for r in folder_resumes:
        if r.filename not in [ar.filename for ar in all_resumes]:
            all_resumes.append(r)

    if all_resumes:
        st.success(f"**{len(all_resumes)}** resume(s) loaded")
        selected_filenames = st.multiselect(
            "Select resumes to use",
            options=[r.filename for r in all_resumes],
            default=[r.filename for r in all_resumes],
            help="Only selected resumes will be sent to the API.",
        )
        selected_resumes: list[Resume] = [r for r in all_resumes if r.filename in selected_filenames]
        with st.expander("Preview loaded resumes"):
            for r in selected_resumes:
                st.markdown(f"**{r.filename}** — *{r.name}*")
                st.text(r.full_text[:500] + ("…" if len(r.full_text) > 500 else ""))
                st.divider()
    else:
        st.info("Upload your `.docx` resumes above to get started.")
        selected_resumes = []

    # ── Step 3 — Match & Score ───────────────────────────────────────────────

    st.subheader("3 — Match Best Resume & Fit Score", help=STEP_TOOLTIPS[3])

    if not api_key:
        st.info("Enter your API key in the sidebar to enable AI features.")

    if job_desc and selected_resumes:
        jt = _estimate_tokens(job_desc)
        rt = sum(_estimate_tokens(r.full_text) for r in selected_resumes)
        est = _estimate_cost(len(selected_resumes), jt, rt, active_model)
        st.markdown(f'<div class="token-est">Estimated: {est}</div>', unsafe_allow_html=True)

    match_btn = st.button(
        f"Match Best Resume Version & Provide Fit Score ({len(selected_resumes)} resume{'s' if len(selected_resumes) != 1 else ''})",
        disabled=not (api_key and job_desc and selected_resumes),
        use_container_width=True,
        type="primary",
    )

    if match_btn:
        with st.spinner("Analyzing resumes against the job description…"):
            try:
                results = match_resumes(job_desc, selected_resumes, api_key, active_model, base_url, json_mode)
                st.session_state["match_results"] = results
            except Exception as e:
                st.error(f"Error during matching: {e}")

    if "match_results" in st.session_state:
        results = st.session_state["match_results"]
        best = results.get("best_resume", "")

        st.markdown(f"**Best resume:** `{best}`")
        st.markdown(f"*{results.get('recommendation', '')}*")

        for entry in results.get("results", []):
            score = entry.get("score", 0)
            css_class = "score-high" if score >= 75 else "score-medium" if score >= 50 else "score-low"
            with st.container():
                c1, c2 = st.columns([1, 3])
                c1.markdown(
                    f'<span class="score-badge {css_class}">{score}%</span>',
                    unsafe_allow_html=True,
                )
                c1.caption(entry.get("filename", "?"))
                c2.write(entry.get("explanation", ""))

    # ── Step 4 — Improvement Recommendations ─────────────────────────────────

    st.subheader("4 — Improvement Recommendations", help=STEP_TOOLTIPS[4])

    resume_names = [r.filename for r in selected_resumes]
    selected_resume_name = st.selectbox(
        "Choose a resume to improve",
        resume_names if resume_names else ["(no resumes loaded)"],
    )

    selected_resume: Resume | None = next(
        (r for r in selected_resumes if r.filename == selected_resume_name), None
    )

    if job_desc and selected_resume:
        jt = _estimate_tokens(job_desc)
        rt = _estimate_tokens(selected_resume.full_text)
        est = _estimate_cost(1, jt, rt, active_model)
        st.markdown(f'<div class="token-est">Estimated: {est}</div>', unsafe_allow_html=True)

    improve_btn = st.button(
        "Get Improvement Suggestions",
        disabled=not (api_key and job_desc and selected_resume),
        use_container_width=True,
    )

    if improve_btn and selected_resume:
        with st.spinner("Generating improvement suggestions…"):
            try:
                improvements = get_improvements(job_desc, selected_resume, api_key, active_model, base_url, json_mode)
                st.session_state["improvements"] = improvements
            except Exception as e:
                st.error(f"Error: {e}")

    if "improvements" in st.session_state:
        imp = st.session_state["improvements"]

        kw = imp.get("keywords", {})
        if kw:
            with st.expander("Keywords extracted from job description", expanded=True):
                cols = st.columns(4)
                for col, (label, key) in zip(cols, [
                    ("Hard Skills", "hard_skills"),
                    ("Soft Skills", "soft_skills"),
                    ("Domain Terms", "domain_terms"),
                    ("Action Verbs", "action_verbs"),
                ]):
                    items = kw.get(key, [])
                    col.markdown(f"**{label}**")
                    col.markdown(", ".join(f"`{k}`" for k in items) if items else "*none*")

        if imp.get("overall_tips"):
            st.info(f"**Tips:** {imp['overall_tips']}")

        n_items = len(imp.get("improvements", []))

        def _toggle_all():
            val = st.session_state["select_all_rewrites"]
            for i in range(n_items):
                st.session_state[f"rewrite_{i}"] = val

        sel_all_col1, sel_all_col2 = st.columns([4, 1])
        with sel_all_col2:
            st.checkbox("Select all", value=True, key="select_all_rewrites", on_change=_toggle_all)

        approved_rewrites: list[dict] = []
        for idx, item in enumerate(imp.get("improvements", [])):
            section = item.get("section", "Unknown section")
            original = item.get("original", "(not provided)")
            rewritten = item.get("rewritten") or item.get("suggested") or item.get("improved") or "(not provided)"
            reason = item.get("reason", "")

            col_left, col_right = st.columns([4, 1])
            with col_left:
                with st.expander(f"📝 {section} — rewrite suggestion"):
                    st.markdown("**Original:**")
                    st.markdown(f"> {original}")
                    st.markdown("**Suggested rewrite:**")
                    st.markdown(f'<blockquote style="color: #111; border-left: 4px solid #0f3460;">{rewritten}</blockquote>', unsafe_allow_html=True)
                    if reason:
                        st.caption(reason)
            with col_right:
                checked = st.checkbox("Accept suggestion", value=True, key=f"rewrite_{idx}")

            if checked:
                approved_rewrites.append({
                    "section": section,
                    "original": original,
                    "rewritten": rewritten,
                })

        st.session_state["approved_rewrites"] = approved_rewrites
        count = len(approved_rewrites)
        total = len(imp.get("improvements", []))
        st.caption(f"{count} of {total} rewrites selected for optimization.")

        if imp.get("bullets_to_remove"):
            st.markdown("---")
            st.markdown("**Bullets to consider removing** (least relevant):")
            for rm in imp["bullets_to_remove"]:
                st.markdown(f"- ~~{rm.get('bullet', '?')}~~ ({rm.get('section', '?')}) — {rm.get('reason', '')}")

    # ── Step 5 — Optimize & Review ───────────────────────────────────────────

    st.subheader("5 — Optimize & Review", help=STEP_TOOLTIPS[5])

    if job_desc and selected_resume:
        jt = _estimate_tokens(job_desc)
        rt = _estimate_tokens(selected_resume.full_text)
        est = _estimate_cost(1, jt, rt, active_model)
        st.markdown(f'<div class="token-est">Estimated: {est}</div>', unsafe_allow_html=True)

    optimize_btn = st.button(
        "Generate Optimized Resume",
        disabled=not (api_key and job_desc and selected_resume),
        use_container_width=True,
        type="primary",
    )

    if optimize_btn and selected_resume:
        with st.spinner("Optimizing resume…"):
            try:
                approved = st.session_state.get("approved_rewrites", [])
                optimized = optimize_resume(job_desc, selected_resume, api_key, active_model, base_url, json_mode, approved)
                st.session_state["optimized"] = optimized
                st.session_state["optimized_source_resume"] = selected_resume
                st.session_state["export_approved"] = False
            except Exception as e:
                st.error(f"Error: {e}")

    if "optimized" in st.session_state:
        opt = st.session_state["optimized"]

        fit_score = opt.get("job_fit_score", 0)
        fit_summary = opt.get("job_fit_summary", "")
        if fit_summary or fit_score:
            score_css = "score-high" if fit_score >= 75 else "score-medium" if fit_score >= 50 else "score-low"
            col_score, col_summary = st.columns([1, 4])
            with col_score:
                st.markdown(
                    f'<span class="score-badge {score_css}">{fit_score}%</span>',
                    unsafe_allow_html=True,
                )
            with col_summary:
                st.success(f"**Job fit:** {fit_summary}")

        with st.expander("Preview optimized resume", expanded=True):
            st.markdown(f"### {opt.get('name', '')}")
            for sec in opt.get("sections", []):
                st.markdown(f"**{sec.get('heading', '')}**")
                if sec.get("content"):
                    st.write(sec["content"])
                for b in sec.get("bullets", []):
                    st.markdown(f"- {b}")

    # ── Step 6 — Export ──────────────────────────────────────────────────────

    st.subheader("6 — Export", help=STEP_TOOLTIPS[6])

    if "optimized" in st.session_state:
        opt_export = st.session_state["optimized"]
        source_resume: Resume | None = st.session_state.get("optimized_source_resume")
        has_original = source_resume and source_resume.raw_bytes

        company_name = st.text_input(
            "Company name (used in the exported filename)",
            value="",
            placeholder="e.g. Google, McKinsey, Tesla…",
            key="company_name_export",
        )

        if company_name.strip():
            cand_name = opt_export.get("name", "Candidate").strip()
            parts = cand_name.split()
            first, last = (parts[0], parts[-1]) if len(parts) >= 2 else (cand_name, "")
            safe = lambda s: s.replace(" ", "_").replace("/", "_")
            preview_stem = f"{safe(first)}_{safe(last)}_Resume_{safe(company_name.strip())}"
            st.markdown(
                f'<div class="filename-preview">📁 {preview_stem}.docx &nbsp;/&nbsp; {preview_stem}.pdf</div>',
                unsafe_allow_html=True,
            )

        if not has_original:
            st.warning("Original .docx bytes not available — export will use a basic format.")

        col_exp_word, col_exp_pdf = st.columns(2)

        with col_exp_word:
            word_btn = st.button(
                "Export to Word",
                disabled=not (has_original and company_name.strip()),
                use_container_width=True,
                type="primary",
            )

        with col_exp_pdf:
            pdf_btn = st.button(
                "Export to PDF",
                disabled=not (has_original and company_name.strip()),
                use_container_width=True,
                type="primary",
            )

        if word_btn and has_original:
            with st.spinner("Generating Word file…"):
                try:
                    docx_path = export_docx(source_resume.raw_bytes, opt_export, OUTPUT_DIR, company_name.strip())
                    st.session_state["export_docx_path"] = docx_path
                    st.session_state["export_word_done"] = True
                except Exception as e:
                    st.error(f"Error: {e}")

        if pdf_btn and has_original:
            with st.spinner("Generating PDF…"):
                try:
                    docx_path = export_docx(source_resume.raw_bytes, opt_export, OUTPUT_DIR, company_name.strip())
                    pdf_path = _convert_to_pdf(docx_path)
                    st.session_state["export_docx_path"] = docx_path
                    st.session_state["export_pdf_path"] = pdf_path
                    st.session_state["export_pdf_done"] = True
                except Exception as e:
                    st.error(f"Error: {e}")

        if st.session_state.get("export_word_done"):
            docx_path: Path | None = st.session_state.get("export_docx_path")
            if docx_path and docx_path.exists():
                st.balloons()
                st.success(f"Ready: `{docx_path.name}`")
                with open(docx_path, "rb") as f:
                    st.download_button(
                        label="Download .docx",
                        data=f.read(),
                        file_name=docx_path.name,
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        use_container_width=True,
                    )

        if st.session_state.get("export_pdf_done"):
            pdf_path: Path | None = st.session_state.get("export_pdf_path")
            if pdf_path and pdf_path.exists():
                st.balloons()
                st.success(f"Ready: `{pdf_path.name}`")
                with open(pdf_path, "rb") as f:
                    st.download_button(
                        label="Download .pdf",
                        data=f.read(),
                        file_name=pdf_path.name,
                        mime="application/pdf",
                        use_container_width=True,
                    )
            else:
                st.warning("PDF conversion requires LibreOffice. Try exporting to Word instead.")
    else:
        st.info("Generate an optimized resume in step 5 first.")

copilot_resume = _resolve_copilot_resume(selected_resumes, selected_resume)
analysis_context = build_analysis_context(
    st.session_state.get("match_results"),
    st.session_state.get("improvements"),
    st.session_state.get("optimized"),
)

with chat_col:
    st.markdown(
        """
        <div class="copilot-panel">
            <h3>Resume & Interview Copilot</h3>
            <p>Ask follow-up questions without re-pasting your resume or the job description.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    company_name_for_chat = st.text_input(
        "Target company",
        value=_default_company_name(),
        placeholder="e.g. Google, Stripe, BCG X",
        key="copilot_company_name",
        help="Used for company-specific interview and resume advice.",
    )

    company_context = st.session_state.get("copilot_company_context", "")
    if company_name_for_chat.strip():
        if st.button("Refresh company context", use_container_width=True):
            company_context = _store_company_context(company_name_for_chat)
        elif st.session_state.get("copilot_company_context_name") != company_name_for_chat.strip():
            st.caption("Refresh company context to update public company information.")

    st.markdown(
        f'<span class="copilot-meta">Job description: {"ready" if bool(job_desc.strip()) else "missing"}</span>'
        f'<span class="copilot-meta">Resume: {"ready" if copilot_resume is not None else "missing"}</span>'
        f'<span class="copilot-meta">Analysis: {"ready" if bool(analysis_context) else "pending"}</span>',
        unsafe_allow_html=True,
    )

    if company_context:
        with st.expander("Public company context", expanded=False):
            st.write(company_context)

    if "copilot_messages" not in st.session_state:
        st.session_state["copilot_messages"] = [
            {
                "role": "assistant",
                "content": (
                    "Ask about fit, interview preparation, recruiter concerns, resume edits, "
                    "or how to explain a project. I will reuse the current resume, job description, "
                    "company context, and prior analysis."
                ),
            }
        ]

    for message in st.session_state["copilot_messages"]:
        with st.chat_message(message["role"]):
            st.write(message["content"])

    sample_prompts = [
        "Why am I a strong fit for this role?",
        "What accomplishments should I emphasize?",
        "What recruiter concerns might come up?",
        "How should I explain my most relevant project?",
    ]

    prompt_cols = st.columns(2)
    for idx, prompt in enumerate(sample_prompts):
        if prompt_cols[idx % 2].button(prompt, key=f"copilot_prompt_{idx}", use_container_width=True):
            st.session_state["copilot_draft"] = prompt

    with st.form("copilot_form", clear_on_submit=True):
        question_input = st.text_area(
            "Ask a follow-up question",
            key="copilot_draft",
            height=110,
            placeholder="Why do you want to work at this company?",
        )
        ask_btn = st.form_submit_button(
            "Ask Copilot",
            use_container_width=True,
            disabled=not api_key,
        )

    if not api_key:
        st.caption("Enter an API key in the sidebar to enable the copilot.")
    elif not job_desc.strip() or copilot_resume is None:
        st.markdown(
            '<div class="chat-hint">Load a resume and paste the job description first so the copilot can answer with full context.</div>',
            unsafe_allow_html=True,
        )
    elif ask_btn:
        question = question_input.strip()
        if not question:
            st.warning("Enter a question for the copilot.")
        else:
            st.session_state["copilot_messages"].append({"role": "user", "content": question})
            with st.spinner("Thinking through your background, the role, and the company…"):
                try:
                    if company_name_for_chat.strip() and st.session_state.get("copilot_company_context_name") != company_name_for_chat.strip():
                        company_context = _store_company_context(company_name_for_chat)
                    answer = answer_follow_up(
                        question=question,
                        job_description=job_desc,
                        resume=copilot_resume,
                        api_key=api_key,
                        model=active_model,
                        base_url=base_url,
                        company_name=company_name_for_chat.strip(),
                        company_context=company_context,
                        analysis_context=analysis_context,
                        conversation_history=st.session_state["copilot_messages"][:-1],
                    )
                except Exception as e:
                    answer = f"Copilot error: {e}"
            st.session_state["copilot_messages"].append({"role": "assistant", "content": answer})
            st.rerun()

qr_code_data_uri = _image_data_uri(QR_CODE_PATH)
if qr_code_data_uri:
    st.markdown(
        f"""
        <div class="support-card">
            <p>If this was helpful, you can buy me a coffee :)</p>
            <img src="{qr_code_data_uri}" alt="Buy me a coffee QR code" />
        </div>
        """,
        unsafe_allow_html=True,
    )
