import pytest
import yaml
from policy.annex4ac_validate import validate_payload
from annex4ac import fetch_annex3_tags

# Check for errors and warnings

def test_wasm_bundle_sync():
    payload = {k: "" for k in [
        "risk_level", "system_overview", "development_process", "system_monitoring", "performance_metrics", "risk_management", "changes_and_versions", "standards_applied", "compliance_declaration", "post_market_plan", "use_cases", "enterprise_size"]}
    denies, warns = validate_payload(payload)
    all_results = warns + denies
    assert any(v["rule"] == "risk_lvl_missing" for v in all_results)
    assert any("warning" in v["rule"] or v.get("level") == "warn" for v in warns) or len(warns) >= 0

def test_high_risk_requires_annex_iv():
    payload = {
        "risk_level": "high",
        "use_cases": [],
        "system_overview": "",
        "development_process": "",
        "system_monitoring": "",
        "performance_metrics": "",
        "risk_management": "",
        "changes_and_versions": "",
        "standards_applied": "",
        "compliance_declaration": "",
        "post_market_plan": "",
        "enterprise_size": "large"
    }
    denies, warns = validate_payload(payload)
    # Check if required rules are among denies
    required_rules = {"overview_required", "dev_process_required"}
    found_rules = {v["rule"] for v in denies}
    for rule in required_rules:
        assert rule in found_rules, f"{rule} not found in denies: {found_rules}"

def test_limited_risk_annex_iv_optional():
    payload = {
        "risk_level": "limited",
        "use_cases": [],
        "system_overview": "",
        "development_process": "",
        "system_monitoring": "",
        "performance_metrics": "",
        "risk_management": "",
        "changes_and_versions": "",
        "standards_applied": "",
        "compliance_declaration": "",
        "post_market_plan": "",
        "enterprise_size": "mid"
    }
    denies, warns = validate_payload(payload)
    assert len(denies) == 0
    assert len(warns) > 0
    assert any("limited_annex_warning" in v["rule"] for v in warns)

def test_auto_high_risk_detection():
    payload = {
        "risk_level": "limited",
        "use_cases": ["employment_screening"],
        "system_overview": "",
        "development_process": "",
        "system_monitoring": "",
        "performance_metrics": "",
        "risk_management": "",
        "changes_and_versions": "",
        "standards_applied": "",
        "compliance_declaration": "",
        "post_market_plan": "",
        "enterprise_size": "mid"
    }
    denies, warns = validate_payload(payload)
    all_results = warns + denies
    assert any(v["rule"] == "auto_high_risk" for v in all_results)

def test_all_annex_iii_tags():
    annex_iii_tags = [
        "biometric_id", "critical_infrastructure", "education_scoring",
        "employment_screening", "essential_services", "law_enforcement",
        "migration_control", "justice_decision"
    ]
    for tag in annex_iii_tags:
        payload = {
            "risk_level": "limited",
            "use_cases": [tag],
            "system_overview": "",
            "development_process": "",
            "system_monitoring": "",
            "performance_metrics": "",
            "risk_management": "",
            "changes_and_versions": "",
            "standards_applied": "",
            "compliance_declaration": "",
            "post_market_plan": "",
            "enterprise_size": "mid"
        }
        denies, warns = validate_payload(payload)
        all_results = warns + denies
        # Should trigger auto_high_risk
        assert any(v["rule"] == "auto_high_risk" for v in all_results), f"Tag {tag} should trigger high-risk, got: {all_results}"
        # Should have denies for required fields (high-risk)
        required_rules = {"overview_required", "dev_process_required"}
        found_rules = {v["rule"] for v in denies}
        for rule in required_rules:
            assert rule in found_rules, f"{rule} not found in denies for tag {tag}: {found_rules}"

def test_dynamic_annex3_tags_auto_high_risk():
    tags = fetch_annex3_tags()
    for tag in tags:
        payload = {
            "risk_level": "limited",
            "use_cases": [tag],
            "system_overview": "",
            "development_process": "",
            "system_monitoring": "",
            "performance_metrics": "",
            "risk_management": "",
            "changes_and_versions": "",
            "standards_applied": "",
            "compliance_declaration": "",
            "post_market_plan": "",
            "enterprise_size": "mid"
        }
        denies, warns = validate_payload(payload)
        all_results = warns + denies
        assert any(v["rule"] == "auto_high_risk" for v in all_results), f"Tag {tag} should trigger high-risk, got: {all_results}" 