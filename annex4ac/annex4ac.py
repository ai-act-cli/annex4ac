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
$ annex4ac fetch-schema  > annex_schema.yaml             # refresh local schema
$ annex4ac validate my_model.yaml                        # CI gate (free)
$ annex4ac generate my_model.yaml --output my_annex.pdf  # Pro only

The code is intentionally compact; production users should add logging, retries and
exception handling as required.
"""

import os
import sys
import json
import tempfile
import re
from pathlib import Path
from typing import Dict, Literal, List, Optional
from enum import Enum
from datetime import datetime, timedelta, date
from dateutil.relativedelta import relativedelta
from dateutil.parser import parse as parse_dt

import requests
from bs4 import BeautifulSoup
import yaml
import typer
from pydantic import BaseModel, ValidationError, Field, field_validator
from importlib.resources import files
from jinja2 import Template
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, ListFlowable, ListItem, KeepTogether
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from .policy.annex4ac_validate import validate_payload
import unicodedata
from .docx_generator import render_docx
from ftfy import fix_text
from markupsafe import escape, Markup
from platformdirs import user_cache_dir
from .constants import DOC_CTRL_FIELDS, SECTION_MAPPING, SCHEMA_VERSION, AI_ACT_ANNEX_IV_HTML, AI_ACT_ANNEX_IV_PDF
from .config import Settings
from .db import (
    get_session,
    load_annex_iv_from_db,
    get_schema_version_from_db,
    get_expected_top_counts,
)
from .tags import fetch_annex3_tags


class SourcePref(str, Enum):
    db_only = "db_only"
    web_only = "web_only"
    db_then_web = "db_then_web"


def _parse_iso_date(val):
    """Parse ISO date string or datetime object to datetime."""
    if isinstance(val, datetime):
        return val
    if isinstance(val, date):
        return datetime.combine(val, datetime.min.time())
    if isinstance(val, str) and val.strip():
        # allow without "T"
        return parse_dt(val if 'T' in val else val + "T00:00:00")
    return datetime.now()

def _build_doc_meta(payload: dict) -> dict:
    """Build unified document metadata for all formats."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    meta = {}
    for label, key in DOC_CTRL_FIELDS:
        if key == "generation_date":
            meta[key] = now
        elif key == "_schema_version":
            sv = payload.get(key, SCHEMA_VERSION)
            if isinstance(sv, str) and len(sv) == 8 and sv.isdigit():
                sv = f"{sv[:4]}-{sv[4:6]}-{sv[6:8]}"
            meta[key] = sv
        elif key == "retention_until":
            # Calculate retention period according to Art. 18(1): 10 years after placing on market/put into service
            placed_on_market = payload.get("placed_on_market") or payload.get("put_into_service")
            if placed_on_market:
                pom = _parse_iso_date(placed_on_market)
                retention = (pom + relativedelta(years=10)).date().isoformat()
                meta[key] = retention
            else:
                meta[key] = "—"
        else:
            # Show "—" for empty values
            value = payload.get(key, "")
            meta[key] = value if value else "—"
    return meta

# Attempt to import pikepdf for PDF/A support
try:
    import pikepdf
    PIKEPDF_AVAILABLE = True
except ImportError:
    PIKEPDF_AVAILABLE = False

# Regular expressions for parsing lists
"""Regular expressions for parsing list structures in Annex IV text."""
BULLET_RE = re.compile(r'^\s*(?:[\u2022\u25CF\u25AA\u00B7\u2013\u2014\-\*])\s+')
# allow any letter a-z; roman numerals filtered separately via ROMAN_RE
SUBPOINT_RE = re.compile(r'^\s*\(([a-z])\)\s+', re.I)
TOP_BULLET_RE = re.compile(r'^\s{0,3}(?:[-*\u2022\u25AA\u00B7\u2013\u2014]|\d+[\.)])\s+')
ROMAN_RE = re.compile(r'^\s*\(([ivxlcdm]+)\)\s+', re.I)


def _normalize_lines(text: str) -> list[str]:
    if not text:
        return []
    text = fix_text(text)
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    return [ln.rstrip() for ln in text.splitlines()]


def _count_subpoints_db(db_text: str) -> tuple[int, int]:
    """Return (N_top, N_sub_of_first) for canonical Annex IV text from DB."""
    lines = _normalize_lines(db_text)
    letters_idx = [
        i
        for i, ln in enumerate(lines)
        if SUBPOINT_RE.match(ln) and not ROMAN_RE.match(ln)
    ]
    if letters_idx:
        n_top = len(letters_idx)
        start = letters_idx[0]
        end = letters_idx[1] if len(letters_idx) > 1 else len(lines)
        block = lines[start + 1 : end]
    else:
        candidates = [(i, len(re.match(r"^\s*", ln).group(0)))
                      for i, ln in enumerate(lines) if TOP_BULLET_RE.match(ln)]
        if candidates:
            min_indent = min(ind for _, ind in candidates)
            top_idxs = [i for i, ind in candidates if ind == min_indent]
            n_top = len(top_idxs)
        else:
            n_top = 0
        if n_top:
            first_idx = top_idxs[0]
            indent_top = min_indent
            j = first_idx + 1
            block = []
            while j < len(lines):
                ln = lines[j]
                if ln.strip() == "":
                    j += 1
                    continue
                indent = len(re.match(r"^\s*", ln).group(0))
                if TOP_BULLET_RE.match(ln) and indent <= indent_top:
                    break
                block.append(ln)
                j += 1
        else:
            return (0, 0)
    n_sub = 0
    for ln in block:
        if TOP_BULLET_RE.match(ln) or ROMAN_RE.match(ln) or SUBPOINT_RE.match(ln):
            n_sub += 1
    return (n_top, n_sub)


def _count_subpoints_user(user_text: str) -> tuple[int, int]:
    """Return (N_top, N_sub_of_first) for user-provided section text."""
    lines = _normalize_lines(user_text)
    letters = [
        i
        for i, ln in enumerate(lines)
        if SUBPOINT_RE.match(ln) and not ROMAN_RE.match(ln)
    ]
    if letters:
        n_top = len(letters)
        start = letters[0]
        end = letters[1] if len(letters) > 1 else len(lines)
        block = lines[start + 1 : end]
    else:
        candidates = [(i, len(re.match(r"^\s*", ln).group(0)))
                      for i, ln in enumerate(lines) if TOP_BULLET_RE.match(ln)]
        if candidates:
            min_indent = min(ind for _, ind in candidates)
            top_idx = [i for i, ind in candidates if ind == min_indent]
            n_top = len(top_idx)
        else:
            return (0, 0)
        start = top_idx[0]
        indent_top = min_indent
        j = start + 1
        block = []
        while j < len(lines):
            ln = lines[j]
            if ln.strip() == "":
                j += 1
                continue
            indent = len(re.match(r"^\s*", ln).group(0))
            if TOP_BULLET_RE.match(ln) and indent <= indent_top:
                break
            block.append(ln)
            j += 1
    n_sub = 0
    for ln in block:
        if TOP_BULLET_RE.match(ln) or ROMAN_RE.match(ln) or SUBPOINT_RE.match(ln):
            n_sub += 1
    return (n_top, n_sub)

def listify(text: str) -> Markup:
    """
    Creates HIERARCHICAL structure:
      (a) Item heading
          - subitem
          - subitem
      (b) ...
    One <ol class="alpha"> per group; each <li> can contain <ul>.
    Also processes regular bulleted lists.
    """
    if not text:
        return Markup("")
    text = fix_text(text)

    out: list[str] = []
    lines = text.splitlines()

    # Current top-level "block"
    ol_items: list[dict] = []   # [{'head': str, 'bullets': [str,...]}]
    current: Optional[dict] = None

    def punctuate(arr: list[str]) -> list[str]:
        arr = [s.rstrip(" ;.") for s in arr]
        result = []
        for i, s in enumerate(arr):
            # Don't add period/semicolon if string ends with colon
            if s.endswith(':'):
                result.append(s)
            else:
                result.append(s + ("." if i == len(arr)-1 else ";"))
        return result

    def flush_ol():
        nonlocal ol_items, current
        if current:
            ol_items.append(current)
            current = None
        if not ol_items:
            return
        # render one common <ol>
        li_html = []
        for item in ol_items:
            head = escape(item['head'])
            if item['bullets']:
                bullets = ''.join(f"<li>{escape(b)}</li>" for b in punctuate(item['bullets']))
                li_html.append(f"<li>{head}<ul>{bullets}</ul></li>")
            else:
                li_html.append(f"<li>{head}</li>")
        out.append(f"<ol class=\"alpha\">{''.join(li_html)}</ol>")
        ol_items = []

    def flush_ul(ul_items: list[str]):
        if not ul_items:
            return
        bullets = ''.join(f"<li>{escape(b)}</li>" for b in punctuate(ul_items))
        out.append(f"<ul>{bullets}</ul>")

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue

        m_alpha = SUBPOINT_RE.match(line)
        m_bullet = BULLET_RE.match(line)

        if m_alpha:
            # New item (a)/(b)...
            cleaned = SUBPOINT_RE.sub('', line, 1).strip()
            # Close previous item and add to array
            if current:
                ol_items.append(current)
            current = {'head': cleaned, 'bullets': []}
            i += 1
            continue

        if m_bullet and current:
            # Bullet inside (a)/(b) structure
            cleaned = BULLET_RE.sub('', line, 1).strip()
            current['bullets'].append(cleaned)
            i += 1
            continue

        if m_bullet and not current:
            # Regular bulleted list without (a)/(b)...
            flush_ol()
            ul_items = [BULLET_RE.sub('', line, 1).strip()]
            i += 1
            # Collect all subsequent bullets
            while i < len(lines):
                next_line = lines[i].strip()
                if not next_line:
                    break
                if BULLET_RE.match(next_line):
                    ul_items.append(BULLET_RE.sub('', next_line, 1).strip())
                    i += 1
                else:
                    break
            flush_ul(ul_items)
            continue

        # Regular text — need to close current ol-block (if any)
        flush_ol()
        out.append(f"<p>{escape(line)}</p>")
        i += 1

    # Final flush
    flush_ol()

    return Markup('\n'.join(out))



# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------
# Register Liberation Sans (expects LiberationSans-Regular.ttf and LiberationSans-Bold.ttf to be available)
try:
    regular_font_path = files("annex4ac").joinpath("fonts/LiberationSans-Regular.ttf")
    bold_font_path = files("annex4ac").joinpath("fonts/LiberationSans-Bold.ttf")
    pdfmetrics.registerFont(TTFont("LiberationSans", str(regular_font_path)))
    pdfmetrics.registerFont(TTFont("LiberationSans-Bold", str(bold_font_path)))
except Exception:
    # Fallback to direct file access
    FONTS_DIR = Path(__file__).parent / "fonts"
    pdfmetrics.registerFont(TTFont("LiberationSans", str(FONTS_DIR / "LiberationSans-Regular.ttf")))
    pdfmetrics.registerFont(TTFont("LiberationSans-Bold", str(FONTS_DIR / "LiberationSans-Bold.ttf")))

# -----------------------------------------------------------------------------
# Pydantic schema mirrors Annex IV – update automatically during fetch.
# -----------------------------------------------------------------------------
app = typer.Typer(
    add_completion=False,
    help="Generate and validate EU AI Act Annex IV technical documentation. \n\n ⚠️  LEGAL DISCLAIMER: This software is provided for informational and compliance assistance purposes only. It is not legal advice and should not be relied upon as such. Users are responsible for ensuring their documentation meets all applicable legal requirements and should consult with qualified legal professionals for compliance matters. The authors disclaim any liability for damages arising from the use of this software.\n\n🔒 DATA PROTECTION: All processing occurs locally on your machine. No data leaves your system."
)

class AnnexIVSection(BaseModel):
    heading: str = Field(..., description="Canonical heading from Annex IV")
    body: str = Field(..., description="Verbatim text of the section")

class AnnexIVSchema(BaseModel):
    enterprise_size: Literal["sme", "mid", "large"]  # Art. 11 exemption - all sizes get full 9 sections
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
    placed_on_market: datetime
    last_updated: datetime

    @field_validator('last_updated')
    def _fresh_enough(cls, v, info):
        pom = info.data.get('placed_on_market')
        if pom and v < pom:
            raise ValueError("last_updated cannot be before placed_on_market")
        return v

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

    # Use the global SECTION_MAPPING for correct mapping
    section_keys = [key for _, key in SECTION_MAPPING]

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
                raise ValueError("Annex IV structure on the website has changed: more sections than expected! Please update SECTION_MAPPING and the parser.")
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
        for _, key in SECTION_MAPPING:
            if key in data:
                if not first:
                    f.write("\n")
                yaml.dump({key: data[key]}, f, allow_unicode=True, default_flow_style=False)
                first = False
        
        # Add empty line before enterprise_size
        f.write("\n")
        
        # Always write all mandatory fields with proper defaults and examples
        f.write("\n# enterprise_size: sme | mid | large (Art. 11 exemption)\n")
        yaml.dump({"enterprise_size": data.get("enterprise_size", "")}, f, allow_unicode=True, default_flow_style=False)
        
        # Get full list of use_cases from Annex III
        try:
            full_use_cases = fetch_annex3_tags()
            use_cases_list = sorted(list(full_use_cases))
            if use_cases_list:
                f.write(f"\n# use_cases: list of tags (e.g., ['biometric_id', 'critical_infrastructure'])\n")
                f.write(f"# Use cases that make AI system high-risk (from Annex III):\n")
                f.write(f"#   {', '.join(use_cases_list)}\n")
            else:
                # Fallback to known tags from the schema
                known_tags = ["biometric_id", "critical_infrastructure", "education_scoring", "employment_screening", "essential_services", "law_enforcement", "migration_control", "justice_decision"]
                f.write(f"\n# use_cases: list of tags (e.g., ['biometric_id', 'critical_infrastructure'])\n")
                f.write(f"# Use cases that make AI system high-risk (from Annex III):\n")
                f.write(f"#   {', '.join(known_tags[:4])},\n")
                f.write(f"#   {', '.join(known_tags[4:])}\n")
        except Exception:
            # Fallback to known tags from the schema
            known_tags = ["biometric_id", "critical_infrastructure", "education_scoring", "employment_screening", "essential_services", "law_enforcement", "migration_control", "justice_decision"]
            f.write(f"\n# use_cases: list of tags (e.g., ['biometric_id', 'critical_infrastructure'])\n")
            f.write(f"# Use cases that make AI system high-risk (from Annex III):\n")
            f.write(f"#   {', '.join(known_tags[:4])},\n")
            f.write(f"#   {', '.join(known_tags[4:])}\n")
        yaml.dump({"use_cases": data.get("use_cases", [])}, f, allow_unicode=True, default_flow_style=False)
        
        f.write("\n# risk_level: high | limited | minimal (Art. 6 / Annex III) - AI system risk classification\n")
        yaml.dump({"risk_level": data.get("risk_level", "")}, f, allow_unicode=True, default_flow_style=False)
        
        f.write("\n# placed_on_market: ISO datetime (e.g., 2024-01-15T10:30:00) - when AI system was first placed on market\n")
        yaml.dump({"placed_on_market": data.get("placed_on_market", "")}, f, allow_unicode=True, default_flow_style=False)
        
        f.write("\n# last_updated: this document last updated (ISO datetime, e.g., 2024-07-28T14:20:00)\n")
        yaml.dump({"last_updated": data.get("last_updated", "")}, f, allow_unicode=True, default_flow_style=False)
        
        f.write("\n# _schema_version: automatically set\n")
        yaml.dump({"_schema_version": data.get("_schema_version", SCHEMA_VERSION)}, f, allow_unicode=True, default_flow_style=False)


def _punctuate(items: list[str]) -> list[str]:
    """Adds ';' after each list item and '.' after the last one, but not after colon."""
    if not items:
        return items
    out = []
    for i, t in enumerate(items):
        t = t.rstrip(" ;.")
        # Don't add period/semicolon if string ends with colon
        if t.endswith(':'):
            out.append(t)
        else:
            out.append(t + ("." if i == len(items)-1 else ";"))
    return out

def _make_ul(items):
    items = _punctuate(items)
    return ListFlowable(
        [Paragraph(t, _get_body_style()) for t in items],
        bulletType='bullet',
        leftIndent=18,
        bulletIndent=0,
    )

def _make_ol(items, start=1):
    """Alphabetical list ((a),(b)…). Pass value=…, otherwise ReportLab repeats (a)."""
    items = _punctuate(items)
    flow_items = [
        ListItem(Paragraph(t, _get_body_style()), value=i)
        for i, t in enumerate(items, start)
    ]
    return ListFlowable(
        flow_items,
        bulletType='a',
        bulletFormat='(%s)',
        leftIndent=18,
        bulletIndent=0,
        start=start,
    )

def _text_to_flowables(text: str):
    """
    Splits block into Paragraph / ListFlowable using the same regex as DOCX.
    Supports simple UL and OL lists (a)(b)(c).
    """
    if not text:
        return [Paragraph('—', _get_body_style())]

    lines = text.splitlines()
    flows, mode, buf = [], None, []
    alpha_cursor = 1

    def flush():
        nonlocal mode, buf, alpha_cursor
        if not buf:
            return
        if mode == 'ol':
            flows.append(_make_ol(buf, start=alpha_cursor))
            alpha_cursor += len(buf)
        elif mode == 'ul':
            flows.append(_make_ul(buf))
        mode, buf = None, []

    for raw in lines:
        line = raw.strip()
        if not line:
            # empty line ends current list
            flush()
            continue
        if SUBPOINT_RE.match(line):
            cleaned = SUBPOINT_RE.sub('', line, 1).strip()
            if mode != 'ol':
                flush(); mode = 'ol'
            buf.append(cleaned)
        elif BULLET_RE.match(line):
            cleaned = BULLET_RE.sub('', line, 1).strip()
            if mode != 'ul':
                flush(); mode = 'ul'
            buf.append(cleaned)
        else:
            flush()
            flows.append(Paragraph(line, _get_body_style()))
    flush()
    return [KeepTogether(f) for f in flows]


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

def _doc_control_pdf(meta: dict):
    """Returns list of Flowable for PDF 'Document control' block."""
    flows = [Paragraph("Document control", _get_heading_style())]
    for label, key in DOC_CTRL_FIELDS:
        val = meta.get(key, "—")
        flows.append(Paragraph(f"<b>{label}:</b> {val}", _get_body_style()))
    flows.append(Spacer(1, 12))
    return flows

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
        f"Annex IV — Technical documentation referred to in Article 11(1)")
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

def _render_pdf(payload: dict, out_pdf: Path, meta: dict):
    doc = SimpleDocTemplate(str(out_pdf), pagesize=A4,
                            leftMargin=25*mm, rightMargin=25*mm,
                            topMargin=20*mm, bottomMargin=20*mm)  # top/bottom margins 20 mm
    doc._schema_version = payload.get("_schema_version", "unknown")
    doc._payload = payload
    story = []
    # Insert metadata block
    story.extend(_doc_control_pdf(meta))
    
    # Generate all 9 sections for all enterprise sizes (SME, MID, LARGE)
    for title, key in SECTION_MAPPING:
        story.append(Paragraph(title, _get_heading_style()))
        body = payload.get(key, "—")
        # Fix text encoding issues
        body = fix_text(body)
        # Unescape \n and normalize line breaks
        body = body.replace('\\r\\n', '\n').replace('\\r', '\n').replace('\\n', '\n')
        # Restore logical line breaks for YAML flow scalars
        body = re.sub(r'\s+(?=(?:[-•*]\s))', '\n', body)
        body = re.sub(r'\s+(?=\([a-z]\)\s+)', '\n', body, flags=re.I)
        # Fix double line breaks before list markers
        body = re.sub(r'\n\s*\n\s*([-•*])', r'\n\1', body)
        # Split into paragraphs and process each separately
        paragraphs = re.split(r'\n{2,}', body)
        for para in paragraphs:
            if para.strip():
                for fl in _text_to_flowables(para.strip()):
                    story.append(fl)
        story.append(Spacer(1, 12))
    doc.build(story, onFirstPage=_header_and_footer, onLaterPages=_header_and_footer)

def _embed_output_intent(pdf, icc_bytes):
    """Embeds OutputIntent with ICC profile for PDF/A-2."""
    import pikepdf
    from pikepdf import Name
    
    # Create ICC profile as stream
    icc = pdf.make_stream(icc_bytes)
    icc[Name("/N")] = 3  # RGB
    icc[Name("/Alternate")] = Name("/DeviceRGB")
    typer.secho(f"  Created ICC stream: {len(icc_bytes)} bytes", fg=typer.colors.BLUE)

    # Create OutputIntent as dictionary
    oi = pikepdf.Dictionary()
    oi[Name("/Type")] = Name("/OutputIntent")
    oi[Name("/S")] = Name("/GTS_PDFA2")  # PDF/A-2 standard (can also use /GTS_PDFA1)
    oi[Name("/OutputConditionIdentifier")] = "sRGB IEC61966-2.1"
    oi[Name("/Info")] = "sRGB IEC61966-2.1 ICC profile"
    oi[Name("/DestOutputProfile")] = icc
    
    # Add OutputIntent to document root
    pdf.Root[Name("/OutputIntents")] = [oi]
    typer.secho(f"  OutputIntent added to document root", fg=typer.colors.BLUE)

def _to_pdfa(path: Path):
    """Converts PDF to archival PDF/A-2b format."""
    if not PIKEPDF_AVAILABLE:
        typer.secho("pikepdf not installed, skipping PDF/A conversion", fg=typer.colors.YELLOW)
        return
    
    typer.secho("Converting to PDF/A-2b...", fg=typer.colors.BLUE)
    
    try:
        icc_bytes = files("annex4ac").joinpath("resources/sRGB.icc").read_bytes()
        typer.secho(f"  Loaded ICC profile: {len(icc_bytes)} bytes", fg=typer.colors.BLUE)
    except Exception:
        # Fallback to direct file access
        icc_path = Path(__file__).parent / "resources" / "sRGB.icc"
        if not icc_path.exists():
            typer.secho(f"  ICC profile not found: {icc_path}", fg=typer.colors.RED)
            return
        icc_bytes = icc_path.read_bytes()
        typer.secho(f"  Loaded ICC profile: {len(icc_bytes)} bytes", fg=typer.colors.BLUE)

    try:
        with pikepdf.open(str(path), allow_overwriting_input=True) as pdf:
            typer.secho(f"  Opened PDF: {len(pdf.pages)} pages", fg=typer.colors.BLUE)

            # Add XMP metadata for PDF/A
            with pdf.open_metadata() as meta:
                meta['pdfaid:part'] = "2"
                meta['pdfaid:conformance'] = "B"
                meta['dc:title'] = 'Annex IV Technical Documentation'
                meta['dc:subject'] = 'EU AI Act Compliance'
                meta['dc:creator'] = ['Annex4AC']
            typer.secho(f"  Added XMP metadata: pdfaid:part=2, pdfaid:conformance=B", fg=typer.colors.BLUE)

            # Add basic document info
            pdf.docinfo['/Title'] = 'Annex IV Technical Documentation'
            pdf.docinfo['/Subject'] = 'EU AI Act Compliance'
            pdf.docinfo['/Creator'] = 'Annex4AC'
            typer.secho(f"  Added document info: Title, Subject, Creator", fg=typer.colors.BLUE)

            # Embed OutputIntent with ICC profile
            _embed_output_intent(pdf, icc_bytes)

            # Save with PDF/A-2b compliance using new pikepdf 9+ approach
            typer.secho(f"  Saving with PDF/A-2b compliance...", fg=typer.colors.BLUE)
            pdf.save(
                str(path),
                preserve_pdfa=True,  # don't break PDF/A compliance
                fix_metadata_version=True,  # fix PDFVersion in XMP if present
                deterministic_id=True,  # reproducible /ID for same input
            )

        # Check result
        file_size = path.stat().st_size
        typer.secho(f"  File size after conversion: {file_size:,} bytes", fg=typer.colors.BLUE)

        typer.secho(f"PDF/A-2b conversion completed: {path}", fg=typer.colors.GREEN)

    except Exception as e:
        typer.secho(f"PDF/A conversion failed: {e}", fg=typer.colors.RED)
        import traceback
        typer.secho(f"Error details: {traceback.format_exc()}", fg=typer.colors.RED)

def _default_tpl() -> str:
    try:
        return files("annex4ac").joinpath("templates/template.html").read_text(encoding="utf-8")
    except Exception:
        # Fallback to direct file access
        return Path(__file__).parent.joinpath("templates", "template.html").read_text(encoding="utf-8")

def _render_html(data: dict, meta: dict) -> str:
    """Render HTML from template with data."""
    from datetime import datetime
    from jinja2 import Environment, select_autoescape

    # normalize strings
    norm = {}
    for k, v in data.items():
        if isinstance(v, str):
            # Fix text encoding issues
            v = fix_text(v)
            # Unescape \n and normalize line breaks
            v = v.replace('\\r\\n', '\n').replace('\\r', '\n').replace('\\n', '\n')
            # Restore logical line breaks for YAML flow scalars
            v = re.sub(r'\s+(?=(?:[-•*]\s))', '\n', v)
            v = re.sub(r'\s+(?=\([a-z]\)\s+)', '\n', v, flags=re.I)
            norm[k] = v
        else:
            norm[k] = v
    # Use passed metadata
    meta_lines = [f"<p><strong>{label}:</strong> {meta[key]}</p>" for label, key in DOC_CTRL_FIELDS]
    norm['__doc_control_html'] = '<section id="doc-control"><h2>Document control</h2>' + "\n".join(meta_lines) + "</section>"
    
    env = Environment(autoescape=select_autoescape(['html', 'xml']))
    env.filters['listify'] = listify  # add filter
    
    template = env.from_string(_default_tpl())
    html = template.render(**norm)
    
    # Insert block after the title but before the first section
    title_end = html.find('</h1>')
    if title_end != -1:
        insert_pos = title_end + 6  # after </h1>
        # Remove duplicate metadata from template
        # Find and remove lines with "Generated:", "Schema Version:", "Retention Until:"
        lines = html.split('\n')
        filtered_lines = []
        skip_next = False
        for line in lines:
            if any(skip in line for skip in ['<p><strong>Generated:</strong>', '<p><strong>Schema Version:</strong>', '<p><strong>Retention Until:</strong>']):
                skip_next = True
                continue
            if skip_next and line.strip() == '':
                skip_next = False
                continue
            if skip_next:
                skip_next = False
                continue
            filtered_lines.append(line)
        
        html = '\n'.join(filtered_lines)
        title_end = html.find('</h1>')
        if title_end != -1:
            insert_pos = title_end + 6
            return html[:insert_pos] + norm['__doc_control_html'] + html[insert_pos:]
    return norm['__doc_control_html'] + html

def _check_freshness(dt, max_days=None, strict=False):
    """Heuristic staleness check. max_days=None/<=0 disables it."""
    if not max_days or max_days <= 0:
        return
    if datetime.now() - dt > timedelta(days=max_days):
        msg = f"Technical doc is older than {max_days} days — consider updating (Art. 11 'techdoc must be kept up-to-date')."
        if strict:
            typer.secho(f"[ERROR] {msg}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        else:
            typer.secho(f"[WARNING] {msg}", fg=typer.colors.YELLOW)

def _validate_payload(payload):
    """Offline validation via pure Python rule engine.

    Returns ``(violations, warnings)`` so callers can append extra rules to
    ``violations`` before writing SARIF once. Warnings are printed but also
    returned for callers that need them.
    """
    denies, warns = validate_payload(payload)
    violations = list(denies)
    warnings = list(warns)

    # Print warnings for limited/minimal risk systems
    for w in warnings:
        typer.secho(f"[WARNING] {w['rule']}: {w['msg']}", fg=typer.colors.YELLOW)

    return violations, warnings

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
    import os, time, typer, jwt

    token = os.getenv("ANNEX4AC_LICENSE")
    if not token:
        typer.secho("Licence env ANNEX4AC_LICENSE not set", fg=typer.colors.RED)
        raise typer.Exit(1)

    # 1) Extract kid from header
    header = jwt.get_unverified_header(token)
    kid = header.get("kid")

    # 2) Public key dictionary (ready for rotation)
    pub_map = {
        "2025-01": files("annex4ac").joinpath("lic_pub.pem").read_text()
    }

    key = pub_map.get(kid)
    if not key:
        typer.secho(f"No public key for kid={kid}", fg=typer.colors.RED)
        raise typer.Exit(1)

    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],  # Hardcode algorithm for security
            issuer="annex4ac.io",
            audience="annex4ac-cli",
            options={"require": ["exp", "iat", "iss", "aud"]},
        )
    except jwt.ExpiredSignatureError:
        typer.secho("License expired", fg=typer.colors.RED)
        raise typer.Exit(1)
    except jwt.PyJWTError as exc:
        typer.secho(f"License error: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1)

    # 3) Check expiration and plan
    if claims["exp"] < time.time():
        typer.secho("License expired", fg=typer.colors.RED)
        raise typer.Exit(1)

    plan = claims.get("plan")
    if plan != "pro":
        typer.secho(f"License plan '{plan}' insufficient for PDF generation", fg=typer.colors.RED)
        raise typer.Exit(1)

@app.command("update-annex3-cache")
def update_annex3_cache():
    """Force-update cached Annex III high-risk tags."""
    tags = fetch_annex3_tags(cache_days=0)
    typer.secho(
        f"Annex III tags updated: {len(tags)} entries",
        fg=typer.colors.GREEN,
    )

# -----------------------------------------------------------------------------
# CLI Commands
# -----------------------------------------------------------------------------

@app.command()
def fetch_schema(
    output: Path = typer.Argument(Path("annex_schema.yaml"), exists=False),
    offline: bool = typer.Option(False, help="Use offline cache if available"),
    db_url: str = typer.Option(None, help="SQLAlchemy DB URL (postgresql+psycopg://...)"),
    celex_id: str = typer.Option("32024R1689", help="CELEX id"),
    source_preference: Optional[SourcePref] = typer.Option(
        None, help="db_only|web_only|db_then_web"
    ),
):
    """Download the latest Annex IV text and convert to YAML scaffold."""
    import requests
    from shutil import copyfile

    settings = Settings()
    db_url = db_url or settings.db_url
    celex_id = celex_id or settings.celex_id
    source_preference = (source_preference.value if source_preference else settings.source_preference)

    cache_dir = user_cache_dir("annex4ac")
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

        try_db_first = (source_preference != "web_only") and bool(db_url)
        data = None
        schema_version = None
        source_used = "WEB"

        if try_db_first:
            try:
                with get_session(db_url) as ses:
                    data = load_annex_iv_from_db(ses, celex_id=celex_id)
                    schema_version = get_schema_version_from_db(ses, celex_id=celex_id)
                    source_used = f"DB (version {schema_version})" if schema_version else "DB"
            except Exception:
                typer.secho(
                    "[DB] fallback to web (connection failed or CELEX not found)",
                    fg=typer.colors.YELLOW,
                )

        if source_preference == "db_only" and not data:
            typer.secho(
                "DB not reachable or CELEX not found. Check ANNEX4AC_DB_URL, network, and permissions, or switch to --source-preference web_only.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(2)

        if not data:
            r = requests.get(AI_ACT_ANNEX_IV_HTML, timeout=20)
            r.raise_for_status()
            html = r.text
            data = _parse_annex_iv(html)
            source_used = "WEB"

        data["_schema_version"] = schema_version or SCHEMA_VERSION
        _write_yaml(data, output)
        with open(output, "r", encoding="utf-8") as src, open(cache_path, "w", encoding="utf-8") as dst:
            dst.write(src.read())
        typer.secho(f"Using source: {source_used}", fg=typer.colors.BLUE)
        typer.secho(f"Schema written to {output}", fg=typer.colors.GREEN)
    except Exception as e:
        if os.path.exists(cache_path):
            typer.secho(f"Network error, using offline cache: {e}", fg=typer.colors.YELLOW)
            copyfile(cache_path, output)
        else:
            typer.secho(f"Download error and no cache: {e}.", fg=typer.colors.RED)
            raise typer.Exit(1)

@app.command()
def validate(
    input: Path = typer.Argument(..., exists=True, help="Your filled Annex IV YAML"),
    sarif: Path = typer.Option(None, help="Write SARIF report to this file"),
    stale_after: int = typer.Option(0, help="Warn if last_updated older than N days (0=off)", show_default=False),
    strict_age: bool = typer.Option(False, help="Exit 1 if stale_after is exceeded"),
    use_db: bool = typer.Option(False, help="Cross-check sections against DB"),
    db_url: str = typer.Option(None, help="SQLAlchemy DB URL (postgresql+psycopg://...)"),
    celex_id: str = typer.Option("32024R1689", help="CELEX id"),
):
    """Validate user YAML against required Annex IV keys; exit 1 on error."""
    if stale_after == 0:
        stale_after = int(os.getenv("ANNEX4AC_STALE_AFTER", "0"))
    try:
        settings = Settings()
        db_url = db_url or settings.db_url
        celex_id = celex_id or settings.celex_id

        from ruamel.yaml import YAML
        yaml_ruamel = YAML(typ="rt")
        with input.open("r", encoding="utf-8") as f:
            payload = yaml_ruamel.load(f)

        violations, _warnings = _validate_payload(payload)

        if use_db and not db_url:
            typer.secho(
                "--use-db requires a database URL. Set ANNEX4AC_DB_URL or pass --db-url.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(2)

        if use_db and db_url:
            with get_session(db_url) as ses:
                db_schema = load_annex_iv_from_db(ses, celex_id=celex_id)
                exp_top_counts = get_expected_top_counts(ses, celex_id=celex_id)
            for _, key in SECTION_MAPPING:
                db_text = (db_schema.get(key) or "").strip()
                user_text = str(payload.get(key) or "").strip()
                if not db_text:
                    continue
                if not user_text:
                    violations.append({
                        "rule": f"{key}_required",
                        "msg": f"Annex IV requires content for '{key}' (per DB snapshot).",
                    })
                    continue
                exp_top = exp_top_counts.get(key, 0)
                exp_sub = _count_subpoints_db(db_text)[1]
                got_top, got_sub = _count_subpoints_user(user_text)
                if exp_top >= 2 and got_top < exp_top:
                    violations.append({
                        "rule": f"{key}_subpoints_insufficient",
                        "msg": f"{key}: expected ≥{exp_top} top-level subpoints, got {got_top}.",
                    })
                if exp_sub >= 2 and got_sub < exp_sub:
                    violations.append({
                        "rule": f"{key}_subsub_insufficient",
                        "msg": f"{key}: first subpoint expected ≥{exp_sub} nested items, got {got_sub}.",
                    })

        if sarif and violations:
            _write_sarif(violations, sarif, str(input))

        if violations:
            for v in violations:
                typer.secho(f"[VALIDATION] {v['rule']}: {v['msg']}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)

        model = AnnexIVSchema(**payload)
        _check_freshness(model.last_updated, max_days=stale_after, strict=strict_age)
    except (ValidationError, Exception) as exc:
        typer.secho("Validation failed:\n" + str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    typer.secho("Validation OK!", fg=typer.colors.GREEN)

@app.command()
def generate(
    input: Path = typer.Argument(..., help="YAML input file"),
    output: Path = typer.Option(None, help="Output file name"),
    fmt: str = typer.Option("pdf", help="pdf | html | docx"),
    pdfa: bool = typer.Option(False, help="Convert PDF to PDF/A-2b format for archival"),
    skip_validation: bool = typer.Option(False, help="Don't validate before rendering"),
):
    """Generate output from YAML: PDF (default), HTML, or DOCX."""
    payload = yaml.safe_load(input.read_text(encoding='utf-8'))

    if not skip_validation:
        violations, _warnings = _validate_payload(payload)
        if violations:
            for v in violations:
                typer.secho(f"[VALIDATION] {v['rule']}: {v['msg']}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        AnnexIVSchema(**payload)

    # Build unified metadata for all formats (includes retention calculation)
    meta = _build_doc_meta(payload)

    # Automatically determine output filename
    if output is None:
        output = input.with_suffix(f".{fmt}")

    # License check for Pro features (PDF requires license)
    if fmt == "pdf":
        _check_license()
        _render_pdf(payload, output, meta)
        if pdfa:
            _to_pdfa(output)
        typer.secho(f"PDF generated: {output}", fg=typer.colors.GREEN)
    elif fmt == "html":
        # HTML is free
        html_content = _render_html(payload, meta)
        output.write_text(html_content, encoding='utf-8')
        typer.secho(f"HTML generated: {output}", fg=typer.colors.GREEN)
    elif fmt == "docx":
        # DOCX is free
        render_docx(payload, output, meta)
        typer.secho(f"DOCX generated: {output}", fg=typer.colors.GREEN)
    else:
        raise ValueError(f"Unknown format: {fmt}")



if __name__ == "__main__":
    app()
