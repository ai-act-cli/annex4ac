# Annex IV‑as‑Code (annex4ac)
Project code and files located at https://github.com/ai-act-cli/annex4ac

Generate and validate EU AI Act Annex IV technical documentation straight from your CI. 

100% local by default.

SaaS/PDF unlocks with a licence key .

> **⚠️ Legal Disclaimer:** This software is provided for informational and compliance assistance purposes only. It is not legal advice and should not be relied upon as such. Users are responsible for ensuring their documentation meets all applicable legal requirements and should consult with qualified legal professionals for compliance matters. The authors disclaim any liability for damages arising from the use of this software.

> **🔒 Data Protection:** All processing occurs locally on your machine. No data leaves your system.

---

## 🚀 Quick‑start

```bash
# 1 Install (Python 3.9+) - includes all dependencies
pip install annex4ac

# 2 Pull the latest Annex IV layout
annex4ac fetch-schema annex_template.yaml

# 3 Fill in the YAML → validate
cp annex_template.yaml my_annex.yaml
$EDITOR my_annex.yaml
annex4ac validate my_annex.yaml   # "Validation OK!" or exit 1

# Optional: Check if document is stale (heuristic, not legal requirement)
annex4ac validate my_annex.yaml --stale-after 30  # Warn if older than 30 days
annex4ac validate my_annex.yaml --stale-after 180 --strict-age  # Fail CI if older than 180 days

# 4 Generate output (PDF requires license)
# HTML (free) - automatically validates before generation
annex4ac generate my_annex.yaml --output annex_iv.html --fmt html

# DOCX (free) - automatically validates before generation
annex4ac generate my_annex.yaml --output annex_iv.docx --fmt docx

# PDF (Pro - requires license) - automatically validates before generation
export ANNEX4AC_LICENSE="your_jwt_token_here"
annex4ac generate my_annex.yaml --output annex_iv.pdf --fmt pdf

# Skip validation if needed (not recommended)
annex4ac generate my_annex.yaml --output annex_iv.pdf --fmt pdf --skip-validation

# 5 Review existing documentation (optional)
# Note: Review functionality has been moved to annex4nlp package
annex4nlp annex_iv.pdf  # Analyze for compliance issues
annex4nlp doc1.pdf doc2.pdf  # Compare multiple documents for contradictions
```

> **License System:** Pro features require a JWT license token. Contact support to obtain your token, then set it as the `ANNEX4AC_LICENSE` environment variable. See [LICENSE_SYSTEM.md](LICENSE_SYSTEM.md) for details.

> **Hint :** You only need to edit the YAML once per model version—CI keeps it green.

---

## 💡 Use Cases

**For Developers:**
- Generate compliant Annex IV documentation from YAML
- Validate documentation in CI/CD pipelines
- Review existing PDFs for compliance issues

**For Legal Teams:**
- Ensure all 9 required sections are present
- Check for contradictions between documents
- Verify GDPR compliance requirements

**For Enterprises:**
- Generate archival PDF/A-2b documents
- Track 10-year retention periods
- Maintain up-to-date technical documentation

---



---

## 🗂 Required YAML fields (June 2024 format)

| Key                      | Annex IV § |
| ------------------------ | ---------- |
| `risk_level`             | —          | "high", "limited", "minimal" — determines required sections |
| `use_cases`              | —          | List of tags (Annex III) for auto high-risk. Acceptable values: employment_screening, biometric_id, critical_infrastructure, education_scoring, justice_decision, migration_control |
| `system_overview`        |  1         |
| `development_process`    |  2         |
| `system_monitoring`      |  3         |
| `performance_metrics`    |  4         |
| `risk_management`        |  5         |
| `changes_and_versions`   |  6         |
| `standards_applied`      |  7         |
| `compliance_declaration` |  8         |
| `post_market_plan`       |  9         |
| `enterprise_size`        | —          | `"sme"`, `"mid"`, `"large"` – enterprise size classification (Art. 11 exemption). |
| `placed_on_market`       | —          | ISO datetime when the AI system was placed on market (required for retention calculation). |
| `last_updated`           | —          | ISO datetime of last documentation update (for optional freshness heuristic). |

---

## 🛠 Commands

| Command        | What it does                                                                  |
| -------------- | ----------------------------------------------------------------------------- |
| `fetch-schema` | Download the current Annex IV HTML, convert to YAML scaffold `annex_schema.yaml`. |
| `validate`     | Validate your YAML against the Pydantic schema and built-in Python rules. Exits 1 on error. Supports `--sarif` for GitHub annotations, `--stale-after` for optional freshness heuristic, and `--strict-age` for strict age checking.             |
| `generate`     | Render PDF (Pro), HTML, or DOCX from YAML. Automatically validates before generation. PDF requires license, HTML/DOCX are free. |
| `annex4nlp`       | Review functionality has been moved to `annex4nlp` package. Analyze PDF technical documentation for compliance issues, missing sections, and contradictions between documents. Uses advanced NLP for intelligent negation detection. Provides detailed console output with error/warning classification.|

Run `annex4ac --help` for full CLI.

---

## ✨ Features

Generate compliant EU AI Act Annex IV documentation with advanced validation and review capabilities.

### Schema-First Approach
- **Always up-to-date**: Every run pulls the latest Annex IV HTML from the official AI Act Explorer
- **9 numbered sections**: YAML scaffold mirrors the official July 2024 format
- **Auto-validation**: `annex4ac generate` validates before generation
- **Fail-fast CI**: `annex4ac validate` exits 1 on errors, blocking PRs

### Multiple Output Formats
- **HTML (Free)**: Web-ready documentation
- **DOCX (Free)**: Microsoft Word compatible
- **PDF (Pro)**: Professional PDF with embedded fonts and metadata
- **PDF/A-2b (Pro)**: Archival format for long-term preservation

### EU-Compliant Formatting
- **List formatting**: `(a) ...; (b) ...; (c) ...` according to EU drafting rules
- **Hierarchical lists**: Support for nested structures
- **Cross-format consistency**: Same formatting in PDF, HTML, and DOCX
- **Proper punctuation**: Semicolons and final periods

### Compliance Review: annex4nlp
- **Advanced NLP**: Uses spaCy and negspaCy for intelligent analysis
- **Section validation**: Checks all 9 required Annex IV sections
- **Contradiction detection**: Finds inconsistencies between documents
- **GDPR compliance**: Analyzes data protection and privacy issues
- **Console output**: Detailed error/warning classification

## 🔧 Advanced Features

### Retention and Freshness Tracking
- **10-year retention**: Automatic calculation from `placed_on_market` date
- **Freshness validation**: `--stale-after N` for document age checking
- **Legal compliance**: Meets Article 18 requirements

### List Formatting Examples

#### Hierarchical Lists (EU-Compliant)
```yaml
development_process: |
  (a) Requirements analysis phase (3 months):
      - Stakeholder interviews and requirements gathering
      - Technical feasibility assessment
      - Risk analysis and compliance review
  
  (b) Design and architecture phase (4 months):
      - System architecture design
      - Data flow and security design
      - Integration planning
```

#### Regular Bulleted Lists
```yaml
standards_applied: |
  Compliance with international standards:
  
  - ISO 27001: Information security management
  - IEEE 2857: AI system development guidelines
  - GDPR: Data protection and privacy
  - ISO 9001: Quality management systems
  - Internal AI ethics guidelines and policies
```

### Console Output Example
```
============================================================
COMPLIANCE REVIEW RESULTS
============================================================

❌ ERRORS (2):
  1. [doc1.pdf] (Section 1) Missing content for Annex IV section 1.
  2. [doc2.pdf] (Section 5) No mention of risk management procedures.

⚠️  WARNINGS (1):
  1. [doc1.pdf] No mention of transparency or explainability.

Found 3 total issue(s): 2 errors, 1 warnings
```



---

## 🏷️ High-risk tags (Annex III)

The list of high-risk tags (Annex III) is now loaded dynamically from the official website. If the network is unavailable, a cache or fallback list is used. This affects the auto_high_risk logic in validation.

---

## 🏷️ Schema version in PDF

Each PDF now displays the Annex IV schema version stamp (e.g., v20240613) and the document generation date.

---

## 🔑 Pro-licence & JWT

To generate PDF in Pro mode, a license is required (JWT, RSA signature). The ANNEX4AC_LICENSE key can be checked offline, the public key is stored in the package. See [LICENSE_SYSTEM.md](LICENSE_SYSTEM.md) for detailed information about the license system.

---

## 🛡️ Rule-based validation (Python)

- **High-risk systems**: All 9 sections of Annex IV are mandatory (Art. 11 §1).
- **Limited/minimal risk**: Annex IV is optional but recommended for transparency (Art. 52).
- For high-risk (`risk_level: high`), post_market_plan is required.
- If use_cases contains a high-risk tag (Annex III), risk_level must be high (auto high-risk).
- SARIF report now supports coordinates (line/col) for integration with GitHub Code Scanning.
- **Auto-detection**: Systems with Annex III use_cases are automatically classified as high-risk.

---

## 🐙 GitHub Action example

```yaml
name: Annex IV gate
on: [pull_request]

jobs:
  ai-act-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install annex4ac
      - run: annex4ac validate model.yaml
```

Add `ANNEX4AC_LICENSE` as a secret to use PDF export in CI.

---

## 📄 Offline cache

If Annex IV is temporarily unavailable online, use:

```bash
annex4ac fetch-schema --offline
```

This will load the last saved schema from `~/.cache/annex4ac/` (the cache is updated automatically every 14 days).

---

## ⚙️ Local development

```bash
git clone https://github.com/your‑org/annex4ac
cd annex4ac
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest                     # unit tests
python annex4ac.py --help
```

---

## 🔑 Licensing & pricing

| Tier       | Price           | Features                                                     |
| ---------- | --------------- | ------------------------------------------------------------ |
| Community  | **Free**        | `fetch-schema`, `validate`, unlimited public repos           |
| Pro        | **€15 / month** | PDF generation, version history (future SaaS), email support |
| Enterprise | Custom          | Self‑hosted Docker, SLA 99.9 %, custom sections              |

Pay once, use anywhere – CLI, GitHub Action, future REST API.

---

## 🛠 Requirements

- Python 3.9+
- [reportlab](https://www.reportlab.com/documentation) (PDF, Pro)
- [pydantic](https://docs.pydantic.dev) (schema validation)
- [typer](https://typer.tiangolo.com) (CLI)
- [pyyaml](https://pyyaml.org/) (YAML)

---

## 📚 References

* Annex IV HTML – [https://artificialintelligenceact.eu/annex/4/](https://artificialintelligenceact.eu/annex/4/)
* Official Journal PDF – [https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=OJ:L_202401689](https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=OJ:L_202401689)
* ReportLab docs – [https://www.reportlab.com/documentation](https://www.reportlab.com/documentation)
* Typer docs – [https://typer.tiangolo.com](https://typer.tiangolo.com)
* Pydantic docs – [https://docs.pydantic.dev](https://docs.pydantic.dev)
* PDF/A Standard – [ISO 19005-2:2011](https://www.iso.org/standard/50655.html)
* sRGB Color Space – [IEC 61966-2-1:1999](https://webstore.iec.ch/publication/6169)

---

## 📄 Licensing

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

### Third-party Components

* **PyJWT** - MIT License
* **ReportLab** - BSD-style License  
* **Typer** - MIT License
* **Liberation Sans Fonts** - SIL Open Font License 1.1 (included in `fonts/` directory)

The Liberation Sans fonts are used for PDF generation and are licensed under the SIL Open Font License 1.1. See the [LICENSE](LICENSE) file for the complete license text. 

The software assists in preparing documentation, but does not confirm compliance with legal requirements or standards. The user is responsible for the final accuracy and compliance of the documents.
