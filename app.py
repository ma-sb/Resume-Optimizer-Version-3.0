"""Resume Polisher – Streamlit application."""

import re
import streamlit as st
import streamlit.components.v1 as components
from pathlib import Path

from core.copilot import answer_follow_up, build_analysis_context, fetch_company_context
from core.cover_letter import (
    resume_payload_from_source,
    generate_cover_letter,
    build_export_letter,
    export_cover_letter_docx,
)
from core.reader import load_resumes, read_resume_from_bytes, Resume
from core.matcher import PROVIDERS, match_resumes, get_improvements, optimize_resume
from core.pdf_export import export_pdf_origin_resume
from core.exporter import export_docx, _convert_to_pdf

RESUMES_DIR = Path(__file__).parent / "resumes"
OUTPUT_DIR = Path(__file__).parent / "output"
BMC_LOGO_PATH = Path(__file__).parent / "assets" / "bmc-logo-yellow.png"
APP_NAME = "Resume Polisher 3.0"

STEP_LABELS = [
    "Job Description",
    "Your Resumes",
    "Match & Score",
    "Improvements",
    "Optimize & Review",
    "Export",
    "Cover Letter",
]

STEP_TOOLTIPS = {
    1: "Paste the full job posting so the AI knows what to optimize for.",
    2: "Upload one or more resume versions (.docx, .pdf, .doc) — the AI will compare them.",
    3: "The AI scores each resume against the job and picks the best match.",
    4: "Get bullet-by-bullet rewrite suggestions with keywords from the job.",
    5: "Generate a fully optimized resume and preview it before exporting.",
    6: "Enter the company name and download the final .docx / .pdf files.",
    7: "Optionally generate, edit, improve, and export a tailored cover letter.",
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
    if st.session_state.get("cover_letter_generated"):
        return 7
    if st.session_state.get("export_approved"):
        return 6
    if "optimized" in st.session_state:
        return 5
    if "improvements" in st.session_state:
        return 4
    if "match_results" in st.session_state:
        return 3
    return 1


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


def _infer_company_name(job_description: str, optimized_name: str = "") -> str:
    export_name = st.session_state.get("company_name_export", "")
    if isinstance(export_name, str) and export_name.strip():
        return export_name.strip()

    first_line = next((line.strip() for line in job_description.splitlines() if line.strip()), "")
    if " - " in first_line:
        tail = first_line.split(" - ", 1)[1].strip()
        if tail:
            return tail.split(",")[0].strip()

    match = re.search(r"\bat\s+([A-Z][A-Za-z0-9&.,' -]{1,80})", job_description)
    if match:
        return match.group(1).strip().rstrip(".")

    if optimized_name.strip():
        return optimized_name.strip()
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


def _toggle_copilot() -> None:
    st.session_state["copilot_open"] = not st.session_state.get("copilot_open", False)
    if not st.session_state["copilot_open"]:
        st.session_state["copilot_full_width"] = False


def _toggle_copilot_full_width() -> None:
    st.session_state["copilot_full_width"] = not st.session_state.get("copilot_full_width", False)


def _image_data_uri(path: Path) -> str:
    if not path.exists():
        return ""
    import base64

    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    suffix = path.suffix.lower().lstrip(".") or "png"
    mime = "image/png" if suffix == "png" else f"image/{suffix}"
    return f"data:{mime};base64,{encoded}"


def _style_copilot_launcher_button() -> None:
    components.html(
        """
        <script>
        (function() {
          const parentDoc = window.parent.document;
          const styleButton = () => {
            const buttons = Array.from(parentDoc.querySelectorAll('button'));
            const launcher = buttons.find((btn) => btn.innerText.trim() === 'Chat with copilot');
            if (!launcher) return;
            launcher.style.minHeight = '88px';
            launcher.style.whiteSpace = 'pre-wrap';
            launcher.style.textAlign = 'left';
            launcher.style.justifyContent = 'flex-start';
            launcher.style.alignItems = 'flex-start';
            launcher.style.lineHeight = '1.35';
            launcher.style.padding = '1rem 1.1rem';
            launcher.style.borderRadius = '12px';
            launcher.style.border = '1px solid #1a1a2e';
            launcher.style.background = 'linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%)';
            launcher.style.color = '#ffffff';
            launcher.style.fontWeight = '700';
            launcher.style.fontSize = '1.15rem';
            launcher.querySelectorAll('*').forEach((node) => {
              node.style.color = '#ffffff';
              node.style.fontWeight = '700';
              node.style.fontSize = '1.15rem';
              node.style.lineHeight = '1.35';
            });
          };
          styleButton();
          new MutationObserver(styleButton).observe(parentDoc.body, { childList: true, subtree: true });
        })();
        </script>
        """,
        height=0,
        width=0,
    )


def _pin_copilot_panel() -> None:
    components.html(
        """
        <script>
        (function() {
          const parentDoc = window.parent.document;
          const storageKey = 'resume-polisher-copilot-width';
          const getStoredWidth = () => {
            const raw = parentDoc.defaultView.localStorage.getItem(storageKey);
            const parsed = raw ? parseInt(raw, 10) : NaN;
            return Number.isFinite(parsed) ? parsed : null;
          };
          const setMainPadding = (isOpen, width) => {
            const block = parentDoc.querySelector('section[data-testid="stMain"] .block-container');
            if (!block) return;
            if (isOpen) {
              block.style.setProperty('padding-right', `calc(1.5rem + 1cm + ${width + 32}px)`, 'important');
            } else {
              block.style.removeProperty('padding-right');
            }
          };
          const ensureResizeHandle = (column, minWidth, maxWidth, applyPinnedLayout) => {
            let handle = column.querySelector('.copilot-resize-handle');
            if (!handle) {
              handle = parentDoc.createElement('div');
              handle.className = 'copilot-resize-handle';
              column.prepend(handle);
            }
            if (handle.dataset.bound === 'true') return;
            handle.dataset.bound = 'true';
            const startDrag = (event) => {
              event.preventDefault();
              event.stopPropagation();
              const startX = event.clientX;
              const startWidth = column.getBoundingClientRect().width;
              parentDoc.body.style.userSelect = 'none';
              parentDoc.body.style.cursor = 'ew-resize';
              const onMove = (moveEvent) => {
                const delta = startX - moveEvent.clientX;
                const nextWidth = Math.max(minWidth, Math.min(maxWidth, startWidth + delta));
                parentDoc.defaultView.localStorage.setItem(storageKey, String(Math.round(nextWidth)));
                applyPinnedLayout();
              };
              const onUp = () => {
                parentDoc.defaultView.removeEventListener('pointermove', onMove);
                parentDoc.defaultView.removeEventListener('pointerup', onUp);
                parentDoc.defaultView.removeEventListener('mousemove', onMove);
                parentDoc.defaultView.removeEventListener('mouseup', onUp);
                parentDoc.body.style.userSelect = '';
                parentDoc.body.style.cursor = '';
              };
              parentDoc.defaultView.addEventListener('pointermove', onMove);
              parentDoc.defaultView.addEventListener('pointerup', onUp, { once: true });
              parentDoc.defaultView.addEventListener('mousemove', onMove);
              parentDoc.defaultView.addEventListener('mouseup', onUp, { once: true });
            };
            handle.addEventListener('pointerdown', startDrag);
            handle.addEventListener('mousedown', startDrag);
          };
          const applyPinnedLayout = () => {
            const anchor = parentDoc.querySelector('.copilot-sticky-anchor');
            if (!anchor) return;

            const column = anchor.closest('div[data-testid="stColumn"]');
            if (!column) return;

            const rect = column.getBoundingClientRect();
            const viewportWidth = parentDoc.defaultView.innerWidth || window.innerWidth;
            const rightMargin = viewportWidth <= 900 ? 10 : 18;
            const isOpen = !!parentDoc.querySelector('.copilot-panel');
            const topOffset = isOpen
              ? (viewportWidth <= 900 ? 12 : 16)
              : (viewportWidth <= 900 ? 88 : 60);
            const minWidth = isOpen ? 400 : 110;
            const maxWidth = isOpen ? Math.min(760, Math.floor(viewportWidth * 0.58)) : 110;
            const storedWidth = getStoredWidth();
            const availableWidth = isOpen
              ? Math.max(minWidth, Math.min(maxWidth, storedWidth || rect.width || minWidth))
              : 110;

            column.style.position = 'fixed';
            column.style.top = topOffset + 'px';
            column.style.left = 'auto';
            column.style.right = rightMargin + 'px';
            column.style.width = availableWidth + 'px';
            column.style.maxWidth = availableWidth + 'px';
            column.style.maxHeight = `calc(100vh - ${topOffset + 12}px)`;
            column.style.overflowY = 'auto';
            column.style.alignSelf = 'flex-start';
            column.style.zIndex = '60';
            setMainPadding(isOpen, availableWidth);
            if (isOpen) {
              ensureResizeHandle(column, minWidth, maxWidth, applyPinnedLayout);
            } else {
              const handle = column.querySelector('.copilot-resize-handle');
              if (handle) handle.remove();
            }
          };

          applyPinnedLayout();
          parentDoc.defaultView.addEventListener('resize', applyPinnedLayout);
          new MutationObserver(applyPinnedLayout).observe(parentDoc.body, { childList: true, subtree: true });
        })();
        </script>
        """,
        height=0,
        width=0,
    )


def _sync_main_margin_with_sidebar() -> None:
    components.html(
        """
        <script>
        (function() {
          const parentDoc = window.parent.document;
          const applyMainMargin = () => {
            const sidebar = parentDoc.querySelector('section[data-testid="stSidebar"]');
            const block = parentDoc.querySelector('section[data-testid="stMain"] .block-container');
            if (!block) return;

            const sidebarWidth = sidebar ? parseFloat(parentDoc.defaultView.getComputedStyle(sidebar).width || '0') : 0;
            block.style.paddingLeft = sidebarWidth <= 1 ? 'calc(24px + 1cm)' : '24px';
          };

          applyMainMargin();
          parentDoc.defaultView.addEventListener('resize', applyMainMargin);
          new MutationObserver(applyMainMargin).observe(parentDoc.body, { childList: true, subtree: true, attributes: true });
        })();
        </script>
        """,
        height=0,
        width=0,
    )


def _render_copilot_spacing_css(is_open: bool, is_full_width: bool) -> None:
    if is_full_width:
        reserved_width = "0px"
    elif is_open:
        reserved_width = "30rem"
    else:
        reserved_width = "9rem"

    st.markdown(
        f"""
        <style>
        section[data-testid="stMain"] .block-container {{
            padding-right: calc(1.5rem + 1cm + {reserved_width}) !important;
        }}

        @media (max-width: 900px) {{
            section[data-testid="stMain"] .block-container {{
                padding-right: 1rem !important;
            }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


# ── Page config ──────────────────────────────────────────────────────────────

st.set_page_config(page_title=APP_NAME, page_icon="📄", layout="wide")

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Cookie&display=swap');
    </style>
    <style>
    /* ── Global ──────────────────────────────────────────── */
    .block-container {
        padding-top: 1.5rem;
        padding-left: 1.5rem;
        padding-right: 1.5rem;
        max-width: 100%;
    }

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
    div[data-testid="column"]:has(.copilot-sticky-anchor) {
        align-self: flex-start !important;
        position: relative !important;
    }
    .copilot-resize-handle {
        position: absolute;
        top: 0;
        left: -14px;
        width: 18px;
        height: 100%;
        cursor: ew-resize;
        z-index: 80;
        pointer-events: auto;
        background: linear-gradient(90deg, rgba(226, 232, 240, 0.45), rgba(226, 232, 240, 0));
    }
    .copilot-resize-handle::before {
        content: "";
        position: absolute;
        top: 18px;
        bottom: 18px;
        left: 11px;
        width: 3px;
        border-radius: 999px;
        background: rgba(100, 116, 139, 0.95);
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
    .chat-window {
        border: 1px solid #dbe4f0;
        border-radius: 12px;
        background: #ffffff;
        padding: 0.2rem;
        margin-top: 0.35rem;
    }
    .chat-controls {
        margin-top: 0.25rem;
    }

    /* ── Support card ───────────────────────────────────── */
    .support-widget {
        position: fixed;
        right: 18px;
        bottom: 18px;
        z-index: 999;
    }
    .support-link {
        display: inline-flex;
        align-items: center;
        gap: 0.55rem;
        text-decoration: none !important;
        background: #FFDD00;
        color: #000000 !important;
        border: 2px solid #000000;
        border-radius: 999px;
        padding: 0.72rem 1rem;
        font-family: 'Cookie', cursive;
        font-size: 2rem;
        font-weight: 400;
        box-shadow: 0 14px 32px rgba(15, 23, 42, 0.16);
        line-height: 1;
    }
    .support-link:hover {
        background: #ffe766;
        color: #000000 !important;
        text-decoration: none !important;
    }
    .support-link-icon {
        width: 2.2rem;
        height: 2.2rem;
        border-radius: 8px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        overflow: hidden;
        flex-shrink: 0;
    }
    .support-link-icon img {
        width: 100%;
        height: 100%;
        object-fit: cover;
        display: block;
    }
    @media (max-width: 900px) {
        .support-widget {
            right: 10px;
            bottom: 10px;
        }
        .support-link {
            font-size: 1.6rem;
            padding: 0.68rem 0.9rem;
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
    "<p>AI-powered resume evaluation, optimization, and application copilot.</p>"
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
    #         language="toml",
    #     )
    #     st.caption("Keys stay on your machine and are never uploaded.")

    st.divider()
    st.markdown("**How to use**")
    st.markdown(
        "1. Upload your `.docx`, `.pdf`, or `.doc` resumes\n"
        "2. Paste the job description\n"
        "3. Click **Match Best Resume**\n"
        "4. Get improvement suggestions\n"
        "5. Generate optimized resume & review\n"
        "6. Export\n"
        "7. Generate and export a cover letter"
    )

base_url = provider["base_url"]
json_mode = provider["json_mode"]

if "copilot_open" not in st.session_state:
    st.session_state["copilot_open"] = False
if "copilot_full_width" not in st.session_state:
    st.session_state["copilot_full_width"] = False

copilot_open = st.session_state.get("copilot_open", False)
copilot_full_width = st.session_state.get("copilot_full_width", False)
_render_copilot_spacing_css(copilot_open, copilot_full_width)
_sync_main_margin_with_sidebar()

if copilot_open and copilot_full_width:
    chat_col = st.container()
    main_col = None
elif copilot_open:
    main_col, chat_col = st.columns([0.88, 1.12], gap="large")
else:
    main_col, chat_col = st.columns([1.34, 0.66], gap="large")

if main_col is not None:
    with main_col:
        def _fill_sample():
            st.session_state["job_desc_input"] = SAMPLE_JD

        col_jd, col_sample = st.columns([4.6, 1], vertical_alignment="bottom")
        with col_jd:
            st.subheader("1 — Job Description", help=STEP_TOOLTIPS[1])
        with col_sample:
            st.button("Try sample", on_click=_fill_sample, use_container_width=True)

        job_desc = st.text_area(
            "Paste the job description below",
            height=220,
            placeholder="Copy-paste the full job posting here…",
            key="job_desc_input",
        )

        st.subheader("2 — Your Resumes", help=STEP_TOOLTIPS[2])

        uploaded_files = st.file_uploader(
            "Upload resumes (.docx, .pdf, .doc)",
            type=["docx", "pdf", "doc"],
            accept_multiple_files=True,
            help="Drag and drop one or more .docx, .pdf, or .doc resume files.",
        )

        all_resumes: list[Resume] = []

        if uploaded_files:
            for uf in uploaded_files:
                try:
                    resume = read_resume_from_bytes(uf.getvalue(), uf.name)
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
            st.info("Upload your `.docx`, `.pdf`, or `.doc` resumes above to get started.")
            selected_resumes = []

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

        st.subheader("6 — Export", help=STEP_TOOLTIPS[6])

        if "optimized" in st.session_state:
            opt_export = st.session_state["optimized"]
            source_resume: Resume | None = st.session_state.get("optimized_source_resume")
            source_suffix = Path(source_resume.filename).suffix.lower() if source_resume else ""
            is_pdf_source = source_suffix == ".pdf"
            has_original_template = bool(source_resume and source_resume.raw_bytes and not is_pdf_source)
            can_reconstruct_pdf_source = bool(source_resume and is_pdf_source and api_key)

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

            if is_pdf_source:
                st.info(
                    "PDF source detected. Export will reconstruct the original style as closely as possible. "
                    "Exact formatting preservation remains available for Word uploads."
                )
            elif not has_original_template:
                st.warning(
                    "A .docx template is required for export-preserving formatting. "
                    "Use a .docx resume (or a .doc that can be converted) for full export."
                )

            can_export_word = company_name.strip() and (has_original_template or can_reconstruct_pdf_source)
            can_export_pdf = company_name.strip() and (has_original_template or can_reconstruct_pdf_source)

            col_exp_word, col_exp_pdf = st.columns(2)

            with col_exp_word:
                word_btn = st.button(
                    "Export to Word",
                    disabled=not can_export_word,
                    use_container_width=True,
                    type="primary",
                )

            with col_exp_pdf:
                pdf_btn = st.button(
                    "Export to PDF",
                    disabled=not can_export_pdf,
                    use_container_width=True,
                    type="primary",
                )

            if word_btn and has_original_template:
                with st.spinner("Generating Word file…"):
                    try:
                        docx_path = export_docx(source_resume.raw_bytes, opt_export, OUTPUT_DIR, company_name.strip())
                        st.session_state["export_docx_path"] = docx_path
                        st.session_state["export_word_done"] = True
                        st.session_state["pdf_export_style_profile"] = None
                    except Exception as e:
                        st.error(f"Error: {e}")
            elif word_btn and can_reconstruct_pdf_source:
                with st.spinner("Reconstructing Word file from PDF style…"):
                    try:
                        docx_path, pdf_path, style_profile = export_pdf_origin_resume(
                            optimized=opt_export,
                            source_resume=source_resume,
                            output_dir=OUTPUT_DIR,
                            company_name=company_name.strip(),
                            api_key=api_key,
                            model=active_model,
                            base_url=base_url,
                            json_mode=json_mode,
                        )
                        st.session_state["export_docx_path"] = docx_path
                        st.session_state["export_pdf_path"] = pdf_path
                        st.session_state["export_word_done"] = True
                        st.session_state["pdf_export_style_profile"] = style_profile
                    except Exception as e:
                        st.error(f"Error: {e}")

            if pdf_btn and has_original_template:
                with st.spinner("Generating PDF…"):
                    try:
                        docx_path = export_docx(source_resume.raw_bytes, opt_export, OUTPUT_DIR, company_name.strip())
                        pdf_path = _convert_to_pdf(docx_path)
                        st.session_state["export_docx_path"] = docx_path
                        st.session_state["export_pdf_path"] = pdf_path
                        st.session_state["export_pdf_done"] = True
                        st.session_state["pdf_export_style_profile"] = None
                    except Exception as e:
                        st.error(f"Error: {e}")
            elif pdf_btn and can_reconstruct_pdf_source:
                with st.spinner("Reconstructing PDF from original PDF style…"):
                    try:
                        docx_path, pdf_path, style_profile = export_pdf_origin_resume(
                            optimized=opt_export,
                            source_resume=source_resume,
                            output_dir=OUTPUT_DIR,
                            company_name=company_name.strip(),
                            api_key=api_key,
                            model=active_model,
                            base_url=base_url,
                            json_mode=json_mode,
                        )
                        st.session_state["export_docx_path"] = docx_path
                        st.session_state["export_pdf_path"] = pdf_path
                        st.session_state["export_pdf_done"] = True
                        st.session_state["pdf_export_style_profile"] = style_profile
                    except Exception as e:
                        st.error(f"Error: {e}")

            if st.session_state.get("export_word_done"):
                docx_path: Path | None = st.session_state.get("export_docx_path")
                if docx_path and docx_path.exists():
                    st.success(f"Ready: `{docx_path.name}`")
                    if st.session_state.get("pdf_export_style_profile"):
                        st.caption(
                            "PDF source export used reconstructed style: "
                            f"`{st.session_state['pdf_export_style_profile'].get('template', 'classic')}`"
                        )
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
                    st.success(f"Ready: `{pdf_path.name}`")
                    if st.session_state.get("pdf_export_style_profile"):
                        st.caption(
                            "PDF source export used reconstructed style: "
                            f"`{st.session_state['pdf_export_style_profile'].get('template', 'classic')}`"
                        )
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

        st.subheader("7 — Cover Letter", help=STEP_TOOLTIPS[7])

        if "cover_letter_company_name" not in st.session_state:
            st.session_state["cover_letter_company_name"] = st.session_state.get("company_name_export", "")
        optimized_source = st.session_state.get("optimized")
        fallback_resume: Resume | None = None
        fallback_name = ""

        if not optimized_source and "match_results" in st.session_state:
            best_name = st.session_state["match_results"].get("best_resume", "")
            fallback_resume = next((r for r in all_resumes if r.filename == best_name), None)
            fallback_name = best_name

        source_label, source_candidate_name, source_resume_text = resume_payload_from_source(
            optimized_source, fallback_resume
        )

        if source_label == "optimized_resume_step_5":
            st.success("Using optimized resume from Step 5 as cover letter source.")
        elif source_label == "best_resume_step_3":
            st.info(f"Using best matched resume from Step 3: `{fallback_name}`.")
        else:
            st.warning(
                "No source resume available yet. Generate an optimized resume in Step 5, "
                "or run Step 3 matching so the best resume can be used."
            )

        if source_label:
            st.session_state["cover_letter_source_meta"] = {
                "source": source_label,
                "candidate_name": source_candidate_name,
                "fallback_resume_filename": fallback_name,
            }

        col_tone, col_length = st.columns(2)
        with col_tone:
            tone = st.selectbox(
                "Tone / style",
                ["Professional", "Confident", "Warm", "Concise", "Technical", "Casual"],
                key="cover_letter_tone",
            )
        with col_length:
            length = st.selectbox(
                "Length",
                ["Short (150)", "Standard (250)", "Long (400-500)"],
                key="cover_letter_length",
            )

        col_hm, col_job = st.columns(2)
        with col_hm:
            hiring_manager_name = st.text_input(
                "Hiring manager name (optional)",
                key="cover_letter_hiring_manager_name",
                placeholder="e.g. Alex Johnson",
            )
        with col_job:
            job_title_cl = st.text_input(
                "Job title (optional)",
                key="cover_letter_job_title",
                placeholder="e.g. Senior Data Scientist",
            )

        company_name_cl = st.text_input(
            "Company name (optional for draft; required for export filename)",
            key="cover_letter_company_name",
            placeholder="e.g. Google",
        )
        why_company = st.text_area(
            "Why this company (optional)",
            key="cover_letter_why_company",
            height=90,
            placeholder="Optional: mention what attracts you to this company.",
        )
        why_position = st.text_area(
            "Why this position (optional)",
            key="cover_letter_why_position",
            height=90,
            placeholder="Optional: mention why this role is a strong fit.",
        )
        generate_disabled = not (api_key and job_desc and source_label)

        gen_btn = st.button(
            "Generate Cover Letter",
            disabled=generate_disabled,
            use_container_width=True,
            type="primary",
        )

        if st.session_state.get("cover_letter_generated"):
            st.text_area(
                "What should be improved in the draft? (optional)",
                key="cover_letter_improvement_request",
                height=90,
                placeholder="E.g., make it shorter, more formal, and emphasize leadership.",
            )
            regen_btn = st.button(
                "Regenerate",
                disabled=generate_disabled,
                use_container_width=True,
            )
        else:
            regen_btn = False

        if gen_btn or regen_btn:
            with st.spinner("Generating cover letter…"):
                try:
                    improve_mode = bool(regen_btn and st.session_state.get("cover_letter_edited_draft"))
                    existing_draft = st.session_state.get("cover_letter_edited_draft", "") if improve_mode else ""
                    improvement_request = st.session_state.get("cover_letter_improvement_request", "").strip()
                    if improve_mode and improvement_request:
                        existing_draft = (
                            existing_draft.strip()
                            + "\n\nUSER REVISION REQUEST:\n"
                            + improvement_request
                        ).strip()
                    cl_result = generate_cover_letter(
                        job_description=job_desc,
                        resume_text=source_resume_text,
                        api_key=api_key,
                        model=active_model,
                        base_url=base_url,
                        json_mode=json_mode,
                        tone=tone,
                        length=length,
                        hiring_manager_name=hiring_manager_name,
                        job_title=job_title_cl,
                        company_name=company_name_cl,
                        why_company=why_company,
                        why_position=why_position,
                        existing_draft=existing_draft,
                        improve_mode=improve_mode,
                    )
                    draft = (cl_result.get("draft") or "").strip()
                    st.session_state["cover_letter_generated"] = draft
                    st.session_state["cover_letter_edited_draft"] = draft
                    st.session_state["cover_letter_soft_skills_used"] = cl_result.get("soft_skills_used", [])
                except Exception as e:
                    st.error(f"Error generating cover letter: {e}")

        if st.session_state.get("cover_letter_generated"):
            if st.session_state.get("cover_letter_soft_skills_used"):
                skills = st.session_state["cover_letter_soft_skills_used"]
                st.caption("Soft skills emphasized: " + ", ".join(f"`{s}`" for s in skills))

            st.text_area(
                "Edit your cover letter draft (modern simplified format)",
                key="cover_letter_edited_draft",
                height=320,
            )

            export_ready = bool(st.session_state.get("cover_letter_edited_draft", "").strip() and company_name_cl.strip())
            if not company_name_cl.strip():
                st.caption("Add company name to enable cover letter export.")

            cexp_docx, cexp_pdf = st.columns(2)
            with cexp_docx:
                export_cl_docx_btn = st.button(
                    "Export Cover Letter to Word",
                    disabled=not export_ready,
                    use_container_width=True,
                    type="primary",
                )
            with cexp_pdf:
                export_cl_pdf_btn = st.button(
                    "Export Cover Letter to PDF",
                    disabled=not export_ready,
                    use_container_width=True,
                    type="primary",
                )

            if export_cl_docx_btn:
                with st.spinner("Generating cover letter .docx…"):
                    try:
                        business_text = build_export_letter(
                            draft_body=st.session_state["cover_letter_edited_draft"],
                            candidate_name=source_candidate_name,
                            hiring_manager_name=hiring_manager_name,
                            job_title=job_title_cl,
                            company_name=company_name_cl,
                        )
                        cl_docx = export_cover_letter_docx(
                            cover_letter_text=business_text,
                            candidate_name=source_candidate_name,
                            company_name=company_name_cl,
                            output_dir=OUTPUT_DIR,
                        )
                        st.session_state["cover_letter_docx_path"] = cl_docx
                        st.session_state["cover_letter_export_docx_done"] = True
                    except Exception as e:
                        st.error(f"Export error: {e}")

            if export_cl_pdf_btn:
                with st.spinner("Generating cover letter PDF…"):
                    try:
                        business_text = build_export_letter(
                            draft_body=st.session_state["cover_letter_edited_draft"],
                            candidate_name=source_candidate_name,
                            hiring_manager_name=hiring_manager_name,
                            job_title=job_title_cl,
                            company_name=company_name_cl,
                        )
                        cl_docx = export_cover_letter_docx(
                            cover_letter_text=business_text,
                            candidate_name=source_candidate_name,
                            company_name=company_name_cl,
                            output_dir=OUTPUT_DIR,
                        )
                        cl_pdf = _convert_to_pdf(cl_docx)
                        st.session_state["cover_letter_docx_path"] = cl_docx
                        st.session_state["cover_letter_pdf_path"] = cl_pdf
                        st.session_state["cover_letter_export_pdf_done"] = True
                    except Exception as e:
                        st.error(f"Export error: {e}")

            if st.session_state.get("cover_letter_export_docx_done"):
                cl_docx_path: Path | None = st.session_state.get("cover_letter_docx_path")
                if cl_docx_path and cl_docx_path.exists():
                    with open(cl_docx_path, "rb") as f:
                        st.download_button(
                            label="Download Cover Letter (.docx)",
                            data=f.read(),
                            file_name=cl_docx_path.name,
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            use_container_width=True,
                        )

            if st.session_state.get("cover_letter_export_pdf_done"):
                cl_pdf_path: Path | None = st.session_state.get("cover_letter_pdf_path")
                if cl_pdf_path and cl_pdf_path.exists():
                    with open(cl_pdf_path, "rb") as f:
                        st.download_button(
                            label="Download Cover Letter (.pdf)",
                            data=f.read(),
                            file_name=cl_pdf_path.name,
                            mime="application/pdf",
                            use_container_width=True,
                        )
                else:
                    st.warning("PDF conversion requires LibreOffice. Try exporting to Word instead.")
else:
    job_desc = st.session_state.get("job_desc_input", "")
    selected_resumes = []
    selected_resume = st.session_state.get("optimized_source_resume")

copilot_resume = _resolve_copilot_resume(selected_resumes, selected_resume)
analysis_context = build_analysis_context(
    st.session_state.get("match_results"),
    st.session_state.get("improvements"),
    st.session_state.get("optimized"),
)
company_name_for_chat = _infer_company_name(job_desc)
company_context = st.session_state.get("copilot_company_context", "")
if company_name_for_chat and st.session_state.get("copilot_company_context_name") != company_name_for_chat:
    company_context = _store_company_context(company_name_for_chat)

latest_assistant_message = next(
    (m.get("content", "") for m in reversed(st.session_state.get("copilot_messages", [])) if m.get("role") == "assistant"),
    "",
)
can_expand_long_answer = len(latest_assistant_message) > 900
if not can_expand_long_answer and st.session_state.get("copilot_full_width"):
    st.session_state["copilot_full_width"] = False

with chat_col:
    st.markdown('<div class="copilot-sticky-anchor"></div>', unsafe_allow_html=True)
    if not copilot_open:
        launcher_container = st.container()
        with launcher_container:
            st.markdown('<div class="copilot-launcher-anchor"></div>', unsafe_allow_html=True)
            st.button(
                "Chat with copilot",
                key="open_copilot_launcher",
                use_container_width=True,
                on_click=_toggle_copilot,
            )
            _style_copilot_launcher_button()
    else:
        st.markdown(
            """
            <div class="copilot-panel">
                <h3>Chat with Copilot</h3>
                <p>Ask follow-up questions without re-pasting your resume or the job description.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.button(
            "Hide Copilot",
            key="hide_copilot_launcher",
            use_container_width=True,
            on_click=_toggle_copilot,
        )
        st.markdown(
            f'<span class="copilot-meta">Job description: {"ready" if bool(job_desc.strip()) else "missing"}</span>'
            f'<span class="copilot-meta">Resume: {"ready" if copilot_resume is not None else "missing"}</span>'
            f'<span class="copilot-meta">Analysis: {"ready" if bool(analysis_context) else "pending"}</span>',
            unsafe_allow_html=True,
        )

        if can_expand_long_answer:
            st.button(
                "Expand Answer View" if not st.session_state.get("copilot_full_width", False) else "Return to Split View",
                key="copilot_full_width_toggle",
                use_container_width=True,
                on_click=_toggle_copilot_full_width,
            )

        sample_prompts = [
            "Why am I a strong fit for this role?",
            "What recruiter concerns might come up?",
        ]

        if "copilot_messages" not in st.session_state:
            st.session_state["copilot_messages"] = []

        st.markdown('<div class="chat-controls">', unsafe_allow_html=True)
        prompt_cols = st.columns(2)
        for idx, prompt in enumerate(sample_prompts):
            if prompt_cols[idx % 2].button(prompt, key=f"copilot_prompt_{idx}", use_container_width=True):
                st.session_state["copilot_draft"] = prompt

        with st.form("copilot_form", clear_on_submit=True):
            question_input = st.text_area(
                "",
                key="copilot_draft",
                height=90,
                placeholder="Ask anything",
                label_visibility="collapsed",
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
                        answer = answer_follow_up(
                            question=question,
                            job_description=job_desc,
                            resume=copilot_resume,
                            api_key=api_key,
                            model=active_model,
                            base_url=base_url,
                            company_name=company_name_for_chat,
                            company_context=company_context,
                            analysis_context=analysis_context,
                            conversation_history=st.session_state["copilot_messages"][:-1],
                        )
                    except Exception as e:
                        answer = f"Copilot error: {e}"
                st.session_state["copilot_messages"].append({"role": "assistant", "content": answer})
                st.rerun()
        if st.session_state["copilot_messages"]:
            st.markdown('<div class="chat-window">', unsafe_allow_html=True)
            chat_history = st.container(height=560 if st.session_state.get("copilot_full_width", False) else 360)
            with chat_history:
                for message in st.session_state["copilot_messages"]:
                    with st.chat_message(message["role"]):
                        st.write(message["content"])
            st.markdown('</div>', unsafe_allow_html=True)
    _pin_copilot_panel()

bmc_logo_data_uri = _image_data_uri(BMC_LOGO_PATH)
st.markdown(
    f"""
    <div class="support-widget">
        <a
            class="support-link"
            href="https://buymeacoffee.com/Milica.A"
            target="_blank"
            rel="noopener noreferrer"
        >
            <span class="support-link-icon"><img src="{bmc_logo_data_uri}" alt="Buy me a coffee logo" /></span>
            <span>Buy me a coffee</span>
        </a>
    </div>
    """,
    unsafe_allow_html=True,
)
