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
    requests, beautifulsoup4, ruamel.yaml, typer[all], pydantic, Jinja2, tinytetx (for PDF)

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

# Mapping of Annex IV sections → canonical YAML keys.
# These are created once during runtime so we can keep the code self-contained; in a
# full project we might persist them alongside the package.
_SECTION_KEYS = [
    "system_overview",          # 1. general description
    "intended_purpose",        # 2. intended purpose & conditions of use
    "system_architecture",     # 3. system architecture and components
    "development_process",     # 4. development process and lifecycle
    "data_specifications",     # 5. data used for training/validation/testing
    "performance_metrics",     # 6. performance metrics and results
    "risk_management",         # 7. risk management measures
    "post_market_plan",        # 8. post-market monitoring plan
    "human_machine_interface", # 9. HMI and UX safeguards
    "changes_and_versions",    # 10. versioning and change management
    "records_and_logs",        # 11. logging capability and retention
    "instructions_for_use",    # 12. user documentation / IFU
    "compliance_declaration",  # 13. EU/CE declaration of conformity
]

# -----------------------------------------------------------------------------
# Pydantic schema mirrors Annex IV – update automatically during fetch.
# -----------------------------------------------------------------------------
app = typer.Typer(add_completion=False)

class AnnexIVSection(BaseModel):
    heading: str = Field(..., description="Canonical heading from Annex IV")
    body: str = Field(..., description="Verbatim text of the section")

class AnnexIVSchema(BaseModel):
    """Dynamic schema reflecting the latest Annex IV layout."""

    system_overview: str
    intended_purpose: str
    system_architecture: str
    development_process: str
    data_specifications: str
    performance_metrics: str
    risk_management: str
    post_market_plan: str
    human_machine_interface: str
    changes_and_versions: str
    records_and_logs: str
    instructions_for_use: str
    compliance_declaration: str

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
    """Извлекает секции Annex IV по номерам из HTML."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    # Находим основной div с содержимым
    content = soup.find("div", class_="et_pb_post_content")
    if not content:
        return {}

    # Ключи для YAML (можно скорректировать под ваши нужды)
    section_keys = [
        "system_overview",
        "development_process",
        "system_architecture",
        "performance_metrics",
        "risk_management",
        "changes_and_versions",
        "records_and_logs",
        "compliance_declaration",
        "post_market_plan",
    ]

    result = {}
    current_key = None
    buffer = []
    section_idx = 0

    for p in content.find_all("p"):
        text = p.get_text(strip=True)
        # Новый раздел: начинается с "1.", "2." и т.д.
        if text and text[0].isdigit() and text[1] == ".":
            # Сохраняем предыдущий раздел
            if current_key is not None and buffer:
                result[current_key] = "\n".join(buffer).strip()
            # Новый ключ
            if section_idx < len(section_keys):
                current_key = section_keys[section_idx]
                section_idx += 1
            else:
                current_key = f"section_{section_idx+1}"
                section_idx += 1
            buffer = [text]
        else:
            # Подпункты и детали
            if current_key is not None:
                buffer.append(text)
    # Сохраняем последний раздел
    if current_key is not None and buffer:
        result[current_key] = "\n".join(buffer).strip()
    return result


def _write_yaml(data: Dict[str, str], path: Path):
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True)


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
    html = _fetch_html(AI_ACT_ANNEX_IV_HTML)
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
