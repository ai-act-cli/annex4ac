"""
annex4ac.py

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
* **Zero binaries** – no LaTeX, no system packages, no OPA binary: PDF and rule engine work via pure Python.

Dependencies (add these to requirements.txt or pyproject):
    requests, beautifulsoup4, PyYAML, typer[all], pydantic, Jinja2, reportlab

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
import json
import tempfile
import re
from pathlib import Path
from typing import Dict, Literal, List
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup
import yaml
import typer
from pydantic import BaseModel, ValidationError, Field
import importlib.resources as pkgres
from jinja2 import Template
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, ListFlowable, ListItem
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from policy.annex4ac_validate import validate_payload

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
    "system_overview",
    "development_process",
    "system_monitoring",
    "performance_metrics",
    "risk_management",
    "changes_and_versions",
    "standards_applied",
    "compliance_declaration",
    "post_market_plan",
]

# Official Annex IV section titles (verbatim, 2024, full)
_SECTION_TITLES = [
    "1. A general description of the AI system including:",
    "2. A detailed description of the elements of the AI system and of the process for its development, including:",
    "3. Detailed information about the monitoring, functioning and control of the AI system, in particular with regard to:",
    "4. A description of the appropriateness of the performance metrics for the specific AI system:",
    "5. A detailed description of the risk management system in accordance with Article 9:",
    "6. A description of relevant changes made by the provider to the system through its lifecycle:",
    "7. A list of the harmonised standards applied in full or in part the references of which have been published in the Official Journal of the European Union; where no such harmonised standards have been applied, a detailed description of the solutions adopted to meet the requirements set out in Chapter III, Section 2, including a list of other relevant standards and technical specifications applied:",
    "8. A copy of the EU declaration of conformity referred to in Article 47:",
    "9. A detailed description of the system in place to evaluate the AI-system performance in the post-market phase in accordance with Article 72, including the post-market monitoring plan referred to in Article 72(3):",
]

# Register Liberation Sans (expects LiberationSans-Regular.ttf and LiberationSans-Bold.ttf to be available)
FONTS_DIR = Path(__file__).parent / "fonts"
pdfmetrics.registerFont(TTFont("LiberationSans", str(FONTS_DIR / "LiberationSans-Regular.ttf")))
pdfmetrics.registerFont(TTFont("LiberationSans-Bold", str(FONTS_DIR / "LiberationSans-Bold.ttf")))

# -----------------------------------------------------------------------------
# Pydantic schema mirrors Annex IV – update automatically during fetch.
# -----------------------------------------------------------------------------
app = typer.Typer(add_completion=False)

class AnnexIVSection(BaseModel):
    heading: str = Field(..., description="Canonical heading from Annex IV")
    body: str = Field(..., description="Verbatim text of the section")

class AnnexIVSchema(BaseModel):
    enterprise_size: Literal["sme", "mid", "large"]  # new — Art. 11 exemption
    risk_level: Literal["high", "limited", "minimal"]
    use_cases: List[str] = []  # list of tags from Annex III
    system_overview: str
    development_process: str
    system_monitoring: str
    performance_metrics: str
    risk_management: str
    changes_and_versions: str
    standards_applied: str
    compliance_declaration: str
    post_market_plan: str

    @staticmethod
    def allowed_use_cases() -> set:
        return {
            "biometric_id",
            "critical_infrastructure",
            "education_scoring",
            "employment_screening",
            "essential_services",
            "law_enforcement",
            "migration_control",
            "justice_decision"
        }

    @classmethod
    def validate_use_cases(cls, value):
        allowed = cls.allowed_use_cases()
        unknown = [v for v in value if v not in allowed]
        if unknown:
            raise ValueError(f"Unknown use_case(s): {', '.join(unknown)}. Allowed: {', '.join(sorted(allowed))}")
        return value

    from pydantic import field_validator
    @field_validator('use_cases')
    def check_use_cases(cls, value):
        return cls.validate_use_cases(value)

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def slugify(text):
    # Used only for Annex III parser
    return text.lower().replace(" ", "_").replace("-", "_").replace("–", "_").replace("—", "_").replace("/", "_").replace(".", "").replace(",", "").replace(":", "").replace(";", "").replace("'", "").replace('"', "").strip()

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
    # Dump YAML with an empty line before each key (except the first)
    with path.open("w", encoding="utf-8") as f:
        first = True
        for key in _SECTION_KEYS:
            if key in data:
                if not first:
                    f.write("\n")
                yaml.dump({key: data[key]}, f, allow_unicode=True, default_flow_style=False)
                first = False
        # Always write enterprise_size, risk_level, use_cases, _schema_version
        yaml.dump({"enterprise_size": data.get("enterprise_size", "mid")}, f, allow_unicode=True, default_flow_style=False)
        yaml.dump({"risk_level": data.get("risk_level", "")}, f, allow_unicode=True, default_flow_style=False)
        yaml.dump({"use_cases": data.get("use_cases", [])}, f, allow_unicode=True, default_flow_style=False)
        yaml.dump({"_schema_version": data.get("_schema_version", "")}, f, allow_unicode=True, default_flow_style=False)


def _split_to_list_items(text: str):
    import re
    # Search for subpoints (a)...(h) with any content up to the next subpoint or end of text
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
        # Add letterSpacing (tracking) via wordSpace, since reportlab does not support letterSpacing directly
        wordSpace=0.5,  # 0.5 pt letter-spacing (emulated)
        # small-caps is not supported directly, but can be added via font or manually if needed
    )
    return style

def _header(canvas, doc):
    import datetime
    canvas.saveState()
    canvas.setFont("LiberationSans", 8)
    schema = getattr(doc, "_schema_version", None)
    if not schema:
        # Try to get from payload
        try:
            schema = doc._payload.get("_schema_version", "unknown")
        except Exception:
            schema = "unknown"
    canvas.drawRightString(A4[0]-25*mm, A4[1]-15*mm,
        f"Annex IV v{schema} – generated {datetime.date.today()}")
    canvas.restoreState()

def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("LiberationSans", 9)
    # Center of the bottom margin — page number
    page_num = canvas.getPageNumber()
    canvas.drawCentredString(A4[0]/2, 15*mm, str(page_num))
    canvas.restoreState()

def _header_and_footer(canvas, doc):
    _header(canvas, doc)
    _footer(canvas, doc)

def _render_pdf(payload: dict, out_pdf: Path):
    doc = SimpleDocTemplate(str(out_pdf), pagesize=A4,
                            leftMargin=25*mm, rightMargin=25*mm,
                            topMargin=20*mm, bottomMargin=20*mm)  # top/bottom margins 20 mm
    doc._schema_version = payload.get("_schema_version", "unknown")
    doc._payload = payload
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

def _validate_payload(payload, sarif_path=None, yaml_path=None):
    """
    Offline validation via pure Python rule engine.
    """
    denies, warns = validate_payload(payload)
    violations = list(denies)
    warnings = list(warns)

    if sarif_path and violations:
        _write_sarif(violations, sarif_path, yaml_path)

    # Print warnings for limited/minimal risk systems
    for w in warnings:
        typer.secho(f"[WARNING] {w['rule']}: {w['msg']}", fg=typer.colors.YELLOW)

    return violations

# SARIF: template for passing region (line/col)
def _write_sarif(violations, sarif_path, yaml_path):
    # Use ruamel.yaml AST for precise coordinates
    key_lines = {}
    try:
        from ruamel.yaml import YAML
        yaml_ruamel = YAML(typ="rt")
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml_ruamel.load(f)
        def find_key_coords(node, target):
            if hasattr(node, 'lc') and hasattr(node, 'fa'):
                for k in node:
                    if k == target:
                        ln = node.lc.key(k)[0] + 1
                        col = node.lc.key(k)[1] + 1
                        return (ln, col)
                    v = node[k]
                    if isinstance(v, dict):
                        res = find_key_coords(v, target)
                        if res:
                            return res
            return None
        for v in violations:
            key = v.get("rule", "").replace("_required", "")
            coords = find_key_coords(data, key)
            if coords:
                key_lines[v["rule"]] = coords
    except Exception:
        pass
    sarif = {
        "version": "2.1.0",
        "$schema": "https://schemastore.azurewebsites.net/schemas/json/sarif-2.1.0-rtm.5.json",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "annex4ac/opa",
                        "informationUri": "https://openpolicyagent.org/"
                    }
                },
                "results": [
                    {
                        "level": "error",
                        "ruleId": v["rule"],
                        "message": {"text": v["msg"]},
                        "locations": [
                            {
                                "physicalLocation": {
                                    "artifactLocation": {"uri": yaml_path or "annex.yaml"},
                                    "region": {"startLine": key_lines.get(v["rule"], (1,1))[0], "startColumn": key_lines.get(v["rule"], (1,1))[1]}
                                }
                            }
                        ]
                    } for v in violations
                ]
            }
        ]
    }
    with open(sarif_path, "w", encoding="utf-8") as f:
        json.dump(sarif, f, ensure_ascii=False, indent=2)

# JWT license check (Pro)
def _check_license():
    import os, time, typer
    import importlib.resources as pkgres
    import jwt
    try:
        pub_key = pkgres.read_text("annex4ac", "lic_pub.pem")
        claims = jwt.decode(os.getenv("ANNEX4AC_LICENSE"), pub_key, algorithms=["RS256"])
        assert claims["exp"] > time.time()
    except Exception:
        typer.secho("Invalid licence.", fg=typer.colors.RED)
        raise typer.Exit(1)

def fetch_annex3_tags(cache_path="high_risk_tags.json", cache_days=14):
    """
    Parses Annex III, caches the result in high_risk_tags.json (next to annex4ac.py),
    returns a set of tags.
    """
    cache_file = os.path.join(os.path.dirname(__file__), cache_path)
    if os.path.exists(cache_file):
        mtime = datetime.fromtimestamp(os.path.getmtime(cache_file))
        if datetime.now() - mtime < timedelta(days=cache_days):
            with open(cache_file, "r", encoding="utf-8") as f:
                return set(json.load(f))
    html = _fetch_html("https://artificialintelligenceact.eu/annex/3/")
    soup = BeautifulSoup(html, "html.parser")
    tags = {slugify(li.text) for li in soup.select("ol > li")}
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(sorted(tags), f, ensure_ascii=False, indent=2)
    return tags

def update_high_risk_tags_json():
    fetch_annex3_tags()

# -----------------------------------------------------------------------------
# CLI Commands
# -----------------------------------------------------------------------------

@app.command()
def fetch_schema(output: Path = typer.Argument(Path("annex_schema.yaml"), exists=False), offline: bool = typer.Option(False, help="Use offline cache if available")):
    """Download the latest Annex IV text and convert to YAML scaffold."""
    import datetime
    import requests
    from pathlib import Path as SysPath
    from shutil import copyfile
    import yaml as pyyaml
    cache_dir = os.path.expanduser("~/.cache/annex4ac/")
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, "schema-latest.yaml")
    try:
        if offline:
            if os.path.exists(cache_path):
                typer.secho("Using offline cache.", fg=typer.colors.YELLOW)
                copyfile(cache_path, output)
                return
            else:
                typer.secho("No offline cache found.", fg=typer.colors.RED)
                raise typer.Exit(1)
        html = _fetch_html(AI_ACT_ANNEX_IV_HTML)
        r = requests.get(AI_ACT_ANNEX_IV_HTML, timeout=20)
        schema_date = r.headers.get("Last-Modified")
        if schema_date:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(schema_date)
            schema_version = dt.strftime("%Y%m%d")
        else:
            schema_version = datetime.date.today().strftime("%Y%m%d")
        data = _parse_annex_iv(html)
        data["_schema_version"] = schema_version
        _write_yaml(data, output)
        # Save to cache
        with open(output, "r", encoding="utf-8") as src, open(cache_path, "w", encoding="utf-8") as dst:
            dst.write(src.read())
        typer.secho(f"Schema written to {output}", fg=typer.colors.GREEN)
    except Exception as e:
        # If network error — try cache
        if os.path.exists(cache_path):
            typer.secho(f"Network error, using offline cache: {e}", fg=typer.colors.YELLOW)
            copyfile(cache_path, output)
        else:
            typer.secho(f"Download error and no cache: {e}.", fg=typer.colors.RED)
            raise typer.Exit(1)

@app.command()
def validate(input: Path = typer.Option(..., exists=True, help="Your filled Annex IV YAML"), sarif: Path = typer.Option(None, help="Write SARIF report to this file")):
    """Validate user YAML against required Annex IV keys; exit 1 on error."""
    try:
        from ruamel.yaml import YAML
        yaml_ruamel = YAML(typ="rt")
        with input.open("r", encoding="utf-8") as f:
            payload = yaml_ruamel.load(f)
        violations = _validate_payload(payload, sarif_path=sarif, yaml_path=str(input))
        if violations:
            for v in violations:
                typer.secho(f"[VALIDATION] {v['rule']}: {v['msg']}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
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
    # License check for Pro
    if os.getenv("ANNEX4AC_LICENSE"):
        _check_license()
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
