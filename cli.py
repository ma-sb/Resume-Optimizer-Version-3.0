#!/usr/bin/env python3
"""Terminal CLI for Resume Polisher (Typer). Uses .env for GEMINI_API_KEY by default."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Annotated, Optional

import typer
from dotenv import load_dotenv

# Project root on sys.path so `core` imports work when run as `python cli.py`
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.exporter import export_docx
from core.matcher import PROVIDERS, get_improvements, match_resumes, optimize_resume
from core.reader import read_docx

load_dotenv(_ROOT / ".env")

app = typer.Typer(
    name="resume-polisher",
    help="Resume Polisher — match, improve, optimize, and export resumes (Gemini / .env).",
    no_args_is_help=True,
)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _get_api_key(explicit: Optional[str]) -> str:
    if explicit:
        return explicit.strip()
    key = (
        os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
        or os.environ.get("API_KEY")
    )
    if not key or not key.strip():
        typer.secho(
            "Missing API key. Set GEMINI_API_KEY in .env (see .env.example) or pass --api-key.",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)
    return key.strip()


def _provider(provider_name: str) -> dict:
    if provider_name not in PROVIDERS:
        typer.secho(f"Unknown provider: {provider_name}", err=True, fg=typer.colors.RED)
        raise typer.Exit(1)
    return PROVIDERS[provider_name]


def _write_json(path: Optional[Path], data: dict) -> None:
    if path is None:
        typer.echo(json.dumps(data, indent=2))
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    typer.secho(f"Wrote {path}", fg=typer.colors.GREEN)


@app.command("match")
def cmd_match(
    jd: Annotated[Path, typer.Option("--jd", help="Path to job description (.txt)")],
    resume: Annotated[
        list[Path],
        typer.Option("--resume", help=".docx resume path (repeat for multiple)"),
    ],
    output: Annotated[
        Optional[Path],
        typer.Option("--out", "-o", help="Write JSON result to this file (default: stdout)"),
    ] = None,
    provider: Annotated[
        str,
        typer.Option("--provider", help="LLM provider name"),
    ] = "Google Gemini",
    model: Annotated[
        Optional[str],
        typer.Option("--model", help="Model id (default: provider default)"),
    ] = None,
    api_key: Annotated[
        Optional[str],
        typer.Option("--api-key", help="Override API key (else GEMINI_API_KEY from .env)"),
    ] = None,
) -> None:
    """Score and rank one or more resumes against a job description."""
    if not resume:
        typer.secho("Pass at least one --resume path.", err=True, fg=typer.colors.RED)
        raise typer.Exit(1)
    prov = _provider(provider)
    m = model or prov["default"]
    key = _get_api_key(api_key)
    resumes = [read_docx(p) for p in resume]
    result = match_resumes(
        _read_text(jd),
        resumes,
        key,
        m,
        prov["base_url"],
        prov["json_mode"],
    )
    _write_json(output, result)


@app.command("improve")
def cmd_improve(
    jd: Annotated[Path, typer.Option("--jd", help="Path to job description (.txt)")],
    resume: Annotated[Path, typer.Option("--resume", help="Single .docx resume")],
    output: Annotated[
        Optional[Path],
        typer.Option("--out", "-o", help="Write JSON to file (default: stdout)"),
    ] = None,
    provider: Annotated[str, typer.Option("--provider")] = "Google Gemini",
    model: Annotated[Optional[str], typer.Option("--model")] = None,
    api_key: Annotated[Optional[str], typer.Option("--api-key")] = None,
) -> None:
    """Get bullet-level improvement suggestions for one resume."""
    prov = _provider(provider)
    m = model or prov["default"]
    key = _get_api_key(api_key)
    r = read_docx(resume)
    result = get_improvements(
        _read_text(jd),
        r,
        key,
        m,
        prov["base_url"],
        prov["json_mode"],
    )
    _write_json(output, result)


@app.command("optimize")
def cmd_optimize(
    jd: Annotated[Path, typer.Option("--jd", help="Path to job description (.txt)")],
    resume: Annotated[Path, typer.Option("--resume", help=".docx resume to optimize")],
    output: Annotated[
        Optional[Path],
        typer.Option("--out", "-o", help="Write optimized resume JSON (default: stdout)"),
    ] = None,
    approved_rewrites: Annotated[
        Optional[Path],
        typer.Option(
            "--approved-rewrites",
            help="Optional JSON file with list of {section, original, rewritten}",
        ),
    ] = None,
    provider: Annotated[str, typer.Option("--provider")] = "Google Gemini",
    model: Annotated[Optional[str], typer.Option("--model")] = None,
    api_key: Annotated[Optional[str], typer.Option("--api-key")] = None,
) -> None:
    """Generate a fully optimized resume (JSON) for the job."""
    prov = _provider(provider)
    m = model or prov["default"]
    key = _get_api_key(api_key)
    r = read_docx(resume)
    approved: list[dict] | None = None
    if approved_rewrites is not None:
        raw = json.loads(approved_rewrites.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            approved = raw
        elif isinstance(raw, dict) and "approved_rewrites" in raw:
            approved = raw["approved_rewrites"]
        else:
            typer.secho("approved-rewrites JSON must be a list or {approved_rewrites: [...]}", err=True)
            raise typer.Exit(1)
    result = optimize_resume(
        _read_text(jd),
        r,
        key,
        m,
        prov["base_url"],
        prov["json_mode"],
        approved,
    )
    _write_json(output, result)


@app.command("export")
def cmd_export(
    resume: Annotated[Path, typer.Option("--resume", help="Original .docx file (template)")],
    optimized: Annotated[
        Path,
        typer.Option("--optimized", help="JSON file from `optimize` command"),
    ],
    company: Annotated[str, typer.Option("--company", help="Company name for filename")],
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", "-d", help="Directory for exported .docx"),
    ] = Path("output"),
) -> None:
    """Write optimized content into a clone of the original .docx."""
    data = json.loads(optimized.read_text(encoding="utf-8"))
    raw = Path(resume).read_bytes()
    path = export_docx(raw, data, output_dir, company)
    typer.secho(f"Wrote {path}", fg=typer.colors.GREEN)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
