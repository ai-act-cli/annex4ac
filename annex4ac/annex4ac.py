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
import ftfy
from pathlib import Path
from typing import Dict, Literal, List
from datetime import datetime, timedelta, date
from dateutil.relativedelta import relativedelta
from dateutil.parser import parse as parse_dt

import requests
from bs4 import BeautifulSoup
import yaml
import typer
from pydantic import BaseModel, ValidationError, Field, field_validator
import importlib.resources as pkgres
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

import re
from ftfy import fix_text
from markupsafe import escape, Markup
from .constants import DOC_CTRL_FIELDS, SECTION_MAPPING, SCHEMA_VERSION, AI_ACT_ANNEX_IV_HTML, AI_ACT_ANNEX_IV_PDF

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
BULLET_RE = re.compile(r'^\s*(?:[\u2022\u25CF\u25AA\-\*])\s+')
SUBPOINT_RE = re.compile(r'^\s*\(([a-z])\)\s+', re.I)  # (a), (b)...

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
    current: dict | None = None

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
    sme_short = payload.get("enterprise_size", "").lower() == "sme"
    if sme_short:
        short_keys = [
            "system_overview",
            "development_process",
            "risk_management",
            "post_market_plan"
        ]
        short_titles = [
            title for title, key in SECTION_MAPPING if key in short_keys
        ]
        for key, title in zip(short_keys, short_titles):
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
    else:
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
        # Load ICC profile
        icc_path = Path(__file__).parent / "annex4ac" / "resources" / "sRGB.icc"
        if not icc_path.exists():
            typer.secho(f"  ICC profile not found: {icc_path}", fg=typer.colors.RED)
            return
        icc_bytes = icc_path.read_bytes()
        typer.secho(f"  Loaded ICC profile: {len(icc_bytes)} bytes", fg=typer.colors.BLUE)
        
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
            pdf.save(str(path),
                     preserve_pdfa=True,          # don't break PDF/A compliance
                     fix_metadata_version=True,   # fix PDFVersion in XMP if present
                     deterministic_id=True        # reproducible /ID for same input
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
    return Path(__file__).parent.joinpath("templates", "template.html").read_text(encoding='utf-8')

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
        msg = f"Technical doc is older than {max_days} days — consider updating (Art. 11 'doc must be kept up-to-date')."
        if strict:
            typer.secho(f"[ERROR] {msg}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        else:
            typer.secho(f"[WARNING] {msg}", fg=typer.colors.YELLOW)

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
    import os, time, typer, jwt
    from importlib.resources import files

    token = os.getenv("ANNEX4AC_LICENSE")
    if not token:
        typer.secho("Licence env ANNEX4AC_LICENSE not set", fg=typer.colors.RED)
        raise typer.Exit(1)

    # 1) Extract kid from header
    header = jwt.get_unverified_header(token)
    kid = header.get("kid")

    # 2) Public key dictionary (ready for rotation)
    pub_map = {
        "2025-01": Path(__file__).parent.joinpath("lic_pub.pem").read_text()
    }

    key = pub_map.get(kid)
    if not key:
        typer.secho(f"No public key for kid={kid}", fg=typer.colors.RED)
        raise typer.Exit(1)

    claims = jwt.decode(
        token,
        key,
        algorithms=["RS256"],              # Hardcode algorithm for security
        issuer="annex4ac.io",
        audience="annex4ac-cli",
        options={"require": ["exp", "iat", "iss", "aud"]}
    )

    # 3) Check expiration and plan
    if claims["exp"] < time.time():
        typer.secho("License expired", fg=typer.colors.RED)
        raise typer.Exit(1)

    plan = claims.get("plan")
    if plan != "pro":
        typer.secho(f"License plan '{plan}' insufficient for PDF generation", fg=typer.colors.RED)
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
        r = requests.get(AI_ACT_ANNEX_IV_HTML, timeout=20)
        html = r.text
        data = _parse_annex_iv(html)
        data["_schema_version"] = SCHEMA_VERSION
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
def validate(input: Path = typer.Argument(..., exists=True, help="Your filled Annex IV YAML"), sarif: Path = typer.Option(None, help="Write SARIF report to this file"), stale_after: int = typer.Option(0, help="Warn if last_updated older than N days (0=off)", show_default=False), strict_age: bool = typer.Option(False, help="Exit 1 if stale_after is exceeded")):
    """Validate user YAML against required Annex IV keys; exit 1 on error."""
    # Check environment variable for default stale_after
    if stale_after == 0:
        stale_after = int(os.getenv("ANNEX4AC_STALE_AFTER", "0"))
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
        model = AnnexIVSchema(**payload)  # triggers pydantic validation
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
    pdfa: bool = typer.Option(False, help="Convert PDF to PDF/A-2b format for archival")
):
    """Generate output from YAML: PDF (default), HTML, or DOCX."""
    payload = yaml.safe_load(input.read_text(encoding='utf-8'))
    
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
