# Annex IV‑as‑Code (annex4ac)

Generate and validate EU AI Act Annex IV technical documentation straight from your CI. 

100% local by default.

SaaS/PDF in the Pro tier.

---

## ✨ Features

* **Always up‑to‑date** – fetches the latest Annex IV text from the AI Act Explorer every run.
* **Schema‑first** – YAML skeleton mirrors the 13 required sections.
* **Fail‑fast CI** – exits 1 when mandatory fields are missing.
* **Freemium** – `fetch-schema` & `validate` are free; `generate` (PDF) unlocks with a licence key.
* **One‑line GitHub Action** – block pull requests that break compliance.

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

# 4 (Pro) Generate the PDF
echo "ANNEX4AC_LICENSE=your_key" >> ~/.bashrc
annex4ac generate -i my_annex.yaml -o docs/annex_iv.pdf
```

> **Hint :** You only need to edit the YAML once per model version—CI keeps it green.

---

## 🗂 Required YAML fields

| Key                       | Corresponds to Annex IV § |
| ------------------------- | ------------------------- |
| `system_overview`         |  1 «General description»  |
| `intended_purpose`        |  2                        |
| `system_architecture`     |  3                        |
| `development_process`     |  4                        |
| `data_specifications`     |  5                        |
| `performance_metrics`     |  6                        |
| `risk_management`         |  7                        |
| `post_market_plan`        |  8 (only high‑risk)       |
| `human_machine_interface` |  9                        |
| `changes_and_versions`    |  10                       |
| `records_and_logs`        |  11                       |
| `instructions_for_use`    |  12                       |
| `compliance_declaration`  |  13                       |

---

## 🛠 Commands

| Command        | What it does                                                                    |
| -------------- | ------------------------------------------------------------------------------- |
| `fetch-schema` | Download current Annex IV HTML, convert to YAML scaffold `annex_schema.yaml`.   |
| `validate`     |  Validate your YAML against the Pydantic schema & Rego rules. Exits 1 on error. |
| `generate`     | Render PDF via Jinja2 → XeLaTeX (Pro).                                          |

Run `annex4ac --help` for details.

---

## 🐙 GitHub Action example

```yaml
name: Annex IV gate
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

To use the Pro license in CI, add `ANNEX4AC_LICENSE` as an encrypted repository secret.

---

## ⚙️ Local development

```bash
git clone https://github.com/your‑org/annex4ac
cd annex4ac
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest                     # unit and opa tests
python annex4ac.py --help
```

### TinyTeX (Linux/Mac)

```bash
curl -sL https://yihui.org/tinytex/install-unx.sh | sh
export PATH="$HOME/.TinyTeX/bin/x86_64-linux:$PATH"
```

---

## 🔑 Licensing & pricing

* **Community (free)** – validate in CI, unlimited public repos.
* **Pro €15/mo** – PDF generation, version history (SaaS), email support.
* **Enterprise** – self‑hosted Docker, SLA 99.9 %, custom sections.

Pay once, use anywhere: CLI, GitHub Action, REST (coming soon).

---

## 📚 References

* Annex IV HTML – [https://artificialintelligenceact.eu/annex/4/](https://artificialintelligenceact.eu/annex/4/)
* Official Journal PDF – [https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=OJ\:L\_202401689](https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=OJ:L_202401689)
* Typer docs – [https://typer.tiangolo.com](https://typer.tiangolo.com)
* Pydantic docs – [https://docs.pydantic.dev](https://docs.pydantic.dev)
* Open Policy Agent – [https://www.openpolicyagent.org](https://www.openpolicyagent.org)
* TinyTeX install – [https://yihui.org/tinytex](https://yihui.org/tinytex)
