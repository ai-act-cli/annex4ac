# Annex IV‑as‑Code (annex4ac)

Generate and validate EU AI Act Annex IV technical documentation straight from your CI. 

100% local by default.

SaaS/PDF unlocks with a licence key .

> **⚠️ Legal Disclaimer:** This software is provided for informational and compliance assistance purposes only. It is not legal advice and should not be relied upon as such. Users are responsible for ensuring their documentation meets all applicable legal requirements and should consult with qualified legal professionals for compliance matters. The authors disclaim any liability for damages arising from the use of this software.

> **🔒 Data Protection:** All processing occurs locally on your machine. No data leaves your system.

---

## ✨ Features

* **Always up‑to‑date** – every run pulls the latest Annex IV HTML from the official AI Act Explorer.
* **Schema‑first** – YAML scaffold mirrors the **9 numbered sections** adopted in the July 2024 Official Journal.
* **Fail‑fast CI** – `annex4ac validate` exits 1 when a mandatory field is missing, so a GitHub Action can block the PR.
* **Zero binaries** – ReportLab renders the PDF; no LaTeX, no system packages.
* **Freemium** – `fetch-schema` & `validate` are free; `generate` (PDF) requires `ANNEX4AC_LICENSE`.
* **Built-in rule engine** – business-logic validation runs locally via pure Python.
* **EU-compliant formatting** – proper list punctuation (semicolons and periods) and ordered list formatting (a), (b), (c) according to EU drafting rules.
* **Retention tracking** – automatic 10-year retention period calculation and metadata embedding (Article 18 compliance).
* **Freshness validation** – warns or fails when documentation is older than specified threshold (Article 11 compliance).
* **PDF/A-2b support** – optional archival PDF format with embedded ICC profiles for long-term preservation.

---

## 🛠 Requirements

- Python 3.9+
- [reportlab](https://www.reportlab.com/documentation) (PDF, Pro)
- [pydantic](https://docs.pydantic.dev) (schema validation)
- [typer](https://typer.tiangolo.com) (CLI)
- [pyyaml](https://pyyaml.org/) (YAML)

---

## 🚀 Quick‑start

```bash
# 1 Install (Python 3.9+)
pip install annex4ac

# 2 Pull the latest Annex IV layout
annex4ac fetch-schema annex_template.yaml

# 3 Fill in the YAML → validate
cp annex_template.yaml my_annex.yaml
$EDITOR my_annex.yaml
annex4ac validate -i my_annex.yaml   # "Validation OK!" or exit 1

# 4 Generate output (PDF requires license)
# HTML (free)
annex4ac generate -i my_annex.yaml -o docs/annex_iv.html --fmt html

# DOCX (free)  
annex4ac generate -i my_annex.yaml -o docs/annex_iv.docx --fmt docx

# PDF (Pro - requires license)
export ANNEX4AC_LICENSE="your_jwt_token_here"
annex4ac generate -i my_annex.yaml -o docs/annex_iv.pdf --fmt pdf
```

> **License System:** Pro features require a JWT license token. Contact support to obtain your token, then set it as the `ANNEX4AC_LICENSE` environment variable. See [LICENSE_SYSTEM.md](LICENSE_SYSTEM.md) for details.

> **Hint :** You only need to edit the YAML once per model version—CI keeps it green.

---

## 🗂 Required YAML fields (June 2024 format)

| Key                      | Annex IV § |
| ------------------------ | ---------- |
| `risk_level`             | —          | "high", "limited", "minimal" — determines required sections |
| `use_cases`              | —          | List of tags (Annex III) for auto high-risk. Acceptable values: employment_screening, biometric_id, critical_infrastructure, education_scoring, justice_decision, migration_control |
| `system_overview`        |  1         |
| `development_process`    |  2         |
| `system_monitoring`      |  3         |
| `performance_metrics`    |  4         |
| `risk_management`        |  5         |
| `changes_and_versions`   |  6         |
| `standards_applied`      |  7         |
| `compliance_declaration` |  8         |
| `post_market_plan`       |  9         |
| `enterprise_size`        | —          | `"sme"`, `"mid"`, `"large"` – determines if the PDF will be generated in short SME form automatically. |
| `placed_on_market`       | —          | ISO datetime when the AI system was placed on market (required for retention calculation). |
| `last_updated`           | —          | ISO datetime of last documentation update (required for freshness validation). |

---

## 🛠 Commands

| Command        | What it does                                                                  |
| -------------- | ----------------------------------------------------------------------------- |
| `fetch-schema` | Download the current Annex IV HTML, convert to YAML scaffold `annex_schema.yaml`. |
| `validate`     | Validate your YAML against the Pydantic schema and built-in Python rules. Exits 1 on error. Supports `--sarif` for GitHub annotations, `--max-age` for freshness validation, and `--strict-age` for strict age checking.             |
| `generate`     | Render PDF (Pro), HTML, or DOCX from YAML. PDF requires license, HTML/DOCX are free. |

Run `annex4ac --help` for full CLI.

---

## 🆕 New Features

### EU-Compliant List Formatting
Lists are automatically formatted according to EU drafting rules:
- Ordered lists: `(a) ...; (b) ...; (c) ...`
- Unordered lists: `• ...; • ...; • ...`
- Proper punctuation with semicolons and final periods

### Retention and Freshness Tracking
- **10-year retention**: Automatic calculation and metadata embedding
- **Freshness validation**: `--max-age 180` (default) or `--strict-age` for CI enforcement
- **Compliance**: Meets Article 11 (up-to-date) and Article 18 (retention) requirements

### PDF/A-2b Archival Support
Enable archival PDF generation with:

```bash
# Generate PDF/A-2b for long-term preservation
annex4ac generate -i my_annex.yaml --fmt pdf --pdfa

# PDF/A-2b includes:
# - Embedded sRGB ICC profile
# - XMP metadata
# - ISO 19005-2:2011 compliance
# - 10-year retention metadata
```

**Legal compliance**: PDF/A-2b format ensures documents remain accessible and visually identical for decades, meeting archival requirements under Article 18 of Regulation 2024/1689.
```bash
export ANNEX4AC_PDFA=1
annex4ac generate -i my_annex.yaml -o annex_iv.pdf --fmt pdf
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

## 🐙 GitHub Action example

```yaml
name: Annex IV gate
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
      - run: annex4ac validate -i spec/model.yaml
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
| Pro        | **€15 / month** | PDF generation, version history (future SaaS), email support |
| Enterprise | Custom          | Self‑hosted Docker, SLA 99.9 %, custom sections              |

Pay once, use anywhere – CLI, GitHub Action, future REST API.

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
