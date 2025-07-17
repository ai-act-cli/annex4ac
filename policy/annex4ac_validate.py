#!/usr/bin/env python3
"""
annex4ac_validate.py

Standalone Python validator for Annex IV-as-Code models.
Replaces Rego-based OPA rules with pure-Python logic.
"""

import sys
import json
import yaml

# ──────────────────────────────────────────────────────────────────────────────
# Configuration of rules (translated from Rego)
# ──────────────────────────────────────────────────────────────────────────────

HIGH_RISK_TAGS = {
    "employment_screening", "biometric_id", "critical_infrastructure",
    "education_scoring", "justice_decision", "migration_control",
    "essential_services", "law_enforcement",
}

PROHIBITED_TAGS = {
    "social_scoring",
    "emotion_recognition",
    "real_time_remote_biometric_identification"
}

REQUIRED_FIELDS = [
    ("system_overview",         "overview_required",           "§1: general description is missing."),
    ("development_process",     "dev_process_required",        "§2: development process is missing."),
    ("system_monitoring",       "monitoring_required",         "§3: system monitoring info missing."),
    ("performance_metrics",     "metrics_required",            "§4: performance metrics missing."),
    ("risk_management",         "risk_mgmt_required",          "§5: risk management is missing."),
    ("changes_and_versions",    "changes_versioning",          "§6: lifecycle changes must be documented."),
    ("standards_applied",       "standards_or_alternatives",   "§7: list harmonised standards or alternative specs."),
    ("compliance_declaration",  "eu_decl_required",            "§8: copy of EU declaration of conformity is missing."),
    ("post_market_plan",        "post_market_required",        "§9: post‑market plan is missing."),
]

# ──────────────────────────────────────────────────────────────────────────────
# Utility
# ──────────────────────────────────────────────────────────────────────────────

def is_blank(x):
    if x is None: return True
    if isinstance(x, str) and x.strip() == "": return True
    if isinstance(x, (list, dict)) and len(x) == 0: return True
    return False

# ──────────────────────────────────────────────────────────────────────────────
# Main validation logic
# ──────────────────────────────────────────────────────────────────────────────

def validate_payload(payload):
    denies = []
    warns = []

    risk = payload.get("risk_level")
    use_cases = payload.get("use_cases", [])
    size = payload.get("enterprise_size")

    # 1) risk_level required
    if is_blank(risk):
        denies.append({"rule":"risk_lvl_missing","msg":"risk_level must be set."})

    # 2) prohibited practices
    for t in use_cases:
        if t in PROHIBITED_TAGS:
            denies.append({
                "rule":"unacceptable_practice",
                "msg":f"Use‑case {t} is prohibited by Art‑5 AI Act."
            })

    # 3) auto_high_risk
    for t in use_cases:
        if t in HIGH_RISK_TAGS and risk != "high":
            denies.append({
                "rule":"auto_high_risk",
                "msg":f"Use‑case '{t}' triggers high‑risk; set risk_level: high."
            })

    # 4) high_post_market
    if risk == "high" and is_blank(payload.get("post_market_plan")):
        denies.append({"rule":"high_post_market","msg":"High‑risk ⇒ post‑market plan (§9) is mandatory."})

    # 5) enterprise_size required
    if is_blank(size):
        denies.append({"rule":"size_missing","msg":"enterprise_size must be set."})

    # Determine high-risk flag
    high_risk = (risk == "high") or any(tag in HIGH_RISK_TAGS for tag in use_cases)
    is_sme = (size == "sme")

    # 6) required_fields
    for idx, (field, rule, msg) in enumerate(REQUIRED_FIELDS):
        if high_risk:
            if is_blank(payload.get(field)):
                if not is_sme:
                    denies.append({"rule":rule,"msg":msg})
                else:
                    warns.append({
                        "rule":"sme_annex_warning",
                        "msg":f"High‑risk SME: Annex IV §{idx+1} missing – simplified form accepted."
                    })
        else:
            # warn for limited/minimal risk
            if is_blank(payload.get(field)):
                warns.append({
                    "rule":"limited_annex_warning",
                    "msg":f"Limited/minimal risk: Annex IV {field} is optional but recommended for transparency."
                })

    return denies, warns

# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) != 2:
        print("Usage: python annex4ac_validate.py <input.yaml>", file=sys.stderr)
        sys.exit(2)

    path = sys.argv[1]
    with open(path, encoding="utf-8") as f:
        payload = yaml.safe_load(f)

    denies, warns = validate_payload(payload)

    # Output JSON similar to OPA: combined list
    result = warns + denies
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if denies:
        sys.exit(1)
    else:
        sys.exit(0)

if __name__ == "__main__":
    main() 