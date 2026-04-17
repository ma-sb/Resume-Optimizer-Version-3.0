"""Export an optimized resume by cloning the original .docx and converting to PDF."""

import io
import subprocess
import shutil
import tempfile
from pathlib import Path

from docx import Document


def _replace_paragraph_text(para, new_text: str):
    """Replace a paragraph's visible text while preserving run formatting.

    Puts new text in the first run, blanks middle runs, and keeps
    trailing whitespace/punctuation runs intact so tabs, periods,
    and alignment characters at the end of the line stay as-is.
    """
    if not para.runs:
        para.text = new_text
        return

    trail_start = len(para.runs)
    for i in range(len(para.runs) - 1, 0, -1):
        if para.runs[i].text.strip(" \t.,:;"):
            break
        trail_start = i

    para.runs[0].text = new_text
    for run in para.runs[1:trail_start]:
        run.text = ""


def _is_major_heading(para) -> bool:
    """Same heading detection as the reader."""
    text = para.text.strip()
    if not text or len(text) > 80:
        return False
    if para.style.name.startswith("Heading"):
        return True
    if not para.runs:
        return False
    return all(r.bold for r in para.runs if r.text.strip())


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _build_section_map(optimized: dict) -> dict[str, list[str]]:
    """Map normalized section headings to a flat list of their content items."""
    section_map: dict[str, list[str]] = {}
    for sec in optimized.get("sections", []):
        heading = sec.get("heading", "")
        key = _normalize(heading)
        items: list[str] = []
        content = sec.get("content", "")
        if content:
            items.append(content)
        items.extend(sec.get("bullets", []))
        section_map[key] = items
    return section_map


def export_docx(
    original_bytes: bytes,
    optimized: dict,
    output_dir: str | Path,
    company_name: str = "Company",
) -> Path:
    """
    Clone the original .docx, replace content paragraphs with optimized text
    while preserving all formatting, and save.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    name = optimized.get("name", "Candidate").strip()
    parts = name.split()
    first, last = (parts[0], parts[-1]) if len(parts) >= 2 else (name, "")
    safe = lambda s: s.replace(" ", "_").replace("/", "_")
    stem = f"{safe(first)}_{safe(last)}_Resume_{safe(company_name)}"

    doc = Document(io.BytesIO(original_bytes))
    section_map = _build_section_map(optimized)

    current_key: str | None = None
    bullet_idx = 0

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue

        if _is_major_heading(para):
            norm = _normalize(text)
            if norm in section_map:
                current_key = norm
                bullet_idx = 0
            else:
                current_key = None
            continue

        if current_key is not None:
            items = section_map[current_key]
            if bullet_idx < len(items):
                _replace_paragraph_text(para, items[bullet_idx])
                bullet_idx += 1

    docx_path = output_dir / f"{stem}.docx"
    doc.save(str(docx_path))
    return docx_path


def _convert_to_pdf(docx_path: Path) -> Path | None:
    """Try to convert a .docx to PDF using LibreOffice."""
    lo_cmd = None
    for candidate in ["libreoffice", "soffice", "/Applications/LibreOffice.app/Contents/MacOS/soffice"]:
        if shutil.which(candidate):
            lo_cmd = candidate
            break

    if not lo_cmd:
        return None

    with tempfile.TemporaryDirectory() as tmpdir:
        result = subprocess.run(
            [lo_cmd, "--headless", "--convert-to", "pdf", "--outdir", tmpdir, str(docx_path)],
            capture_output=True, timeout=60,
        )
        if result.returncode != 0:
            return None
        pdf_tmp = Path(tmpdir) / (docx_path.stem + ".pdf")
        if not pdf_tmp.exists():
            return None
        pdf_dest = docx_path.with_suffix(".pdf")
        shutil.move(str(pdf_tmp), str(pdf_dest))
        return pdf_dest


def export(
    original_bytes: bytes,
    optimized: dict,
    output_dir: str | Path,
    company_name: str = "Company",
) -> tuple[Path, Path | None]:
    """
    Export optimized resume. Returns (docx_path, pdf_path_or_None).
    The .docx always preserves original formatting. PDF requires LibreOffice.
    """
    docx_path = export_docx(original_bytes, optimized, output_dir, company_name)
    pdf_path = _convert_to_pdf(docx_path)
    return docx_path, pdf_path
