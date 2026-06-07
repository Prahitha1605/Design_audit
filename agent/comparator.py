"""Level 2: before/after regression analysis."""
from __future__ import annotations

import base64
import json
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw

from design_audit_agent.agent.logger import logger
from design_audit_agent.agent.prompts import LEVEL2_PROMPT
from design_audit_agent.agent.validators import validate_comparison_report, ComparisonReport
from design_audit_agent.config import SUPPORTED_IMAGE_EXT, REPORTS_DIR, settings


class ComparisonError(RuntimeError):
    pass


def _encode(path: Path) -> str:
    with path.open("rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _select_client(model_name: str | None, mock: bool = False):
    if mock or (model_name or "").strip().lower() == "mock":
        from design_audit_agent.models.mock_client import MockClient


        return MockClient()
    model_name = (model_name or settings.default_model).strip().lower()
    if model_name == "gemini":
        from design_audit_agent.models.gemini_client import GeminiClient


        return GeminiClient()
    if model_name == "grok":
        from design_audit_agent.models.grok_client import GrokClient


        return GrokClient()
    raise ValueError(f"Unknown model selected: {model_name}")


def _normalize_severity(value: Any) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"critical", "high", "medium", "low", "info"}:
            return normalized
        if normalized.isdigit():
            severity_score = int(normalized)
            if severity_score >= 5:
                return "high"
            if severity_score >= 3:
                return "medium"
            return "low"
    if isinstance(value, (int, float)):
        score = int(value)
        if score >= 5:
            return "high"
        if score >= 3:
            return "medium"
        return "low"
    return "medium"


def _normalize_summary_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        if not value:
            return ""
        if "summary" in value and isinstance(value["summary"], str):
            return value["summary"].strip()
        if "overall_change" in value and isinstance(value["overall_change"], str):
            parts = [value["overall_change"].strip()]
            if "visual_rating" in value:
                parts.append(f"Visual rating: {value['visual_rating']}")
            return " ".join(parts)
        if "text" in value and isinstance(value["text"], str):
            return value["text"].strip()
        if "description" in value and isinstance(value["description"], str):
            return value["description"].strip()
        return "; ".join(f"{k}: {v}" for k, v in value.items())
    if isinstance(value, list):
        if all(isinstance(item, str) for item in value):
            return " ".join(item).strip()
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _rgb_to_hex(color: tuple[int, int, int] | tuple[int, int, int, int]) -> str:
    return "#" + "".join(f"{int(c):02x}" for c in color[:3])


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    def channel(value: int) -> float:
        s = value / 255.0
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast_ratio(rgb1: tuple[int, int, int], rgb2: tuple[int, int, int]) -> float:
    lighter = max(_relative_luminance(rgb1), _relative_luminance(rgb2))
    darker = min(_relative_luminance(rgb1), _relative_luminance(rgb2))
    return round((lighter + 0.05) / (darker + 0.05), 2)


def _estimate_region_contrast(image: Image.Image) -> float | None:
    gray = image.convert("L")
    pixels = sorted(gray.getdata())
    if len(pixels) < 20:
        return None
    cutoff = max(2, len(pixels) // 10)
    dark = sum(pixels[:cutoff]) / cutoff
    light = sum(pixels[-cutoff:]) / cutoff
    if abs(light - dark) < 20:
        return None
    dark_rgb = (int(dark), int(dark), int(dark))
    light_rgb = (int(light), int(light), int(light))
    return _contrast_ratio(light_rgb, dark_rgb)


def _align_images_if_scroll(baseline: Image.Image, current: Image.Image) -> tuple[Image.Image, Image.Image, int, int]:
    if baseline.size != current.size:
        width = max(baseline.width, current.width)
        height = max(baseline.height, current.height)
        background = Image.new("RGB", (width, height), "white")
        padded_baseline = background.copy()
        padded_baseline.paste(baseline, (0, 0))
        padded_current = background.copy()
        padded_current.paste(current, (0, 0))
        return padded_baseline, padded_current, 0, 0

    width, height = baseline.size
    baseline_gray = baseline.convert("L")
    current_gray = current.convert("L")
    baseline_pixels = baseline_gray.load()
    current_pixels = current_gray.load()

    best_mean = None
    best_shift = 0
    max_shift = min(60, height // 8)
    for shift in range(-max_shift, max_shift + 1):
        total = 0
        count = 0
        for y in range(height):
            ty = y + shift
            if 0 <= ty < height:
                for x in range(width):
                    total += abs(baseline_pixels[x, y] - current_pixels[x, ty])
                    count += 1
        if count == 0:
            continue
        mean = total / count
        if best_mean is None or mean < best_mean:
            best_mean = mean
            best_shift = shift

    if best_shift != 0 and best_mean is not None:
        unshifted = float(
            sum(abs(baseline_pixels[x, y] - current_pixels[x, y]) for y in range(height) for x in range(width))
            / (width * height)
        )
        if best_mean <= unshifted * 0.75:
            aligned_current = ImageChops.offset(current, 0, best_shift)
            if best_shift > 0:
                aligned_current.paste("white", (0, 0, width, best_shift))
            elif best_shift < 0:
                aligned_current.paste("white", (0, height + best_shift, width, -best_shift))
            return baseline, aligned_current, best_shift, 0

    return baseline, current, 0, 0


def _diff_mask(baseline: Image.Image, current: Image.Image, threshold: int = 18) -> Image.Image:
    diff = ImageChops.difference(baseline, current).convert("L")
    return diff.point(lambda x: 255 if x > threshold else 0)


def _bbox_union(a: dict[str, int], b: dict[str, int]) -> dict[str, int]:
    x1 = min(a["x"], b["x"])
    y1 = min(a["y"], b["y"])
    x2 = max(a["x"] + a["width"], b["x"] + b["width"])
    y2 = max(a["y"] + a["height"], b["y"] + b["height"])
    return {"x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1}


def _boxes_are_close(a: dict[str, int], b: dict[str, int], gap: int) -> bool:
    return (
        a["x"] <= b["x"] + b["width"] + gap
        and b["x"] <= a["x"] + a["width"] + gap
        and a["y"] <= b["y"] + b["height"] + gap
        and b["y"] <= a["y"] + a["height"] + gap
    )


def _merge_nearby_regions(regions: list[dict[str, Any]], gap: int) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for region in regions:
        found = False
        for target in merged:
            if _boxes_are_close(target["bbox"], region["bbox"], gap):
                target["bbox"] = _bbox_union(target["bbox"], region["bbox"])
                target["pixel_count"] += region["pixel_count"]
                found = True
                break
        if not found:
            merged.append({"bbox": dict(region["bbox"]), "pixel_count": region["pixel_count"]})
    if len(merged) != len(regions):
        return _merge_nearby_regions(merged, gap)
    return merged


def _is_micro_region(region: dict[str, Any], min_pixels: int) -> bool:
    bbox = region["bbox"]
    area = bbox["width"] * bbox["height"]
    if bbox["width"] <= 8 and bbox["height"] <= 8:
        return True
    if region["pixel_count"] < min_pixels and area < 256:
        return True
    return False


def _find_zero_runs(values: list[int], min_run: int) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start = None
    for idx, value in enumerate(values):
        if value == 0:
            if start is None:
                start = idx
        elif start is not None:
            if idx - start >= min_run:
                runs.append((start, idx))
            start = None
    if start is not None and len(values) - start >= min_run:
        runs.append((start, len(values)))
    return runs


def _split_large_region(region: dict[str, Any], diff: Image.Image, min_gap: int = 24) -> list[dict[str, Any]]:
    bbox = region["bbox"]
    width, height = diff.size
    if bbox["width"] < width * 0.6 and bbox["height"] < height * 0.6:
        return [region]

    crop = diff.crop((bbox["x"], bbox["y"], bbox["x"] + bbox["width"], bbox["y"] + bbox["height"]))
    w, h = crop.size
    if w < min_gap * 2 and h < min_gap * 2:
        return [region]

    cols = [sum(1 for y in range(h) if crop.getpixel((x, y)) > 0) for x in range(w)]
    vertical_gaps = _find_zero_runs(cols, min_gap)
    if vertical_gaps:
        split = max(vertical_gaps, key=lambda gap: gap[1] - gap[0])
        left_bbox = {"x": bbox["x"], "y": bbox["y"], "width": split[0], "height": bbox["height"]}
        right_bbox = {"x": bbox["x"] + split[1], "y": bbox["y"], "width": bbox["width"] - split[1], "height": bbox["height"]}
        if left_bbox["width"] > 0 and right_bbox["width"] > 0:
            left_pixels = sum(1 for x in range(left_bbox["width"]) for y in range(h) if crop.getpixel((x, y)) > 0)
            right_pixels = sum(1 for x in range(right_bbox["x"] - bbox["x"], w) for y in range(h) if crop.getpixel((x, y)) > 0)
            return [
                {"bbox": left_bbox, "pixel_count": left_pixels},
                {"bbox": right_bbox, "pixel_count": right_pixels},
            ]

    rows = [sum(1 for x in range(w) if crop.getpixel((x, y)) > 0) for y in range(h)]
    horizontal_gaps = _find_zero_runs(rows, min_gap)
    if horizontal_gaps:
        split = max(horizontal_gaps, key=lambda gap: gap[1] - gap[0])
        top_bbox = {"x": bbox["x"], "y": bbox["y"], "width": bbox["width"], "height": split[0]}
        bottom_bbox = {"x": bbox["x"], "y": bbox["y"] + split[1], "width": bbox["width"], "height": bbox["height"] - split[1]}
        if top_bbox["height"] > 0 and bottom_bbox["height"] > 0:
            top_pixels = sum(1 for x in range(w) for y in range(top_bbox["height"]) if crop.getpixel((x, y)) > 0)
            bottom_pixels = sum(1 for x in range(w) for y in range(bottom_bbox["y"] - bbox["y"], h) if crop.getpixel((x, y)) > 0)
            return [
                {"bbox": top_bbox, "pixel_count": top_pixels},
                {"bbox": bottom_bbox, "pixel_count": bottom_pixels},
            ]

    return [region]


def _trim_regions(regions: list[dict[str, Any]], max_regions: int = 12) -> list[dict[str, Any]]:
    return regions[:max_regions]


def _extract_regions(diff: Image.Image) -> list[dict[str, Any]]:
    width, height = diff.size
    data = list(diff.getdata())
    visited = bytearray(width * height)
    regions: list[dict[str, Any]] = []
    min_pixels = max(100, (width * height) // 10000)

    for idx, value in enumerate(data):
        if value == 0 or visited[idx]:
            continue
        x = idx % width
        y = idx // width
        queue = deque([idx])
        visited[idx] = 1
        min_x = max_x = x
        min_y = max_y = y
        pixel_count = 0
        while queue:
            current_idx = queue.popleft()
            cx = current_idx % width
            cy = current_idx // width
            pixel_count += 1
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx = cx + dx
                ny = cy + dy
                if 0 <= nx < width and 0 <= ny < height:
                    neighbor = ny * width + nx
                    if data[neighbor] and not visited[neighbor]:
                        visited[neighbor] = 1
                        queue.append(neighbor)
                        min_x = min(min_x, nx)
                        max_x = max(max_x, nx)
                        min_y = min(min_y, ny)
                        max_y = max(max_y, ny)
        bbox = {
            "x": min_x,
            "y": min_y,
            "width": max_x - min_x + 1,
            "height": max_y - min_y + 1,
        }
        if pixel_count < min_pixels:
            continue
        if bbox["width"] >= width * 0.9 and bbox["height"] <= height * 0.12 and (
            bbox["y"] < height * 0.12 or bbox["y"] + bbox["height"] > height * 0.88
        ):
            continue
        regions.append({"bbox": bbox, "pixel_count": pixel_count})

    regions = [region for region in regions if not _is_micro_region(region, min_pixels)]
    regions = _merge_nearby_regions(regions, gap=max(8, min(width, height) // 120))
    split_regions: list[dict[str, Any]] = []
    for region in regions:
        split_regions.extend(_split_large_region(region, diff, min_gap=max(24, min(width, height) // 40)))
    split_regions.sort(key=lambda region: region["pixel_count"], reverse=True)
    return _trim_regions(split_regions, max_regions=12)


def _average_color(image: Image.Image) -> tuple[int, int, int]:
    pixels = list(image.convert("RGB").getdata())
    count = len(pixels)
    if count == 0:
        return (0, 0, 0)
    r = sum(p[0] for p in pixels) // count
    g = sum(p[1] for p in pixels) // count
    b = sum(p[2] for p in pixels) // count
    return (r, g, b)


def _save_visual_evidence(
    baseline: Image.Image,
    current: Image.Image,
    regions: list[dict[str, Any]],
) -> dict[str, str | None]:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    overlay_path = REPORTS_DIR / f"compare_overlay_{uuid.uuid4().hex}.png"
    overlay = current.copy().convert("RGBA")
    draw = ImageDraw.Draw(overlay, "RGBA")
    for region in regions:
        bbox = region["bbox"]
        draw.rectangle(
            [bbox["x"], bbox["y"], bbox["x"] + bbox["width"], bbox["y"] + bbox["height"]],
            outline=(255, 0, 0, 200),
            width=4,
        )
        draw.rectangle(
            [bbox["x"], bbox["y"], bbox["x"] + bbox["width"], bbox["y"] + bbox["height"]],
            fill=(255, 0, 0, 40),
        )
    overlay.save(overlay_path)

    if not regions:
        return {
            "diff_overlay_path": str(overlay_path),
            "baseline_crop_path": None,
            "current_crop_path": None,
        }

    first_region = regions[0]["bbox"]
    baseline_crop = baseline.crop(
        (first_region["x"], first_region["y"], first_region["x"] + first_region["width"], first_region["y"] + first_region["height"])
    )
    current_crop = current.crop(
        (first_region["x"], first_region["y"], first_region["x"] + first_region["width"], first_region["y"] + first_region["height"])
    )
    baseline_crop_path = REPORTS_DIR / f"baseline_crop_{uuid.uuid4().hex}.png"
    current_crop_path = REPORTS_DIR / f"current_crop_{uuid.uuid4().hex}.png"
    baseline_crop.save(baseline_crop_path)
    current_crop.save(current_crop_path)
    return {
        "diff_overlay_path": str(overlay_path),
        "baseline_crop_path": str(baseline_crop_path),
        "current_crop_path": str(current_crop_path),
    }


def _region_evidence(region: dict[str, Any], baseline: Image.Image, current: Image.Image, total_pixels: int) -> dict[str, Any]:
    bbox = region["bbox"]
    baseline_crop = baseline.crop(
        (bbox["x"], bbox["y"], bbox["x"] + bbox["width"], bbox["y"] + bbox["height"])
    )
    current_crop = current.crop(
        (bbox["x"], bbox["y"], bbox["x"] + bbox["width"], bbox["y"] + bbox["height"])
    )
    before_color = _rgb_to_hex(_average_color(baseline_crop))
    after_color = _rgb_to_hex(_average_color(current_crop))
    contrast_before = _estimate_region_contrast(baseline_crop)
    contrast_after = _estimate_region_contrast(current_crop)
    pixel_diff_percentage = (
        round(region["pixel_count"] / total_pixels * 100, 2)
        if region["pixel_count"]
        else None
    )

    return {
        "bbox": bbox,
        "pixel_diff_percentage": pixel_diff_percentage,
        "before_color": before_color,
        "after_color": after_color,
        "contrast_before": contrast_before,
        "contrast_after": contrast_after,
    }


def _infer_direction_and_impact(raw_item: dict[str, Any], evidence: dict[str, Any]) -> tuple[str, str, str, int]:
    classification = str(raw_item.get("classification", raw_item.get("direction", "neutral"))).lower()
    severity = _normalize_severity(raw_item.get("severity", raw_item.get("severity_score", "medium")))
    before_contrast = evidence.get("contrast_before")
    after_contrast = evidence.get("contrast_after")
    pixel_diff = evidence.get("pixel_diff_percentage", 0.0) or 0.0
    color_changed = evidence.get("before_color") != evidence.get("after_color")

    if classification == "improvement":
        direction = "improvement"
    elif classification == "regression":
        direction = "regression"
    else:
        if before_contrast and after_contrast and after_contrast + 1.0 < before_contrast:
            direction = "regression"
        elif pixel_diff >= 10 and not color_changed:
            direction = "regression"
        elif pixel_diff >= 7:
            direction = "neutral"
        elif color_changed and after_contrast and before_contrast and after_contrast > before_contrast:
            direction = "improvement"
        else:
            direction = "neutral"

    if direction == "regression":
        if before_contrast and after_contrast and after_contrast + 1.0 < before_contrast:
            ux_impact = (
                "Reduced contrast makes content harder to read for low-vision users and weakens visual hierarchy."
            )
        elif pixel_diff >= 15:
            ux_impact = (
                "A visible layout change may disrupt user flow and confuse returning users."
            )
        else:
            ux_impact = (
                "This change is likely a regression that should be reviewed for usability and accessibility."
            )
    elif direction == "improvement":
        ux_impact = (
            "This change improves readability or visual clarity compared to the baseline."
            if color_changed or (before_contrast and after_contrast and after_contrast > before_contrast)
            else "Visual presentation appears more refined in the current screenshot."
        )
    else:
        ux_impact = "This differs from the baseline but does not appear to be a strong regression."

    if direction == "regression":
        confidence = min(100, max(75, int(pixel_diff * 2 + (10 if before_contrast and after_contrast else 0))))
    elif direction == "improvement":
        confidence = min(95, max(70, int(pixel_diff * 1.5 + (5 if before_contrast and after_contrast and after_contrast > before_contrast else 0))))
    else:
        confidence = min(85, max(55, int(pixel_diff * 1.2)))

    if raw_item.get("confidence") is not None:
        try:
            confidence = int(raw_item.get("confidence"))
        except Exception:
            pass

    return direction, severity, ux_impact, confidence


def _build_difference(idx: int, raw_item: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    issue = str(raw_item.get("description", raw_item.get("issue", raw_item.get("finding", "")))).strip()
    if not issue:
        if evidence.get("before_color") and evidence.get("after_color") and evidence.get("before_color") != evidence.get("after_color"):
            issue = (
                f"Color changed from {evidence['before_color']} to {evidence['after_color']} in the affected region."
            )
        else:
            issue = "Visible region changed between baseline and current screenshots."
    location = str(raw_item.get("element", raw_item.get("location", ""))).strip() or "Page region"
    direction, severity, ux_impact, confidence = _infer_direction_and_impact(raw_item, evidence)
    reasoning_parts: list[str] = [issue]
    if evidence.get("contrast_before") is not None and evidence.get("contrast_after") is not None:
        reasoning_parts.append(
            f"Contrast changed from {evidence['contrast_before']} to {evidence['contrast_after']}.")
    if evidence.get("pixel_diff_percentage") is not None:
        reasoning_parts.append(f"Region contains {evidence['pixel_diff_percentage']}% pixel difference.")
    if evidence.get("before_color") and evidence.get("after_color") and evidence["before_color"] != evidence["after_color"]:
        reasoning_parts.append(
            f"Color shifted from {evidence['before_color']} to {evidence['after_color']}.")
    reasoning = " ".join(reasoning_parts).strip()
    if not reasoning:
        reasoning = issue

    before_color = raw_item.get("before_color") if raw_item.get("before_color") is not None else evidence.get("before_color")
    after_color = raw_item.get("after_color") if raw_item.get("after_color") is not None else evidence.get("after_color")
    contrast_before = raw_item.get("contrast_before") if raw_item.get("contrast_before") is not None else evidence.get("contrast_before")
    contrast_after = raw_item.get("contrast_after") if raw_item.get("contrast_after") is not None else evidence.get("contrast_after")
    pixel_diff_percentage = raw_item.get("pixel_diff_percentage") if raw_item.get("pixel_diff_percentage") is not None else evidence.get("pixel_diff_percentage")
    bbox = raw_item.get("bbox") if raw_item.get("bbox") is not None else evidence.get("bbox")
    ux_impact = str(raw_item.get("ux_impact", ux_impact)).strip() or ux_impact
    reasoning = str(raw_item.get("reasoning", reasoning)).strip() or reasoning
    is_accessibility_regression = raw_item.get("is_accessibility_regression")
    if is_accessibility_regression is None:
        is_accessibility_regression = direction == "regression" and contrast_after is not None and contrast_before is not None and contrast_after < contrast_before

    return {
        "id": str(raw_item.get("id", f"diff_{idx:03d}")),
        "location": location,
        "what_changed": issue,
        "direction": direction,
        "severity": severity,
        "reasoning": reasoning,
        "ux_impact": ux_impact,
        "is_accessibility_regression": bool(is_accessibility_regression),
        "confidence": confidence,
        "before_color": before_color,
        "after_color": after_color,
        "contrast_before": contrast_before,
        "contrast_after": contrast_after,
        "shift_x_px": raw_item.get("shift_x_px"),
        "shift_y_px": raw_item.get("shift_y_px"),
        "pixel_diff_percentage": pixel_diff_percentage,
        "bbox": bbox,
    }


def _normalize_issue_text(raw_item: dict[str, Any]) -> str:
    combined = []
    for key in ("issue", "description", "finding", "reasoning", "classification"):
        value = raw_item.get(key)
        if isinstance(value, str):
            combined.append(value.lower())
    return " ".join(combined)


def _should_exclude_difference(raw_item: dict[str, Any], evidence: dict[str, Any]) -> bool:
    confidence = raw_item.get("confidence")
    if confidence is not None:
        try:
            if int(confidence) < 60:
                return True
        except Exception:
            pass

    if evidence.get("pixel_diff_percentage") is not None:
        if evidence["pixel_diff_percentage"] < 0.5:
            return True

    bbox = evidence.get("bbox")
    if isinstance(bbox, dict) and bbox.get("width") is not None and bbox.get("height") is not None:
        if bbox["width"] <= 8 and bbox["height"] <= 8:
            return True

    issue_text = _normalize_issue_text(raw_item)
    if any(token in issue_text for token in ("contrast", "text", "font", "visibility", "wcag", "accessibility", "readability")):
        pixel_diff = evidence.get("pixel_diff_percentage")
        if pixel_diff is not None and pixel_diff < 4.0:
            return True

    return False


def _should_fallback_from_gemini(exc: Exception, model_name: str | None, mock: bool) -> bool:
    if mock:
        return False
    normalized = (model_name or settings.default_model).strip().lower()
    if normalized != "gemini":
        return False
    message = str(exc).lower()
    return (
        "consumer_suspended" in message
        or "permission denied" in message and "generativelanguage.googleapis.com" in message
        or "consumer 'api_key" in message
    )


def _fallback_model(model_name: str | None) -> tuple[str | None, bool]:
    if (model_name or settings.default_model).strip().lower() == "gemini":
        if settings.grok_api_key:
            return "grok", False
        return None, True
    return None, False


def _normalize_comparison_raw(
    raw: dict[str, Any],
    baseline_path: str,
    current_path: str,
    model_name: str,
    regions: list[dict[str, Any]],
    visual_evidence: dict[str, str | None],
) -> dict[str, Any]:
    normalized = raw.copy()
    normalized.setdefault("baseline_path", baseline_path)
    normalized.setdefault("current_path", current_path)
    normalized.setdefault("model_used", model_name)
    normalized.setdefault("scan_duration_ms", 0)

    baseline = Image.open(baseline_path).convert("RGB")
    current = Image.open(current_path).convert("RGB")
    total_pixels = baseline.width * baseline.height

    normalized.setdefault("baseline_size", [baseline.width, baseline.height])
    normalized.setdefault("current_size", [current.width, current.height])
    normalized.setdefault("diff_overlay_path", visual_evidence.get("diff_overlay_path"))
    normalized.setdefault("baseline_crop_path", visual_evidence.get("baseline_crop_path"))
    normalized.setdefault("current_crop_path", visual_evidence.get("current_crop_path"))

    raw_diffs: list[Any] = []
    normalized_sections: list[str | None] = []
    report_payload = normalized.pop("report", None)
    if isinstance(report_payload, dict):
        for key, value in report_payload.items():
            if key != "differences":
                normalized.setdefault(key, value)
        raw_diffs = report_payload.get("differences", [])
        normalized_sections = [None] * len(raw_diffs)
    elif isinstance(normalized.get("differences"), list):
        raw_diffs = normalized["differences"]
        normalized_sections = [None] * len(raw_diffs)

    screenshot_comparison = normalized.get("screenshot_comparison", {})
    if not raw_diffs and isinstance(screenshot_comparison, dict):
        section_items: list[tuple[dict[str, Any], str]] = []
        for section, items in screenshot_comparison.items():
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict):
                    section_items.append((item, section))
        raw_diffs = [item for item, _ in section_items]
        normalized_sections = [section for _, section in section_items]

    if not raw_diffs and regions:
        raw_diffs = [
            {"id": f"region_{idx+1}", "issue": "Visible UI component changed between baseline and current screenshots."}
            for idx in range(min(len(regions), 12))
        ]
        normalized_sections = [None] * len(raw_diffs)

    differences: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_diffs, start=1):
        if not isinstance(item, dict):
            continue
        section = normalized_sections[idx - 1] if idx - 1 < len(normalized_sections) else None
        item = dict(item)
        if section == "accessibility_issues":
            item.setdefault("classification", "regression")
            item.setdefault("is_accessibility_regression", True)
        elif section == "visual_regressions":
            item.setdefault("classification", "regression")

        if item.get("bbox"):
            evidence = _region_evidence({"bbox": item["bbox"], "pixel_count": 0}, baseline, current, total_pixels)
        elif idx - 1 < len(regions):
            evidence = _region_evidence(regions[idx - 1], baseline, current, total_pixels)
        elif regions:
            evidence = _region_evidence(regions[0], baseline, current, total_pixels)
        else:
            evidence = {
                "bbox": None,
                "pixel_diff_percentage": None,
                "before_color": None,
                "after_color": None,
                "contrast_before": None,
                "contrast_after": None,
            }
        if _should_exclude_difference(item, evidence):
            continue
        differences.append(_build_difference(idx, item, evidence))

    if len(differences) < 5 and regions:
        next_idx = len(differences) + 1
        for region in regions:
            if len(differences) >= 5:
                break
            raw_item = {
                "id": f"region_{next_idx:03d}",
                "issue": "Visible UI component changed between baseline and current screenshots.",
                "classification": "regression",
            }
            evidence = _region_evidence(region, baseline, current, total_pixels)
            if _should_exclude_difference(raw_item, evidence):
                continue
            differences.append(_build_difference(next_idx, raw_item, evidence))
            next_idx += 1

    if not differences:
        differences.append(
            {
                "id": "diff_001",
                "location": "page",
                "what_changed": "No measurable visual difference detected.",
                "direction": "neutral",
                "severity": "info",
                "reasoning": "Baseline and current screenshots are visually equivalent.",
                "ux_impact": "No regression detected.",
                "is_accessibility_regression": False,
                "confidence": 95,
                "before_color": None,
                "after_color": None,
                "contrast_before": None,
                "contrast_after": None,
                "shift_x_px": None,
                "shift_y_px": None,
                "pixel_diff_percentage": 0.0,
                "bbox": None,
            }
        )

    normalized["differences"] = differences

    regressions = sum(1 for d in differences if d.get("direction") == "regression")
    improvements = sum(1 for d in differences if d.get("direction") == "improvement")
    neutrals = sum(1 for d in differences if d.get("direction") == "neutral")
    accessibility_regressions = sum(1 for d in differences if d.get("is_accessibility_regression"))

    normalized.setdefault("accessibility_regressions", accessibility_regressions)
    normalized.setdefault(
        "verdict",
        "net_regression"
        if accessibility_regressions > 0 or regressions > improvements
        else "net_improvement"
        if improvements > regressions
        else "neutral",
    )
    weighted_regression = sum(
        {"critical": 4, "high": 3, "medium": 2, "low": 1}.get(str(d.get("severity", "medium")).lower(), 2)
        for d in differences
        if d.get("direction") == "regression"
    )
    weighted_improvement = sum(
        {"critical": 4, "high": 3, "medium": 2, "low": 1}.get(str(d.get("severity", "medium")).lower(), 2)
        for d in differences
        if d.get("direction") == "improvement"
    )
    summary = f"Detected {len(differences)} region(s): {regressions} regressions, {improvements} improvements, {neutrals} neutral."
    if accessibility_regressions:
        summary += f" Accessibility regressions detected in {accessibility_regressions} region(s)."
    if weighted_regression > weighted_improvement:
        summary += " Regressions outweigh improvements."
    elif weighted_improvement > weighted_regression:
        summary += " Improvements outweigh regressions."

    if normalized.get("summary") is not None:
        normalized["summary"] = _normalize_summary_value(normalized["summary"])
    else:
        normalized["summary"] = summary
    return normalized


def compare_screenshots(baseline_path: str, current_path: str, model: str | None = None, mock: bool = False, fallback: bool = False) -> ComparisonReport:
    start = time.time()
    b = Path(baseline_path)
    c = Path(current_path)
    for p in (b, c):
        if not p.exists():
            logger.error({"agent": "comparator", "action": "file_not_found", "detail": str(p)})
            raise FileNotFoundError(str(p))
        if p.suffix.lower().lstrip(".") not in SUPPORTED_IMAGE_EXT:
            logger.error({"agent": "comparator", "action": "unsupported_format", "detail": p.suffix})
            raise ValueError("Unsupported image format")

    baseline_image = Image.open(b).convert("RGB")
    current_image = Image.open(c).convert("RGB")
    baseline_image, current_image, shift_y, shift_x = _align_images_if_scroll(baseline_image, current_image)
    diff_mask = _diff_mask(baseline_image, current_image)
    regions = _extract_regions(diff_mask)
    visual_evidence = _save_visual_evidence(baseline_image, current_image, regions)

    client = _select_client(model, mock=mock)
    logger.info({"agent": "comparator", "action": "api_call", "detail": client.__class__.__name__})
    try:
        if client.__class__.__name__ == "GeminiClient":
            prompt = f"{LEVEL2_PROMPT} The current image path is {c}."
            resp_text = client.analyze_image(str(b), prompt, str(c))
        elif client.__class__.__name__ == "GrokClient":
            prompt = f"{LEVEL2_PROMPT} The second image is the current screenshot."
            resp_text = client.analyze_image(str(b), prompt, timeout=settings.request_timeout, second_image_path=str(c))
        else:
            payload = {"baseline": _encode(b), "current": _encode(c), "prompt": LEVEL2_PROMPT}
            resp_text = client.analyze_image(payload["baseline"], LEVEL2_PROMPT, timeout=settings.request_timeout)
    except Exception as exc:
        logger.error({"agent": "comparator", "action": "model_error", "detail": str(exc)})
        if not fallback and _should_fallback_from_gemini(exc, model, mock):
            fallback_model_name, fallback_mock = _fallback_model(model)
            if fallback_model_name or fallback_mock:
                logger.warning(
                    {
                        "agent": "comparator",
                        "action": "model_fallback",
                        "detail": f"Gemini suspended, falling back to {'mock' if fallback_mock else fallback_model_name}.",
                    }
                )
                if fallback_mock:
                    return compare_screenshots(baseline_path, current_path, model=None, mock=True, fallback=True)
                return compare_screenshots(baseline_path, current_path, model=fallback_model_name, mock=False, fallback=True)
        raise ComparisonError(
            f"Model API request failed: {exc}. Retry later, switch models, or use mock mode."
        ) from exc
    try:
        raw = json.loads(resp_text)
    except Exception:
        def _simple_repair(s: str) -> str:
            first = s.find("{")
            last = s.rfind("}")
            if first != -1 and last != -1 and last > first:
                return s[first:last+1]
            raise ValueError("cannot repair")

        try:
            raw = json.loads(_simple_repair(resp_text))
        except Exception:
            logger.error({"agent": "comparator", "action": "json_parse_failed"})
            raise ComparisonError("Failed to parse JSON from model response")

    raw = _normalize_comparison_raw(
        raw,
        str(b),
        str(c),
        (model or settings.default_model).lower(),
        regions,
        visual_evidence,
    )
    report = validate_comparison_report(raw)
    duration_ms = int((time.time() - start) * 1000)
    out = report.model_dump()
    out["scan_duration_ms"] = duration_ms

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORTS_DIR / f"compare_{out['scan_id']}.json"
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)

    logger.info({"agent": "comparator", "action": "report_saved", "detail": str(out_path), "duration_ms": duration_ms})
    return ComparisonReport(**out)



