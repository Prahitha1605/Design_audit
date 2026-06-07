import json
from pathlib import Path

from google.api_core.exceptions import ResourceExhausted
from PIL import Image

from design_audit_agent.agent.auditor import AnalysisError, analyze_screenshot


def _write_png(path: Path) -> None:
    img = Image.new("RGB", (100, 100), color=(255, 255, 255))
    img.save(path)


def test_valid_png_accepted(monkeypatch, tmp_path):
    img_path = tmp_path / "test.png"
    _write_png(img_path)

    sample = {
        "image_path": str(img_path),
        "model_used": "gemini",
        "findings": [
            {
                "id": "F001",
                "principle": "Contrast",
                "severity": "high",
                "location": "hero CTA button",
                "issue": "Button text is light gray on a medium blue background, reducing legibility.",
                "user_impact": "Users with low vision will have difficulty reading the call-to-action text.",
                "recommendation": "Increase contrast by using darker text or a darker button background to meet 4.5:1.",
                "confidence": 90,
            },
            {
                "id": "F002",
                "principle": "Spacing",
                "severity": "medium",
                "location": "header navigation",
                "issue": "Navigation links are spaced too tightly and appear crowded.",
                "user_impact": "Users may struggle to distinguish individual navigation targets quickly.",
                "recommendation": "Increase horizontal space between links to at least 16px.",
                "confidence": 80,
            },
            {
                "id": "F003",
                "principle": "Alignment",
                "severity": "low",
                "location": "footer link group",
                "issue": "Footer links are not aligned to the same baseline.",
                "user_impact": "The footer appears visually uneven and less polished.",
                "recommendation": "Align footer links to the baseline grid used by the copyright text.",
                "confidence": 70,
            },
        ],
        "summary": "ok",
        "total_findings": 3,
        "critical_count": 0,
        "scan_duration_ms": 10,
    }

    def fake_analyze(self, b64, prompt, timeout=30):
        return json.dumps(sample)

    monkeypatch.setattr("design_audit_agent.models.gemini_client.GeminiClient.analyze_image", fake_analyze)
    report = analyze_screenshot(str(img_path), model="gemini")
    assert report.total_findings >= 3


def test_grok_model_uses_local_image_path(monkeypatch, tmp_path):
    img_path = tmp_path / "test.png"
    _write_png(img_path)

    sample = {
        "image_path": str(img_path),
        "model_used": "grok",
        "findings": [
            {
                "id": "F001",
                "principle": "Contrast",
                "severity": "high",
                "location": "hero CTA",
                "issue": "Hero CTA text contrast is too low against the blue button.",
                "user_impact": "Users with low vision may miss the primary action button.",
                "recommendation": "Use a darker CTA background or darker text to meet 4.5:1 contrast.",
                "confidence": 90,
            }
        ],
        "summary": "ok",
        "total_findings": 1,
        "critical_count": 0,
        "scan_duration_ms": 10,
    }

    def fake_analyze(self, image_path, prompt, timeout=30, second_image_path=None):
        assert image_path == str(img_path)
        return json.dumps(sample)

    monkeypatch.setattr("design_audit_agent.models.grok_client.GrokClient.analyze_image", fake_analyze)
    try:
        analyze_screenshot(str(img_path), model="grok")
        assert False, "Expected AnalysisError due to too few findings"
    except AnalysisError as exc:
        assert "fewer than 3 findings" in str(exc).lower()


def test_gemini_quota_is_reported_as_analysis_error(monkeypatch, tmp_path):
    img_path = tmp_path / "test.png"
    _write_png(img_path)

    def fake_analyze(self, *args, **kwargs):
        raise ResourceExhausted("Quota exceeded")

    monkeypatch.setattr("design_audit_agent.models.gemini_client.GeminiClient.analyze_image", fake_analyze)
    try:
        analyze_screenshot(str(img_path), model="gemini")
        assert False, "Expected AnalysisError"
    except AnalysisError as exc:
        assert "quota" in str(exc).lower()


def test_invalid_format_rejected(tmp_path):
    txt = tmp_path / "file.txt"
    txt.write_text("not an image")
    try:
        analyze_screenshot(str(txt))
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_minimum_findings_enforced(monkeypatch, tmp_path):
    img_path = tmp_path / "min.png"
    _write_png(img_path)

    sample = {
        "image_path": str(img_path),
        "model_used": "gemini",
        "findings": [
            {
                "id": "F001",
                "principle": "Spacing",
                "severity": "low",
                "location": "footer",
                "issue": "Footer spacing is tighter than surrounding sections.",
                "user_impact": "Users may perceive the footer as cramped and less scannable.",
                "recommendation": "Increase footer spacing to match the section rhythm and improve separation.",
                "confidence": 20,
            }
        ],
        "summary": "one",
        "total_findings": 1,
        "critical_count": 0,
        "scan_duration_ms": 5,
    }

    def fake_analyze(self, b64, prompt, timeout=30):
        return json.dumps(sample)

    monkeypatch.setattr("design_audit_agent.models.gemini_client.GeminiClient.analyze_image", fake_analyze)
    try:
        analyze_screenshot(str(img_path), model="gemini")
        assert False, "Expected AnalysisError due to too few findings"
    except AnalysisError as exc:
        assert "fewer than 3 findings" in str(exc).lower()


def test_vague_recommendation_rejected(monkeypatch, tmp_path):
    img_path = tmp_path / "vague.png"
    _write_png(img_path)

    sample = {
        "image_path": str(img_path),
        "model_used": "gemini",
        "findings": [
            {
                "id": "F001",
                "principle": "Contrast",
                "severity": "high",
                "location": "hero CTA",
                "issue": "Button text contrast is low.",
                "user_impact": "Hard to read for some users.",
                "recommendation": "Improve readability",
                "confidence": 85,
            },
            {
                "id": "F002",
                "principle": "Spacing",
                "severity": "medium",
                "location": "card grid",
                "issue": "Cards are too close.",
                "user_impact": "The layout feels cramped.",
                "recommendation": "Increase spacing.",
                "confidence": 80,
            },
            {
                "id": "F003",
                "principle": "Alignment",
                "severity": "low",
                "location": "footer links",
                "issue": "Footer items are off.",
                "user_impact": "The footer is inconsistent.",
                "recommendation": "Align items.",
                "confidence": 75,
            },
        ],
        "summary": "vague recs",
        "total_findings": 3,
        "critical_count": 0,
        "scan_duration_ms": 10,
    }

    def fake_analyze(self, b64, prompt, timeout=30):
        return json.dumps(sample)

    monkeypatch.setattr("design_audit_agent.models.gemini_client.GeminiClient.analyze_image", fake_analyze)
    try:
        analyze_screenshot(str(img_path), model="gemini")
        assert False, "Expected AnalysisError because recommendations are too vague"
    except AnalysisError as exc:
        assert "recommendation" in str(exc).lower()


def test_auto_repair_severity_and_drop_invalid_findings(monkeypatch, tmp_path):
    img_path = tmp_path / "repair.png"
    _write_png(img_path)

    sample = {
        "image_path": str(img_path),
        "model_used": "gemini",
        "findings": [
            {
                "id": "F001",
                "principle": "Contrast",
                "severity": "",
                "location": "hero CTA button",
                "issue": "Button text contrast is too low for readability.",
                "user_impact": "Users with low vision may not be able to read the button text.",
                "recommendation": "Increase text contrast to at least 4.5:1 against the background.",
                "confidence": None,
            },
            {
                "id": "F002",
                "principle": "Spacing",
                "severity": "UNKNOWN",
                "location": "card grid",
                "issue": "Cards are spaced too tightly and feel crowded.",
                "user_impact": "Users may have trouble scanning the content quickly.",
                "recommendation": "Add additional spacing between cards to improve readability.",
                "confidence": 55,
            },
            {
                "id": "F003",
                "principle": "",
                "severity": "medium",
                "location": "",
                "issue": "",
                "user_impact": "Users may miss important elements.",
                "recommendation": "Increase spacing in the header.",
                "confidence": 60,
            },
            {
                "id": "F004",
                "principle": "Alignment",
                "severity": "low",
                "location": "footer links",
                "issue": "Footer link text is misaligned with the label above.",
                "user_impact": "The footer appears visually inconsistent and less polished.",
                "recommendation": "Align footer links to the baseline grid used by the heading.",
                "confidence": 75,
            },
        ],
        "summary": "repair test",
        "total_findings": 4,
        "critical_count": 0,
        "scan_duration_ms": 10,
    }

    def fake_analyze(self, b64, prompt, timeout=30):
        return json.dumps(sample)

    monkeypatch.setattr("design_audit_agent.models.gemini_client.GeminiClient.analyze_image", fake_analyze)
    report = analyze_screenshot(str(img_path), model="gemini")

    assert report.total_findings == 3
    assert report.critical_count >= 1
    assert report.warning is not None
    assert "Dropped 1 invalid" in report.warning
    assert all(f.severity in {"critical", "high", "medium", "low", "info"} for f in report.findings)
    assert all(f.confidence is not None for f in report.findings)
