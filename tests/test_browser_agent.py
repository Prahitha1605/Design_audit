import json
from pathlib import Path

from design_audit_agent.agent.browser_agent import run_autonomous_audit
from design_audit_agent.agent.validators import AuditReport


class DummyPage:
    def set_viewport_size(self, viewport):
        return None


class DummyBrowser:
    def new_context(self, viewport=None):
        return DummyContext()

    def close(self):
        return None


class DummyContext:
    def new_page(self):
        return DummyPage()

    def close(self):
        return None


class DummyChromium:
    def launch(self, headless=True):
        return DummyBrowser()


class DummyPlaywright:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    @property
    def chromium(self):
        return DummyChromium()


def _build_audit_report(path: Path) -> AuditReport:
    return AuditReport(
        image_path=str(path),
        model_used="mock",
        findings=[
            {
                "id": "F001",
                "principle": "Visual Hierarchy",
                "severity": "medium",
                "location": "hero section",
                "issue": "Button alignment is inconsistent.",
                "user_impact": "May reduce clarity.",
                "recommendation": "Align CTA buttons across the hero section.",
                "confidence": 85,
            }
        ],
        summary="Mock audit report.",
        total_findings=1,
        critical_count=0,
        scan_duration_ms=1,
    )


def test_run_autonomous_audit_in_audit_mode(monkeypatch, tmp_path):
    config = {
        "mode": "audit",
        "page_name": "home",
        "url": "https://example.com",
        "model": "gemini",
        "mock": True,
        "viewport": {"width": 800, "height": 600},
    }
    config_path = tmp_path / "scan-config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    monkeypatch.setattr("design_audit_agent.agent.browser_agent.sync_playwright", lambda: DummyPlaywright())

    def fake_capture(page, url, path, viewport, hide_selectors):
        path.write_bytes(b"PNG")
        return str(path)

    monkeypatch.setattr("design_audit_agent.agent.browser_agent._capture_screenshot", fake_capture)
    monkeypatch.setattr("design_audit_agent.agent.browser_agent.analyze_screenshot", lambda path, model=None, mock=False: _build_audit_report(Path(path)))

    result = run_autonomous_audit(str(config_path))
    assert result["mode"] == "audit"
    assert result["viewport"] == {"width": 1440, "height": 900}
    assert result["scroll_lock"] is True
    assert result["pages"][0]["page_name"] == "home"
    assert result["pages"][0]["current_path"].endswith(".png")
    assert result["pages"][0]["report"]["model_used"] == "mock"
    assert "summary" in result


def test_run_autonomous_audit_in_compare_mode(monkeypatch, tmp_path):
    config = {
        "mode": "compare",
        "pages": [
            {
                "name": "home",
                "baseline_url": "https://example.com/before",
                "current_url": "https://example.com/after",
            },
            {
                "name": "about",
                "baseline_url": "https://example.com/about/before",
                "current_url": "https://example.com/about/after",
            },
            {
                "name": "contact",
                "baseline_url": "https://example.com/contact/before",
                "current_url": "https://example.com/contact/after",
            },
        ],
        "model": "gemini",
        "mock": True,
    }
    config_path = tmp_path / "scan-config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    monkeypatch.setattr("design_audit_agent.agent.browser_agent.sync_playwright", lambda: DummyPlaywright())

    def fake_capture(page, url, path, viewport, hide_selectors):
        path.write_bytes(b"PNG")
        return str(path)

    monkeypatch.setattr("design_audit_agent.agent.browser_agent._capture_screenshot", fake_capture)
    monkeypatch.setattr(
        "design_audit_agent.agent.browser_agent._compute_image_diff",
        lambda baseline, current: {
            "baseline_size": [800, 600],
            "current_size": [800, 600],
            "pixel_diff_pct": 0.5,
            "bbox": None,
            "layout_break": False,
            "diff_pixels": 2400,
            "total_pixels": 480000,
        },
    )

    result = run_autonomous_audit(str(config_path))
    assert result["mode"] == "compare"
    assert result["viewport"] == {"width": 1440, "height": 900}
    assert len(result["pages"]) == 3
    for page in result["pages"]:
        assert page["baseline_path"].endswith("baseline.png")
        assert page["current_path"].endswith(".png")
        assert isinstance(page["baseline_version"], int)
        assert page["baseline_version"] >= 1
        assert page["report"]["verdict"] == "neutral"
    assert result["baseline_refresh"] is False


def test_run_autonomous_audit_with_auth(monkeypatch, tmp_path):
    config = {
        "mode": "audit",
        "scan_name": "protected_flow",
        "pages": [
            {"name": "protected", "url": "https://example.com/protected", "hide_selectors": [".timestamp"]}
        ],
        "auth": {
            "login_url": "https://example.com/login",
            "username_field": "#email",
            "password_field": "#password",
            "submit_selector": "button[type=submit]",
            "username": "user@example.com",
            "password": "password123",
            "post_login_url_contains": "/dashboard",
        },
        "mock": True,
    }
    config_path = tmp_path / "scan-config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    monkeypatch.setattr("design_audit_agent.agent.browser_agent.sync_playwright", lambda: DummyPlaywright())
    login_called = {"value": False}

    def fake_login(page, auth):
        login_called["value"] = True

    def fake_capture(page, url, path, viewport, hide_selectors):
        path.write_bytes(b"PNG")
        return str(path)

    monkeypatch.setattr("design_audit_agent.agent.browser_agent._login", fake_login)
    monkeypatch.setattr("design_audit_agent.agent.browser_agent._capture_screenshot", fake_capture)
    monkeypatch.setattr("design_audit_agent.agent.browser_agent.analyze_screenshot", lambda path, model=None, mock=False: _build_audit_report(Path(path)))

    result = run_autonomous_audit(str(config_path))
    assert login_called["value"]
    assert result["auth"]["auth_status"] == "success"
    assert result["auth"]["auth_method"] == "form_login"
    assert result["auth"]["session_persisted"] is True
    assert result["mode"] == "audit"
    assert result["pages"][0]["page_name"] == "protected"


def test_run_autonomous_audit_with_legacy_string_pages(monkeypatch, tmp_path):
    config = {
        "base_url": "https://example.com",
        "pages": ["/", "/about"],
        "viewport_width": 800,
        "viewport_height": 600,
        "mock": True,
    }
    config_path = tmp_path / "scan-config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    monkeypatch.setattr("design_audit_agent.agent.browser_agent.sync_playwright", lambda: DummyPlaywright())

    def fake_capture(page, url, path, viewport, hide_selectors):
        path.write_bytes(b"PNG")
        return str(path)

    monkeypatch.setattr("design_audit_agent.agent.browser_agent._capture_screenshot", fake_capture)
    monkeypatch.setattr("design_audit_agent.agent.browser_agent.analyze_screenshot", lambda path, model=None, mock=False: _build_audit_report(Path(path)))

    result = run_autonomous_audit(str(config_path))
    assert result["mode"] == "audit"
    assert result["viewport"] == {"width": 1440, "height": 900}
    assert result["scroll_lock"] is True
    assert result["pages"][0]["page_name"] == "design_audit"
    assert result["pages"][1]["page_name"] == "about"
    assert result["pages"][0]["report"]["model_used"] == "mock"
