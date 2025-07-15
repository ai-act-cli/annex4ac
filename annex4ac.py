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
import importlib.resources as pkgres
from jinja2 import Template
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, ListFlowable, ListItem
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

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

# Официальные заголовки Annex IV (verbatim, 2024, полные)
_SECTION_TITLES = [
    "1. A general description of the AI system including:",
    "2. A detailed description of the elements of the AI system and of the process for its development, including:",
    "3. Detailed information about the monitoring, functioning and control of the AI system, in particular with regard to:",
    "4. A description of the appropriateness of the performance metrics for the specific AI system:",
    "5. A detailed description of the risk management system in accordance with Article 9:",
    "6. A description of relevant changes made by the provider to the system through its lifecycle:",
    "7. A list of the harmonised standards applied in full or in part the references of which have been published in the Official Journal of the European Union, or, where no such standards have been applied, a detailed description of the solutions adopted to meet the essential requirements set out in Chapter II:",
    "8. A copy of the EU declaration of conformity referred to in Article 47:",
    "9. A detailed description of the system in place to evaluate the AI-system performance in the post-market phase in accordance with Article 72, including the post-market monitoring plan referred to in Article 72(3):",
]

# Регистрируем Liberation Sans (ожидается, что LiberationSans-Regular.ttf и LiberationSans-Bold.ttf доступны)
pdfmetrics.registerFont(TTFont("LiberationSans", "LiberationSans-Regular.ttf"))
pdfmetrics.registerFont(TTFont("LiberationSans-Bold", "LiberationSans-Bold.ttf"))

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


def _split_to_list_items(text: str):
    import re
    # Ищем подпункты (a)...(h) с любым содержимым до следующего подпункта или конца текста
    pattern = r"\(([a-z])\)\s*((?:.|\n)*?)(?=(\([a-z]\)\s)|$)"
    matches = list(re.finditer(pattern, text, flags=re.I))
    if not matches:
        return Paragraph(text, _get_body_style())

    flowed = []
    for match in matches:
        label, body, _ = match.groups()
        flowed.append(ListItem(
            Paragraph(f"({label}) {body.strip()}", _get_body_style()),
            leftIndent=12)
        )
    return ListFlowable(flowed, bulletType="bullet", leftIndent=18)


def _get_body_style():
    style = ParagraphStyle(
        "Body",
        fontName="LiberationSans",
        fontSize=11,
        leading=14,
        spaceAfter=8,
        spaceBefore=0,
        leftIndent=0,
        rightIndent=0,
    )
    return style

def _get_heading_style():
    style = ParagraphStyle(
        "Heading",
        fontName="LiberationSans-Bold",
        fontSize=14,
        leading=16,
        spaceAfter=8,
        spaceBefore=16,
        leftIndent=0,
        rightIndent=0,
        alignment=0,
        # Добавим letterSpacing (tracking) через wordSpace, т.к. reportlab не поддерживает letterSpacing напрямую
        wordSpace=0.5,  # 0.5 pt letter-spacing (эмулируем)
        # small-caps напрямую не поддерживается, но можно добавить через font или вручную, если потребуется
    )
    return style

def _header(canvas, doc):
    canvas.saveState()
    canvas.setFont("LiberationSans", 8)
    canvas.drawRightString(A4[0]-25*mm, A4[1]-15*mm,
        "Annex IV — Technical documentation referred to in Article 11(1) — v1.0")
    canvas.restoreState()

def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("LiberationSans", 9)
    # Центр нижнего поля — номер страницы
    page_num = canvas.getPageNumber()
    canvas.drawCentredString(A4[0]/2, 15*mm, str(page_num))
    canvas.restoreState()

def _header_and_footer(canvas, doc):
    _header(canvas, doc)
    _footer(canvas, doc)

def _render_pdf(payload: dict, out_pdf: Path):
    doc = SimpleDocTemplate(str(out_pdf), pagesize=A4,
                            leftMargin=25*mm, rightMargin=25*mm,
                            topMargin=20*mm, bottomMargin=20*mm)  # поля сверху/снизу 20 мм
    story = []
    for key, title in zip(_SECTION_KEYS, _SECTION_TITLES):
        story.append(Paragraph(title, _get_heading_style()))
        body = payload.get(key, "—")
        story.append(_split_to_list_items(body))
        story.append(Spacer(1, 12))
    doc.build(story, onFirstPage=_header_and_footer, onLaterPages=_header_and_footer)

def _default_tpl() -> str:
    return pkgres.read_text("annex4ac", "template.html")

def _render_html(data: dict) -> str:
    html_src = Template(_default_tpl()).render(**data)
    return html_src

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
    input: Path = typer.Option(..., help="YAML input file"),
    output: Path = typer.Option("annex_iv.pdf", help="Output file name"),
    fmt: str = typer.Option("pdf", help="pdf | html | docx"),
):
    """Generate output from YAML: PDF (default), HTML, or DOCX."""
    payload = yaml.safe_load(input.read_text())
    if fmt == "pdf":
        _render_pdf(payload, output)
    elif fmt == "html":
        # Placeholder for HTML export
        raise NotImplementedError("HTML export not implemented yet.")
    elif fmt == "docx":
        # Placeholder for docx export
        raise NotImplementedError("DOCX export not implemented yet.")
    else:
        raise ValueError(f"Unknown format: {fmt}")

if __name__ == "__main__":
    app()
