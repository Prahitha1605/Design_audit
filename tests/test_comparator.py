import json
from pathlib import Path

from google.api_core.exceptions import ResourceExhausted
from PIL import Image, ImageDraw

from design_audit_agent.agent.comparator import ComparisonError, compare_screenshots
from design_audit_agent.config import settings


def _write_png(path: Path, color=(255, 255, 255)) -> None:
    img = Image.new("RGB", (100, 100), color=color)
    img.save(path)


def test_two_images_required(tmp_path):
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    _write_png(a)
    try:
        compare_screenshots(str(a), str(b))
        assert False
    except FileNotFoundError:
        pass


def test_verdict_produced(monkeypatch, tmp_path):
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    _write_png(a)
    _write_png(b, color=(240, 240, 240))

    sample = {
        "baseline_path": str(a),
        "current_path": str(b),
        "model_used": "gemini",
        "differences": [
            {
                "id": "D001",
                "location": "hero",
                "what_changed": "color #ffffff -> #f0f0f0",
                "direction": "neutral",
                "reasoning": "minor color shift",
                "ux_impact": "low",
                "is_accessibility_regression": False,
                "confidence": 80,
            }
        ] * 5,
        "verdict": "net_improvement",
        "accessibility_regressions": 0,
        "summary": "ok",
        "scan_duration_ms": 10,
    }

    def fake_analyze(self, *args, **kwargs):
        return json.dumps(sample)

    monkeypatch.setattr("design_audit_agent.models.gemini_client.GeminiClient.analyze_image", fake_analyze)
    report = compare_screenshots(str(a), str(b), model="gemini")
    assert report.verdict in {"net_improvement", "net_regression", "neutral"}


def test_grok_compare_uses_two_image_paths(monkeypatch, tmp_path):
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    _write_png(a)
    _write_png(b)

    sample = {
        "baseline_path": str(a),
        "current_path": str(b),
        "model_used": "grok",
        "differences": [
            {
                "id": "D001",
                "location": "hero",
                "what_changed": "color shift",
                "direction": "neutral",
                "reasoning": "minor change",
                "ux_impact": "low",
                "is_accessibility_regression": False,
                "confidence": 80,
            }
        ],
        "verdict": "neutral",
        "accessibility_regressions": 0,
        "summary": "ok",
        "scan_duration_ms": 10,
    }

    def fake_analyze(self, image_path, prompt, timeout=30, second_image_path=None):
        assert image_path == str(a)
        assert second_image_path == str(b)
        return json.dumps(sample)

    monkeypatch.setattr("design_audit_agent.models.grok_client.GrokClient.analyze_image", fake_analyze)
    report = compare_screenshots(str(a), str(b), model="grok")
    assert report.model_used == "grok"
    assert report.verdict == "neutral"


def test_gemini_quota_is_reported_as_comparison_error(monkeypatch, tmp_path):
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    _write_png(a)
    _write_png(b)

    def fake_analyze(self, *args, **kwargs):
        raise ResourceExhausted("Quota exceeded")

    monkeypatch.setattr("design_audit_agent.models.gemini_client.GeminiClient.analyze_image", fake_analyze)
    try:
        compare_screenshots(str(a), str(b), model="gemini")
        assert False, "Expected ComparisonError"
    except ComparisonError as exc:
        assert "quota" in str(exc).lower()


def test_accessibility_regression_flagged(monkeypatch, tmp_path):
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    _write_png(a)
    _write_png(b, color=(10, 10, 10))

    sample = {
        "baseline_path": str(a),
        "current_path": str(b),
        "model_used": "gemini",
        "differences": [
            {
                "id": "D001",
                "location": "body text",
                "what_changed": "contrast drop",
                "direction": "regression",
                "reasoning": "contrast ratio lowered",
                "ux_impact": "unreadable",
                "is_accessibility_regression": True,
                "confidence": 85,
            }
        ],
        "verdict": "net_regression",
        "accessibility_regressions": 1,
        "summary": "contrast issue",
        "scan_duration_ms": 10,
    }

    def fake_analyze(self, *args, **kwargs):
        return json.dumps(sample)

    monkeypatch.setattr("design_audit_agent.models.gemini_client.GeminiClient.analyze_image", fake_analyze)
    report = compare_screenshots(str(a), str(b), model="gemini")
    assert report.accessibility_regressions >= 1


def test_gemini_screenshot_comparison_normalizes(monkeypatch, tmp_path):
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    _write_png(a)
    _write_png(b, color=(240, 240, 240))

    sample = {
        "screenshot_comparison": {
            "visual_regressions": [
                {
                    "id": 1,
                    "element": "Header navigation",
                    "issue": "Header drops by 8px and pushes navigation layout.",
                    "severity": "medium",
                }
            ],
            "accessibility_issues": [
                {
                    "id": 2,
                    "element": "Sidebar links",
                    "issue": "Link contrast falls below WCAG AA.",
                    "severity": "high",
                }
            ],
        }
    }

    def fake_analyze(self, *args, **kwargs):
        return json.dumps(sample)

    monkeypatch.setattr("design_audit_agent.models.gemini_client.GeminiClient.analyze_image", fake_analyze)
    report = compare_screenshots(str(a), str(b), model="gemini")
    assert report.baseline_path == str(a)
    assert report.current_path == str(b)
    assert report.model_used == "gemini"
    assert len(report.differences) == 2
    assert report.differences[0].id == "1"
    assert report.differences[1].is_accessibility_regression is True


def test_gemini_report_wrapper_normalizes(monkeypatch, tmp_path):
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    _write_png(a)
    _write_png(b, color=(240, 240, 240))

    sample = {
        "report": {
            "overall_rating": 6,
            "differences": [
                {
                    "id": "diff_01",
                    "element": "Header nav",
                    "classification": "regression",
                    "severity_score": 4,
                    "description": "Contrast dropped on nav links.",
                }
            ],
        }
    }

    def fake_analyze(self, *args, **kwargs):
        return json.dumps(sample)

    monkeypatch.setattr("design_audit_agent.models.gemini_client.GeminiClient.analyze_image", fake_analyze)
    report = compare_screenshots(str(a), str(b), model="gemini")
    assert report.baseline_path == str(a)
    assert report.current_path == str(b)
    assert report.model_used == "gemini"
    assert len(report.differences) == 1
    assert report.differences[0].id == "diff_01"
    assert report.differences[0].direction == "regression"
    assert report.verdict == "net_regression"


def test_summary_dict_is_normalized(monkeypatch, tmp_path):
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    _write_png(a)
    _write_png(b, color=(240, 240, 240))

    sample = {
        "report": {
            "summary": {
                "overall_change": "The image shows a higher contrast layout.",
                "visual_rating": 4,
            },
            "differences": [
                {
                    "id": "diff_02",
                    "element": "Footer",
                    "classification": "improvement",
                    "severity_score": 2,
                    "description": "Footer spacing improved.",
                }
            ],
        }
    }

    def fake_analyze(self, *args, **kwargs):
        return json.dumps(sample)

    monkeypatch.setattr("design_audit_agent.models.gemini_client.GeminiClient.analyze_image", fake_analyze)
    report = compare_screenshots(str(a), str(b), model="gemini")
    assert isinstance(report.summary, str)
    assert "higher contrast" in report.summary
    assert report.differences[0].id == "diff_02"


def test_local_diff_evidence_in_fallback(monkeypatch, tmp_path):
    baseline = tmp_path / "baseline.png"
    current = tmp_path / "current.png"
    _write_png(baseline, color=(255, 255, 255))
    _write_png(current, color=(220, 220, 220))

    def fake_analyze(self, *args, **kwargs):
        return json.dumps({})

    monkeypatch.setattr("design_audit_agent.models.gemini_client.GeminiClient.analyze_image", fake_analyze)
    report = compare_screenshots(str(baseline), str(current), model="gemini")
    assert report.baseline_path == str(baseline)
    assert report.current_path == str(current)
    assert report.differences
    assert report.differences[0].pixel_diff_percentage is not None
    assert report.differences[0].bbox is not None or report.differences[0].pixel_diff_percentage == 0.0
    assert report.summary is not None


def test_micro_changes_are_ignored(monkeypatch, tmp_path):
    baseline = tmp_path / "baseline.png"
    current = tmp_path / "current.png"
    img = Image.new("RGB", (100, 100), color=(255, 255, 255))
    img.save(baseline)
    img2 = img.copy()
    draw = ImageDraw.Draw(img2)
    draw.rectangle([10, 10, 13, 13], fill=(0, 0, 0))
    img2.save(current)

    def fake_analyze(self, *args, **kwargs):
        return json.dumps({})

    monkeypatch.setattr("design_audit_agent.models.gemini_client.GeminiClient.analyze_image", fake_analyze)
    report = compare_screenshots(str(baseline), str(current), model="gemini")
    assert report.differences
    assert report.differences[0].direction == "neutral"
    assert "No measurable visual difference" in report.differences[0].what_changed


def test_multiple_ui_regions_detected(monkeypatch, tmp_path):
    baseline = tmp_path / "baseline.png"
    current = tmp_path / "current.png"
    img = Image.new("RGB", (300, 300), color=(255, 255, 255))
    img.save(baseline)
    img2 = img.copy()
    draw = ImageDraw.Draw(img2)
    for x, y in ((20, 20), (120, 20), (220, 20), (20, 120), (120, 120), (220, 120)):
        draw.rectangle([x, y, x + 40, y + 40], fill=(10, 10, 10))
    img2.save(current)

    def fake_analyze(self, *args, **kwargs):
        return json.dumps({})

    monkeypatch.setattr("design_audit_agent.models.gemini_client.GeminiClient.analyze_image", fake_analyze)
    report = compare_screenshots(str(baseline), str(current), model="gemini")
    assert 5 <= len(report.differences) <= 12
    assert all(diff.bbox is not None for diff in report.differences)
    assert all(diff.bbox["width"] < 260 and diff.bbox["height"] < 260 for diff in report.differences)


def test_gemini_suspension_falls_back_to_grok(monkeypatch, tmp_path):
    baseline = tmp_path / "baseline.png"
    current = tmp_path / "current.png"
    _write_png(baseline)
    _write_png(current, color=(240, 240, 240))

    def fake_analyze_gemini(self, *args, **kwargs):
        raise Exception(
            "Permission denied: Consumer 'api_key has been suspended."
        )

    def fake_analyze_grok(self, *args, **kwargs):
        return json.dumps(
            {
                "baseline_path": str(baseline),
                "current_path": str(current),
                "model_used": "grok",
                "differences": [],
                "verdict": "neutral",
                "accessibility_regressions": 0,
                "summary": "Fallback to Grok.",
                "scan_duration_ms": 10,
            }
        )

    monkeypatch.setattr("design_audit_agent.models.gemini_client.GeminiClient.analyze_image", fake_analyze_gemini)
    monkeypatch.setattr("design_audit_agent.models.grok_client.GrokClient.analyze_image", fake_analyze_grok)
    monkeypatch.setattr(settings, "grok_api_key", "dummy")

    report = compare_screenshots(str(baseline), str(current), model="gemini")
    assert report.model_used == "grok"
    assert report.summary == "Fallback to Grok."


def test_severity_and_evidence_fields_preserved(monkeypatch, tmp_path):
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    _write_png(a)
    _write_png(b, color=(240, 240, 240))

    sample = {
        "baseline_path": str(a),
        "current_path": str(b),
        "model_used": "gemini",
        "differences": [
            {
                "id": "D001",
                "location": "hero",
                "what_changed": "Button contrast dropped",
                "direction": "regression",
                "severity": "high",
                "reasoning": "Contrast ratio dropped below 4.5:1.",
                "ux_impact": "Button text is harder to read.",
                "is_accessibility_regression": True,
                "confidence": 92,
                "before_color": "#428bca",
                "after_color": "#7db5e8",
                "contrast_before": 5.6,
                "contrast_after": 2.8,
                "shift_y_px": 18,
                "pixel_diff_percentage": 6.3,
                "bbox": {"x": 10, "y": 20, "width": 100, "height": 40},
            }
        ],
        "verdict": "net_regression",
        "accessibility_regressions": 1,
        "summary": "Contrast regression detected.",
        "scan_duration_ms": 10,
    }

    def fake_analyze(self, *args, **kwargs):
        return json.dumps(sample)

    monkeypatch.setattr("design_audit_agent.models.gemini_client.GeminiClient.analyze_image", fake_analyze)
    report = compare_screenshots(str(a), str(b), model="gemini")
    diff = report.differences[0]
    assert diff.severity == "high"
    assert diff.before_color == "#428bca"
    assert diff.after_color == "#7db5e8"
    assert diff.contrast_before == 5.6
    assert diff.contrast_after == 2.8
    assert diff.shift_y_px == 18
    assert diff.pixel_diff_percentage == 6.3
    assert diff.bbox == {"x": 10, "y": 20, "width": 100, "height": 40}
