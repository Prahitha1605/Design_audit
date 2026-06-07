"""Level 3 autonomous browser agent.

This module uses Playwright to capture screenshots of configured pages and
runs Level 1 or Level 2 analysis automatically with baseline versioning.
"""
from __future__ import annotations

import json
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from PIL import Image, ImageChops
from playwright.sync_api import Page, sync_playwright

from design_audit_agent.agent.auditor import analyze_screenshot
from design_audit_agent.agent.logger import logger
from design_audit_agent.config import BASELINES_DIR, OUTPUT_DIR, REPORTS_DIR, settings

SCAN_UPLOAD_DIR = OUTPUT_DIR / "scan"
SCAN_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
BASELINES_DIR.mkdir(parents=True, exist_ok=True)

FIXED_VIEWPORT = {"width": 1440, "height": 900}
DEFAULT_VIEWPORT = FIXED_VIEWPORT.copy()
DEFAULT_DYNAMIC_SELECTORS = [
    ".spinner",
    ".loading",
    ".loader",
    "[data-testid*=spinner]",
    "[class*=loading]",
    "[class*=spinner]",
    "[aria-live]",
]


def _load_config(path: str) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(str(config_path))
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Scan config must be a JSON object")

    if "viewport" not in raw and ("viewport_width" in raw or "viewport_height" in raw):
        raw["viewport"] = {
            "width": int(raw.get("viewport_width", DEFAULT_VIEWPORT["width"])),
            "height": int(raw.get("viewport_height", DEFAULT_VIEWPORT["height"])),
        }
    return raw


def _normalize_scan_name(value: Any) -> str:
    if value is None:
        value = "design-audit"
    name = str(value)
    safe = "".join(ch if ch.isalnum() else "_" for ch in name).strip("_")
    return safe[:40] or "design_audit"


def _coerce_viewport(raw: Any) -> dict[str, int]:
    # Enforce a fixed viewport for reliable regression capture.
    return FIXED_VIEWPORT.copy()


def _sanitize_selectors(selectors: Any) -> list[str]:
    if not selectors:
        return []
    if isinstance(selectors, str):
        selectors = [selectors]
    return [str(selector).strip() for selector in selectors if str(selector).strip()]


def _validate_mode(mode: str) -> str:
    if not isinstance(mode, str):
        raise ValueError("Scan mode must be a string")
    mode = mode.lower().strip()
    if mode not in {"audit", "compare"}:
        raise ValueError("Scan mode must be either 'audit' or 'compare'")
    return mode


def _get_page_configs(config: dict[str, Any], mode: str) -> list[dict[str, Any]]:
    pages = config.get("pages")
    if isinstance(pages, list) and pages:
        if all(isinstance(page, dict) for page in pages):
            return pages
        if all(isinstance(page, str) for page in pages):
            base_url = str(config.get("base_url", "")).strip()
            if not base_url:
                raise ValueError("Legacy string page list requires top-level 'base_url'.")
            return [
                {
                    "name": _normalize_scan_name(page),
                    "url": urljoin(base_url.rstrip("/") + "/", page.lstrip("/")),
                    "hide_selectors": config.get("hide_selectors", []),
                }
                for page in pages
            ]
        raise ValueError("Scan pages must be a list of page objects or relative URL strings.")

    if mode == "audit":
        url = str(config.get("url", "")).strip()
        if not url:
            raise ValueError("Audit mode requires a top-level 'url' or a pages list.")
        return [{"name": config.get("page_name", "audit"), "url": url, "hide_selectors": config.get("hide_selectors", [])}]

    if mode == "compare":
        baseline_url = str(config.get("baseline_url", "")).strip()
        current_url = str(config.get("current_url", "")).strip()
        if not baseline_url or not current_url:
            raise ValueError("Compare mode requires top-level baseline_url and current_url or a pages list.")
        return [
            {
                "name": config.get("page_name", "compare"),
                "baseline_url": baseline_url,
                "current_url": current_url,
                "hide_selectors": config.get("hide_selectors", []),
            }
        ]

    raise ValueError("Unsupported mode")


def _login(page: Page, auth: dict[str, Any]) -> None:
    required = ["login_url", "username_field", "password_field", "username", "password", "submit_selector"]
    missing = [field for field in required if not auth.get(field)]
    if missing:
        raise ValueError(f"Auth config missing required fields: {', '.join(missing)}")

    page.goto(str(auth["login_url"]), wait_until="networkidle", timeout=int(settings.request_timeout * 1000))
    page.fill(str(auth["username_field"]), str(auth["username"]))
    page.fill(str(auth["password_field"]), str(auth["password"]))
    page.click(str(auth["submit_selector"]))
    page.wait_for_load_state("networkidle", timeout=int(settings.request_timeout * 1000))
    if auth.get("post_login_url_contains"):
        expected = str(auth["post_login_url_contains"])
        if expected not in page.url:
            raise RuntimeError(f"Login flow did not reach expected URL containing '{expected}'")


def _apply_dynamic_filters(page: Page, hide_selectors: list[str]) -> None:
    selectors = [*DEFAULT_DYNAMIC_SELECTORS, *hide_selectors]
    safe_selectors = [selector for selector in selectors if selector]
    if not safe_selectors:
        return
    page.evaluate(
        "selectors => {"
        "selectors.forEach(selector => {"
        "  document.querySelectorAll(selector).forEach(el => {"
        "    el.style.visibility='hidden'; el.style.opacity='0';"
        "  });"
        "});"
        "}",
        safe_selectors,
    )


def _disable_animations(page: Page) -> None:
    page.add_style_tag(
        content="*, *::before, *::after { animation: none !important; transition: none !important; scroll-behavior: auto !important; }"
    )


def _wait_for_no_loading(page: Page, hide_selectors: list[str]) -> None:
    selectors = [*DEFAULT_DYNAMIC_SELECTORS, *hide_selectors]
    for selector in selectors:
        if not selector:
            continue
        try:
            page.wait_for_selector(selector, state="hidden", timeout=800)
        except Exception:
            continue


def _capture_screenshot(page: Page, url: str, path: Path, viewport: dict[str, int], hide_selectors: list[str]) -> None:
    if not url or not isinstance(url, str):
        raise ValueError("A valid URL is required to capture a page")

    logger.info({"agent": "browser_agent", "action": "capture_start", "detail": url})
    page.goto(url, wait_until="networkidle", timeout=int(settings.request_timeout * 1000))
    page.wait_for_timeout(800)
    page.evaluate("() => window.scrollTo(0, 0)")
    _disable_animations(page)
    _apply_dynamic_filters(page, hide_selectors)
    _wait_for_no_loading(page, hide_selectors)
    page.wait_for_timeout(250)
    page.screenshot(path=str(path), full_page=True)
    logger.info({"agent": "browser_agent", "action": "capture_complete", "detail": str(path)})


def _baseline_directory(scan_name: str, page_name: str) -> Path:
    baseline_dir = BASELINES_DIR / scan_name / page_name
    baseline_dir.mkdir(parents=True, exist_ok=True)
    return baseline_dir


def _next_baseline_version(baseline_dir: Path) -> int:
    versions: list[int] = []
    for path in baseline_dir.glob("baseline_*.png"):
        name = path.stem
        parts = name.split("_", 1)
        if len(parts) == 2 and parts[1].isdigit():
            versions.append(int(parts[1]))
    return max(versions, default=0) + 1


def _save_versioned_baseline(source_path: Path, scan_name: str, page_name: str) -> dict[str, str]:
    baseline_dir = _baseline_directory(scan_name, page_name)
    version = _next_baseline_version(baseline_dir)
    version_path = baseline_dir / f"baseline_{version}.png"
    shutil.copy2(source_path, version_path)
    latest_path = baseline_dir / "baseline.png"
    if source_path.resolve() != latest_path.resolve():
        shutil.copy2(source_path, latest_path)
    return {"baseline_path": str(latest_path), "baseline_version": version, "baseline_history": str(version_path)}


def _load_latest_baseline(scan_name: str, page_name: str) -> Path | None:
    baseline_file = _baseline_directory(scan_name, page_name) / "baseline.png"
    return baseline_file if baseline_file.exists() else None


def _compute_image_diff(baseline_path: Path, current_path: Path) -> dict[str, Any]:
    baseline = Image.open(baseline_path).convert("RGB")
    current = Image.open(current_path).convert("RGB")

    if baseline.size != current.size:
        width = max(baseline.width, current.width)
        height = max(baseline.height, current.height)
        background = Image.new("RGB", (width, height), "white")
        padded_baseline = background.copy()
        padded_baseline.paste(baseline, (0, 0))
        padded_current = background.copy()
        padded_current.paste(current, (0, 0))
        baseline = padded_baseline
        current = padded_current

    diff = ImageChops.difference(baseline, current).convert("L")
    threshold = 10
    diff = diff.point(lambda x: 255 if x > threshold else 0)
    diff_pixels = sum(1 for pixel in diff.getdata() if pixel)
    total_pixels = baseline.width * baseline.height
    pixel_diff_pct = round(diff_pixels / total_pixels * 100, 2) if total_pixels else 0.0
    bbox = diff.getbbox()
    region = None
    if bbox:
        region = {"x": bbox[0], "y": bbox[1], "width": bbox[2] - bbox[0], "height": bbox[3] - bbox[1]}

    return {
        "baseline_size": list(baseline.size),
        "current_size": list(current.size),
        "pixel_diff_pct": pixel_diff_pct,
        "bbox": region,
        "layout_break": baseline.size != current.size,
        "diff_pixels": diff_pixels,
        "total_pixels": total_pixels,
    }


def _score_regression(diff: dict[str, Any]) -> dict[str, Any]:
    pixel_diff_pct = diff["pixel_diff_pct"]
    layout_break = diff["layout_break"]
    if layout_break or pixel_diff_pct >= 8:
        direction = "regression"
        ux_impact = "Likely a visible layout regression or broken rendering."
        confidence = min(95, max(75, int(pixel_diff_pct * 1.5)))
    elif pixel_diff_pct >= 3:
        direction = "neutral"
        ux_impact = "Visual change detected; review for possible regression."
        confidence = min(85, max(60, int(pixel_diff_pct * 2)))
    else:
        direction = "neutral"
        ux_impact = "Minor visual variation, likely dynamic content or noise."
        confidence = min(70, max(50, int(pixel_diff_pct * 2)))

    certainty_reason = (
        "Layout changed or screenshot dimensions differ."
        if layout_break
        else f"Pixel difference is {pixel_diff_pct}% of the page area."
    )
    return {
        "direction": direction,
        "ux_impact": ux_impact,
        "reasoning": certainty_reason,
        "confidence": confidence,
    }


def _build_page_finding(page_name: str, diff: dict[str, Any]) -> dict[str, Any]:
    scoring = _score_regression(diff)
    description = (
        "Layout break detected with differing screenshot dimensions."
        if diff["layout_break"]
        else f"Visual difference detected on {page_name}."
    )
    return {
        "id": f"R_{uuid.uuid4().hex[:8]}",
        "location": page_name,
        "what_changed": description,
        "direction": scoring["direction"],
        "reasoning": scoring["reasoning"],
        "ux_impact": scoring["ux_impact"],
        "is_accessibility_regression": scoring["direction"] == "regression",
        "confidence": scoring["confidence"],
        "evidence": {
            "pixel_diff_pct": diff["pixel_diff_pct"],
            "bbox": diff["bbox"],
            "baseline_size": diff["baseline_size"],
            "current_size": diff["current_size"],
        },
    }


def _capture_page_reports(
    page: Page,
    page_config: dict[str, Any],
    page_root: Path,
    scan_name: str,
    refresh_baseline: bool,
    mode: str,
) -> dict[str, Any]:
    name = _normalize_scan_name(page_config.get("name") or page_config.get("page_name") or page_config.get("url") or "page")
    hide_selectors = _sanitize_selectors(page_config.get("hide_selectors", []))
    viewport = _coerce_viewport(page_config.get("viewport") or {})
    page.set_viewport_size(viewport)
    report: dict[str, Any] = {}

    result: dict[str, Any] = {
        "page_name": name,
        "baseline_created": False,
        "baseline_refreshed": False,
        "baseline_version": None,
        "baseline_path": None,
        "current_path": None,
        "report": None,
    }

    if mode == "audit":
        url = str(page_config.get("url", "")).strip()
        if not url:
            raise ValueError("Audit page config must include 'url'.")
        current_path = page_root / f"{name}_audit_{uuid.uuid4().hex[:8]}.png"
        _capture_screenshot(page, url, current_path, viewport, hide_selectors)
        report = analyze_screenshot(str(current_path), model=page_config.get("model"), mock=bool(page_config.get("mock", False)))
        result.update({"current_path": str(current_path), "report": report.model_dump()})
        return result

    if mode == "compare":
        baseline_path = page_config.get("baseline_path")
        current_path = page_config.get("current_path")
        baseline_url = str(page_config.get("baseline_url", "")).strip()
        current_url = str(page_config.get("current_url", "")).strip()

        if not baseline_url and not baseline_path:
            raise ValueError("Compare page config requires 'baseline_url' or 'baseline_path'.")
        if not current_url and not current_path:
            raise ValueError("Compare page config requires 'current_url' or 'current_path'.")

        baseline_file = None
        if baseline_path:
            baseline_file = Path(baseline_path)
            if not baseline_file.exists() or refresh_baseline:
                if not baseline_url:
                    raise ValueError("Baseline path is missing or invalid and no baseline_url was provided.")
                baseline_file = page_root / f"{name}_baseline_{uuid.uuid4().hex[:8]}.png"
                _capture_screenshot(page, baseline_url, baseline_file, viewport, hide_selectors)
        else:
            baseline_file = _load_latest_baseline(scan_name, name)
            if not baseline_file or refresh_baseline:
                if not baseline_url:
                    raise ValueError("No existing baseline found and no baseline_url was provided.")
                baseline_file = page_root / f"{name}_baseline_{uuid.uuid4().hex[:8]}.png"
                _capture_screenshot(page, baseline_url, baseline_file, viewport, hide_selectors)

        if not baseline_file or not baseline_file.exists():
            raise RuntimeError("Baseline capture failed.")

        baseline_info = _save_versioned_baseline(baseline_file, scan_name, name)
        result.update(
            {
                "baseline_created": True,
                "baseline_refreshed": refresh_baseline,
                "baseline_version": baseline_info["baseline_version"],
                "baseline_path": baseline_info["baseline_path"],
            }
        )

        if current_path:
            current_file = Path(current_path)
            if not current_file.exists() or refresh_baseline:
                if not current_url:
                    raise ValueError("Current path is missing or invalid and no current_url was provided.")
                current_file = page_root / f"{name}_current_{uuid.uuid4().hex[:8]}.png"
                _capture_screenshot(page, current_url, current_file, viewport, hide_selectors)
        else:
            current_file = page_root / f"{name}_current_{uuid.uuid4().hex[:8]}.png"
            _capture_screenshot(page, current_url, current_file, viewport, hide_selectors)

        diff_data = _compute_image_diff(Path(result["baseline_path"]), current_file)
        finding = _build_page_finding(name, diff_data)
        comparison_report = {
            "scan_id": str(uuid.uuid4()),
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "baseline_path": str(result["baseline_path"]),
            "current_path": str(current_file),
            "model_used": page_config.get("model") or settings.default_model,
            "differences": [finding],
            "verdict": finding["direction"],
            "accessibility_regressions": 1 if finding["is_accessibility_regression"] else 0,
            "summary": f"{finding['reasoning']} (diff {diff_data['pixel_diff_pct']}%).",
            "scan_duration_ms": 0,
        }
        result.update({"current_path": str(current_file), "report": comparison_report})
        return result

    raise ValueError(f"Unsupported mode {mode}")


def run_autonomous_audit(config_path: str, refresh_baseline: bool = False) -> dict[str, Any]:
    config = _load_config(config_path)
    mode = _validate_mode(config.get("mode", "audit"))
    model = config.get("model")
    mock = bool(config.get("mock", False))
    scan_name = _normalize_scan_name(config.get("scan_name") or config.get("page_name") or mode)
    viewport = _coerce_viewport(config.get("viewport"))
    pages = _get_page_configs(config, mode)
    if mode == "compare" and len(pages) < 3:
        raise ValueError("Compare scan requires at least 3 pages for reliable regression validation.")
    auth = config.get("auth")
    hide_selectors = _sanitize_selectors(config.get("hide_selectors", []))

    auth_info = {
        "auth_status": "not_required",
        "auth_method": None,
        "session_persisted": False,
    }

    page_results: list[dict[str, Any]] = []
    start = time.time()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport=viewport)
        page = context.new_page()

        if auth:
            _login(page, auth)
            auth_info = {
                "auth_status": "success",
                "auth_method": "form_login",
                "session_persisted": True,
            }

        for page_config in pages:
            page_config["viewport"] = FIXED_VIEWPORT.copy()
            page_config["hide_selectors"] = _sanitize_selectors(page_config.get("hide_selectors", [])) or hide_selectors
            page_config["model"] = page_config.get("model", model)
            page_config["mock"] = page_config.get("mock", mock)
            result = _capture_page_reports(page, page_config, SCAN_UPLOAD_DIR, scan_name, refresh_baseline, mode)
            page_results.append(result)

        browser.close()

    duration_ms = int((time.time() - start) * 1000)
    regressions = sum(1 for entry in page_results if entry["report"] and entry["report"].get("verdict") == "regression")
    summary = (
        f"Completed {mode} scan for {len(page_results)} page(s). {regressions} regression(s) detected."
        if mode == "compare"
        else f"Completed {mode} audit for {len(page_results)} page(s)."
    )

    return {
        "scan_id": str(uuid.uuid4()),
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "scan_name": scan_name,
        "mode": mode,
        "model_used": model or settings.default_model,
        "mock": mock,
        "auth": auth_info,
        "viewport": FIXED_VIEWPORT.copy(),
        "scroll_lock": True,
        "baseline_store": str((BASELINES_DIR / scan_name).resolve()),
        "baseline_refresh": refresh_baseline,
        "pages": page_results,
        "summary": summary,
        "scan_duration_ms": duration_ms,
    }
