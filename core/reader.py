"""Read and parse .docx resume files."""

import io
from pathlib import Path
from dataclasses import dataclass, field
from docx import Document


@dataclass
class ResumeSection:
    heading: str
    bullets: list[str] = field(default_factory=list)


@dataclass
class Resume:
    filepath: Path
    filename: str
    full_text: str
    sections: list[ResumeSection] = field(default_factory=list)
    name: str = ""
    raw_bytes: bytes | None = None

    @property
    def display_name(self) -> str:
        return self.name or self.filename


def _is_major_heading(para) -> bool:
    """Detect top-level section headings (EXPERIENCE, EDUCATION, etc.)."""
    text = para.text.strip()
    if not text or len(text) > 80:
        return False
    if para.style.name.startswith("Heading"):
        return True
    if not para.runs:
        return False
    return all(r.bold for r in para.runs if r.text.strip())


def _parse_document(doc: Document, filename: str, filepath: Path | None = None) -> Resume:
    """Parse a python-docx Document into a Resume with per-paragraph bullets."""
    full_lines: list[str] = []
    sections: list[ResumeSection] = []
    current_section: ResumeSection | None = None
    candidate_name = ""

    for i, para in enumerate(doc.paragraphs):
        text = para.text.strip()
        if not text:
            continue

        full_lines.append(text)

        if i == 0 and not candidate_name:
            candidate_name = text

        if _is_major_heading(para):
            current_section = ResumeSection(heading=text)
            sections.append(current_section)
        elif current_section is not None:
            clean = text.lstrip("•-–◦▪ ")
            current_section.bullets.append(clean)

    return Resume(
        filepath=filepath or Path(filename),
        filename=filename,
        full_text="\n".join(full_lines),
        sections=sections,
        name=candidate_name,
    )


def read_docx(filepath: Path) -> Resume:
    """Parse a .docx file from disk."""
    filepath = Path(filepath)
    raw = filepath.read_bytes()
    doc = Document(io.BytesIO(raw))
    resume = _parse_document(doc, filepath.name, filepath)
    resume.raw_bytes = raw
    return resume


def read_docx_from_bytes(data: bytes, filename: str) -> Resume:
    """Parse a .docx file from raw bytes (e.g. an uploaded file)."""
    doc = Document(io.BytesIO(data))
    resume = _parse_document(doc, filename)
    resume.raw_bytes = data
    return resume


def load_resumes(folder: Path) -> list[Resume]:
    """Load all .docx resumes from a folder (recursively)."""
    folder = Path(folder)
    if not folder.exists():
        return []
    resumes = []
    for f in sorted(folder.rglob("*.docx")):
        if f.name.startswith("~$"):
            continue
        try:
            resumes.append(read_docx(f))
        except Exception as e:
            print(f"Warning: could not read {f.name}: {e}")
    return resumes
