import pytest
from opa_wasm import OPARuntime

def test_wasm_bundle_sync():
    with open("annex4ac/annex4_validation.wasm", "rb") as f:
        rt = OPARuntime(f.read())
        # Example: empty payload should return all errors
        payload = {k: "" for k in [
            "risk_level", "system_overview", "development_process", "system_monitoring", "performance_metrics", "risk_management", "changes_and_versions", "standards_applied", "compliance_declaration", "post_market_plan", "use_cases"]}
        res = rt.evaluate({"input": payload})[0]["expressions"][0]["value"]
        assert any(v["rule"] == "risk_lvl_missing" for v in res)

def test_high_risk_requires_annex_iv():
    """Test that high-risk systems require all Annex IV sections."""
    with open("annex4ac/annex4_validation.wasm", "rb") as f:
        rt = OPARuntime(f.read())
        # High-risk with missing sections should fail
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
            "enterprise_size": "mid"
        }
        res = rt.evaluate({"input": payload})[0]["expressions"][0]["value"]
        # Should have violations for missing sections
        assert len([v for v in res if "required" in v["rule"]]) > 0

def test_limited_risk_annex_iv_optional():
    """Test that limited-risk systems can pass without Annex IV (warnings only)."""
    with open("annex4ac/annex4_validation.wasm", "rb") as f:
        rt = OPARuntime(f.read())
        # Limited-risk with missing sections should only get warnings
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
        res = rt.evaluate({"input": payload})[0]["expressions"][0]["value"]
        # Should have warnings but no hard violations for missing sections
        warnings = [v for v in res if "warning" in v["rule"]]
        violations = [v for v in res if "required" in v["rule"] and "warning" not in v["rule"]]
        assert len(warnings) > 0
        assert len(violations) == 0

def test_auto_high_risk_detection():
    """Test that use_cases with Annex III tags auto-detect high-risk."""
    with open("annex4ac/annex4_validation.wasm", "rb") as f:
        rt = OPARuntime(f.read())
        # Limited-risk with high-risk use_case should trigger auto-detection
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
        res = rt.evaluate({"input": payload})[0]["expressions"][0]["value"]
        # Should have auto_high_risk violation
        assert any(v["rule"] == "auto_high_risk" for v in res)

def test_all_annex_iii_tags():
    """Test that all 8 Annex III categories are recognized."""
    with open("annex4ac/annex4_validation.wasm", "rb") as f:
        rt = OPARuntime(f.read())
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
            res = rt.evaluate({"input": payload})[0]["expressions"][0]["value"]
            # Each tag should trigger auto_high_risk
            assert any(v["rule"] == "auto_high_risk" for v in res), f"Tag {tag} should trigger high-risk" 