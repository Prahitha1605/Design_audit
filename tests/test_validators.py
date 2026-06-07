import pytest

from design_audit_agent.agent.validators import (
    AuditReport,
    Difference,
    Finding,
    validate_audit_report,
    validate_comparison_report,
)


def test_generic_location_rejected():
    raw = {
        "image_path": "x.png",
        "model_used": "gemini",
        "findings": [
            {
                "id": "F1",
                "principle": "Contrast",
                "severity": "low",
                "location": "the page",
                "issue": "n/a",
                "user_impact": "n/a",
                "recommendation": "n/a",
                "confidence": 50,
            }
        ],
        "summary": "s",
        "total_findings": 1,
        "critical_count": 0,
        "scan_duration_ms": 1,
    }
    with pytest.raises(ValueError):
        validate_audit_report(raw)


def test_duplicate_finding_rejected():
    raw = {
        "image_path": "x.png",
        "model_used": "gemini",
        "findings": [
            {
                "id": "F1",
                "principle": "Contrast",
                "severity": "low",
                "location": "header",
                "issue": "n/a",
                "user_impact": "n/a",
                "recommendation": "n/a",
                "confidence": 50,
            },
            {
                "id": "F2",
                "principle": "Contrast",
                "severity": "low",
                "location": "header",
                "issue": "n/a",
                "user_impact": "n/a",
                "recommendation": "n/a",
                "confidence": 60,
            },
        ],
        "summary": "s",
        "total_findings": 2,
        "critical_count": 0,
        "scan_duration_ms": 1,
    }
    with pytest.raises(ValueError):
        validate_audit_report(raw)


def test_missing_mandatory_fields_rejected():
    raw = {
        "image_path": "x.png",
        "model_used": "gemini",
        "findings": [
            {
                "id": "F1",
                "principle": "",
                "severity": "high",
                "location": "",
                "issue": "",
                "user_impact": "Users may miss content.",
                "recommendation": "Increase contrast to at least 4.5:1.",
                "confidence": None,
            }
        ],
        "summary": "s",
        "total_findings": 1,
        "critical_count": 0,
        "scan_duration_ms": 1,
    }
    with pytest.raises(ValueError, match="Audit report validation failed"):
        validate_audit_report(raw)


def test_low_confidence_report_flagged():
    raw = {
        "image_path": "x.png",
        "model_used": "gemini",
        "findings": [
            {
                "id": "F1",
                "principle": "Contrast",
                "severity": "critical",
                "location": "body text block",
                "issue": "Body text contrast is below WCAG AA against the background.",
                "user_impact": "Users with low vision may struggle to read core page text.",
                "recommendation": "Increase the text contrast ratio to at least 4.5:1 against its background.",
                "confidence": 30,
            }
        ],
        "summary": "s",
        "total_findings": 1,
        "critical_count": 1,
        "scan_duration_ms": 1,
    }
    r = validate_audit_report(raw)
    d = r.model_dump()
    assert d.get("low_confidence") is True


def test_clickable_recommendation_allowed():
    raw = {
        "image_path": "x.png",
        "model_used": "gemini",
        "findings": [
            {
                "id": "F1",
                "principle": "Affordance",
                "severity": "medium",
                "location": "primary CTA",
                "issue": "The button does not look interactive enough.",
                "user_impact": "Users may not recognize the action target on first glance.",
                "recommendation": "Use clear visual affordance so the button appears clickable.",
                "confidence": 70,
            }
        ],
        "summary": "s",
        "total_findings": 1,
        "critical_count": 0,
        "scan_duration_ms": 1,
    }
    r = validate_audit_report(raw)
    assert r.total_findings == 1


def test_hover_state_finding_marked_not_observable():
    raw = {
        "image_path": "x.png",
        "model_used": "gemini",
        "findings": [
            {
                "id": "F1",
                "principle": "Interaction",
                "severity": "high",
                "location": "navigation menu",
                "issue": "Hover effects are inconsistent across menu items.",
                "user_impact": "Users may not understand which item is active or interactive.",
                "recommendation": "Add a visible hover state to the navigation items.",
                "confidence": 65,
            }
        ],
        "summary": "s",
        "total_findings": 1,
        "critical_count": 0,
        "scan_duration_ms": 1,
    }
    r = validate_audit_report(raw)
    assert r.findings[0].visibility_status == "not_observable"
    assert "behavior not directly visible" in r.findings[0].issue.lower()
    assert "static screenshot" in r.findings[0].recommendation.lower()
