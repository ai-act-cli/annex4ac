package annex4ac

import rego.v1

### ─────────────────────────────────────────────────────
### Helper sets and functions
### ─────────────────────────────────────────────────────
high_risk_tags := {
	"employment_screening", "biometric_id", "critical_infrastructure",
	"education_scoring", "justice_decision", "migration_control",
	"essential_services", "law_enforcement",
}

# true if the value is "blank" in any way
is_blank(x) if x == "" # empty string
is_blank(x) if trim(x, " ") == "" # string of spaces
is_blank(x) if x == [] # empty list
is_blank(x) if not x # field missing / null

### ─────────────────────────────────────────────────────
### Determine high‑risk
### ─────────────────────────────────────────────────────
is_high_risk if input.risk_level == "high"

is_high_risk if {
	some tag
	input.use_cases[_] == tag
	high_risk_tags[tag]
}

### ─────────────────────────────────────────────────────
### Required sections for Annex IV
### ─────────────────────────────────────────────────────
required_fields := [
	{"field": "system_overview", "rule": "overview_required", "msg": "§1: general description is missing."},
	{"field": "development_process", "rule": "dev_process_required", "msg": "§2: development process is missing."},
	{"field": "system_monitoring", "rule": "monitoring_required", "msg": "§3: system monitoring info missing."},
	{"field": "performance_metrics", "rule": "metrics_required", "msg": "§4: performance metrics missing."},
	{"field": "risk_management", "rule": "risk_mgmt_required", "msg": "§5: risk management is missing."},
	{"field": "changes_and_versions", "rule": "changes_versioning", "msg": "§6: lifecycle changes must be documented."},
	{"field": "standards_applied", "rule": "standards_or_alternatives", "msg": "§7: list harmonised standards or alternative specs."},
	{"field": "compliance_declaration", "rule": "eu_decl_required", "msg": "§8: copy of EU declaration of conformity is missing."},
	{"field": "post_market_plan", "rule": "post_market_required", "msg": "§9: post‑market plan is missing."},
]

### ─────────────────────────────────────────────────────
### DENY rules
### ─────────────────────────────────────────────────────
# 1) high‑risk: required sections
deny contains msg if {
	is_high_risk
	some i
	f := required_fields[i]
	is_blank(input[f.field])
	msg := {"rule": f.rule, "msg": f.msg}
}

# 2) risk_level is required
deny contains msg if {
	is_blank(input.risk_level)
	msg := {"rule": "risk_lvl_missing", "msg": "risk_level must be set."}
}

# 3) auto‑flag: high‑risk use‑case, but risk_level is not "high"
deny contains msg if {
	input.risk_level != "high"
	tag := input.use_cases[_]
	high_risk_tags[tag]
	msg := {
		"rule": "auto_high_risk",
		"msg": sprintf("Use‑case '%v' triggers high‑risk; set risk_level: high.", [tag]),
	}
}

# 4) post_market_plan: separate duplicate requirement
deny contains msg if {
	input.risk_level == "high"
	is_blank(input.post_market_plan)
	msg := {"rule": "high_post_market", "msg": "High‑risk ⇒ post‑market plan (§9) is mandatory."}
}

# ─── New condition ─────────────────────────
is_sme if input.enterprise_size == "sme"
# ─── DENY rules: high‑risk + NOT SME ──────
deny contains msg if {
    is_high_risk
    not is_sme
    some j
    f := required_fields[j]
    is_blank(input[f.field])
    msg := {"rule": f.rule, "msg": f.msg}
}
# ─── SME‑high‑risk → warning only ────────
warn contains msg if {
    is_high_risk
    is_sme
    some k
    f := required_fields[k]
    is_blank(input[f.field])
    msg := {
        "rule": "sme_annex_warning",
        "msg": sprintf("High‑risk SME: Annex IV %v missing – simplified form accepted.", [f.field]),
    }
}

### ─────────────────────────────────────────────────────
### WARN rules (for limited / minimal)
### ─────────────────────────────────────────────────────
warn contains msg if {
	not is_high_risk
	some i
	f := required_fields[i]
	is_blank(input[f.field])
	msg := {
		"rule": "limited_annex_warning",
		"msg": sprintf("Limited/minimal risk: Annex IV %v is optional but recommended for transparency.", [f.field]),
	}
}
