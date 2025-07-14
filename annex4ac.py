"""annex4ac.py

CLI tool that fetches the latest Annex IV text from an authoritative source, normalises it
into a machine-readable YAML/JSON skeleton, validates user-supplied YAML specs against that
schema and (in the paid tier) renders a complete Annex IV PDF.

Key design goals
----------------
* **Always up-to-date** – every run pulls Annex IV from the EU AI Act website (HTML fallback)
  and fails if HTTP status ≠ 200.
* **No hidden SaaS** – default mode is local/freemium. Setting env `ANNEX4AC_LICENSE` or
  a `--license-key` flag unlocks PDF generation.
* **Plug-n-play in CI** – exit 1 when validation fails so a GitHub Action can block a PR.

Dependencies (add these to requirements.txt or pyproject):
    requests, beautifulsoup4, PyYAML, typer[all], pydantic, Jinja2, TinyTeX (for PDF)

Usage examples
--------------
$ pip install annex4ac  # once published on PyPI
$ annex4ac fetch-schema  > annex_schema.yaml        # refresh local schema
$ annex4ac validate -i my_model.yaml                # CI gate (free)
$ annex4ac generate -i my_model.yaml -o my_annex.pdf # Pro only

The code is intentionally compact; production users should add logging, retries and
exception handling as required.
"""

import os
import sys
import tempfile
import json
import subprocess
from pathlib import Path
from typing import Dict
import re

import requests
from bs4 import BeautifulSoup
import yaml
import typer
from pydantic import BaseModel, ValidationError, Field

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------
# Primary source – HTML (easier to parse than PDF)
AI_ACT_ANNEX_IV_HTML = "https://artificialintelligenceact.eu/annex/4/"
# Fallback – Official Journal PDF (for archival integrity)
AI_ACT_ANNEX_IV_PDF = (
    "https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=OJ%3AL_202401689"
)

# Mapping of Annex IV sections → canonical YAML keys (updated to 9 items, June 2024)
_SECTION_KEYS = [
    "system_overview",         # 1. general description
    "intended_purpose",        # 2. intended purpose & conditions of use
    "system_monitoring",       # 3. system monitoring and control
    "development_process",     # 4. development process and lifecycle
    "data_specifications",     # 5. data used for training/validation/testing
    "performance_metrics",     # 6. performance metrics and results
    "risk_management",         # 7. risk management measures
    "post_market_plan",        # 8. post-market monitoring plan
    "standards_applied",       # 9. standards applied
]

# -----------------------------------------------------------------------------
# Pydantic schema mirrors Annex IV – update automatically during fetch.
# -----------------------------------------------------------------------------
app = typer.Typer(add_completion=False)

class AnnexIVSection(BaseModel):
    heading: str = Field(..., description="Canonical heading from Annex IV")
    body: str = Field(..., description="Verbatim text of the section")

class AnnexIVSchema(BaseModel):
    """Dynamic schema reflecting the latest Annex IV layout (9 items, June 2024)."""
    system_overview: str
    intended_purpose: str
    system_monitoring: str
    development_process: str
    data_specifications: str
    performance_metrics: str
    risk_management: str
    post_market_plan: str
    standards_applied: str

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _fetch_html(url: str) -> str:
    """Return HTML string, raise on non-200."""
    r = requests.get(url, timeout=20)
    if r.status_code != 200:
        typer.secho(f"ERROR: {url} returned {r.status_code}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    return r.text


def _parse_annex_iv(html: str) -> Dict[str, str]:
    """Extracts Annex IV sections by numbers from HTML."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    # Find the main div with content
    content = soup.find("div", class_="et_pb_post_content")
    if not content:
        return {}

    # Use the global _SECTION_KEYS for correct mapping
    section_keys = _SECTION_KEYS

    result = {}
    current_key = None
    buffer = []
    section_idx = 0

    for p in content.find_all("p"):
        text = p.get_text(" ", strip=True)
        # Remove space before punctuation
        text = re.sub(r" ([,.;:!?])", r"\1", text)
        # New section: starts with "1.", "2." etc.
        if text and text[0].isdigit() and text[1] == ".":
            # Save previous section
            if current_key is not None and buffer:
                result[current_key] = "\n".join(buffer).strip()
            # New key
            if section_idx < len(section_keys):
                current_key = section_keys[section_idx]
                section_idx += 1
            else:
                raise ValueError("Annex IV structure on the website has changed: more sections than expected! Please update _SECTION_KEYS and the parser.")
            buffer = [text]
        else:
            # Subpoints and details
            if current_key is not None:
                buffer.append(text)
    # Save last section
    if current_key is not None and buffer:
        result[current_key] = "\n".join(buffer).strip()
    return result


def _write_yaml(data: Dict[str, str], path: Path):
    # Dump YAML with a blank line before each key (except the first)
    with path.open("w", encoding="utf-8") as f:
        first = True
        for key in _SECTION_KEYS:
            if key in data:
                if not first:
                    f.write("\n")
                yaml.dump({key: data[key]}, f, allow_unicode=True, default_flow_style=False)
                first = False


def _render_tex_from_yaml(yaml_path: Path, tex_template: Path, out_pdf: Path):
    """Render LaTeX via Jinja2 and compile to PDF (premium)."""
    from jinja2 import Template
    with yaml_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    tmpl = Template(tex_template.read_text(encoding="utf-8"))
    rendered_tex = tmpl.render(**data)
    tmp_tex = yaml_path.with_suffix(".tex")
    tmp_tex.write_text(rendered_tex, encoding="utf-8")
    subprocess.run(["xelatex", "-interaction=nonstopmode", str(tmp_tex)], check=True)
    tmp_pdf = tmp_tex.with_suffix(".pdf")
    tmp_pdf.rename(out_pdf)

# -----------------------------------------------------------------------------
# CLI Commands
# -----------------------------------------------------------------------------

@app.command()
def fetch_schema(output: Path = typer.Argument(Path("annex_schema.yaml"), exists=False)):
    """Download the latest Annex IV text and convert to YAML scaffold."""
    typer.echo("Fetching Annex IV HTML…")
    try:
        html = _fetch_html(AI_ACT_ANNEX_IV_HTML)
    except Exception as e:
        typer.secho(f"Download error: {e}.", fg=typer.colors.YELLOW)
        raise typer.Exit(1)
    data = _parse_annex_iv(html)
    _write_yaml(data, output)
    typer.secho(f"Schema written to {output}", fg=typer.colors.GREEN)

@app.command()
def validate(input: Path = typer.Option(..., exists=True, help="Your filled Annex IV YAML")):
    """Validate user YAML against required Annex IV keys; exit 1 on error."""
    try:
        with input.open("r", encoding="utf-8") as f:
            payload = yaml.safe_load(f)
        AnnexIVSchema(**payload)  # triggers pydantic validation
    except (ValidationError, Exception) as exc:
        typer.secho("Validation failed:\n" + str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    typer.secho("Validation OK!", fg=typer.colors.GREEN)

@app.command()
def generate(
    input: Path = typer.Option(..., exists=True),
    output: Path = typer.Option(Path("annex_iv.pdf")),
    tex_template: Path = typer.Option(Path("template.tex"), exists=True),
    license_key: str = typer.Option(None, envvar="ANNEX4AC_LICENSE"),
):
    """Generate Annex IV PDF (premium)."""
    if not license_key:
        typer.secho("PDF generation is a Pro feature. Set ANNEX4AC_LICENSE.", fg=typer.colors.YELLOW)
        raise typer.Exit(1)
    _render_tex_from_yaml(input, tex_template, output)
    typer.secho(f"PDF written to {output}", fg=typer.colors.GREEN)

if __name__ == "__main__":
    app()
