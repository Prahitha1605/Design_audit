"""Pydantic schemas and validation helpers for reports."""
from __future__ import annotations

import re
import re
from datetime import datetime
from enum import Enum
from typing import List, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, conint, constr


class SeverityEnum(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    info = "info"


class Finding(BaseModel):
    id: constr(min_length=1)
    principle: constr(min_length=1)
    severity: SeverityEnum
    location: constr(min_length=1)
    issue: constr(min_length=1)
    user_impact: constr(min_length=5)
    recommendation: constr(min_length=15)
    confidence: conint(ge=0, le=100)
    visibility_status: Literal["visible", "partially_visible", "not_observable"] = "visible"

    model_config = ConfigDict(str_strip_whitespace=True)


class AuditReport(BaseModel):
    scan_id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    image_path: str
    model_used: str
    findings: List[Finding]
    summary: str
    total_findings: int
    critical_count: int
    scan_duration_ms: int
    warning: str | None = None
    low_confidence: bool | None = None


class Difference(BaseModel):
    id: str
    location: str
    what_changed: str
    direction: str
    severity: str | None = None
    reasoning: str
    ux_impact: str
    is_accessibility_regression: bool
    confidence: conint(ge=0, le=100)
    before_color: str | None = None
    after_color: str | None = None
    contrast_before: float | None = None
    contrast_after: float | None = None
    shift_x_px: int | None = None
    shift_y_px: int | None = None
    pixel_diff_percentage: float | None = None
    bbox: dict[str, int] | None = None


class ComparisonReport(BaseModel):
    scan_id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    baseline_path: str
    current_path: str
    model_used: str
    differences: List[Difference]
    verdict: str
    accessibility_regressions: int
    summary: str
    scan_duration_ms: int
    baseline_size: list[int] | None = None
    current_size: list[int] | None = None
    pixel_diff_pct: float | None = None
    diff_pixels: int | None = None
    diff_bbox: dict[str, int] | None = None
    diff_overlay_path: str | None = None
    baseline_crop_path: str | None = None
    current_crop_path: str | None = None


def validate_audit_report(raw: dict) -> AuditReport:
    try:
        report = AuditReport(**raw)
    except ValidationError as exc:
        messages = []
        for err in exc.errors():
            loc = ".".join(str(part) for part in err.get("loc", []))
            messages.append(f"{loc}: {err.get('msg')}")
        raise ValueError("Audit report validation failed: " + "; ".join(messages)) from exc

    if report.total_findings != len(report.findings):
        raise ValueError("total_findings must equal the number of findings")

    severity_values = {"critical", "high", "medium", "low", "info"}
    generic_locations = {"the page", "page", "throughout", "entire page", "site"}
    vague_recommendations = {
        "improve readability",
        "fix spacing",
        "adjust padding",
        "align items",
        "increase contrast",
        "make it better",
        "enhance usability",
    }
    hidden_state_patterns = [
        re.compile(r"\bhover (?:state|states|effect|effects|style|styles)\b", re.I),
        re.compile(r"\b(on|when) hover(?:ed|ing)?\b", re.I),
        re.compile(r"\bfocus (?:state|states|ring|outline|styles?)\b", re.I),
        re.compile(r"\b(on|when) focus(?:ed)?\b", re.I),
        re.compile(r"\b(on|when) click(?:ed)?\b", re.I),
        re.compile(r"\b(on|when) tap(?:ped)?\b", re.I),
        re.compile(r"\b(on|when) scroll(?:ed|ing)?\b", re.I),
        re.compile(r"\bkeyboard (?:navigation|focus|interaction)\b", re.I),
        re.compile(r"\b(?:dropdown|modal|accordion)\b", re.I),
        re.compile(r"\bexpand(?:ed|ing)?\b", re.I),
        re.compile(r"\bcollaps(?:ed|ing|e)?\b", re.I),
        re.compile(r"\bactivated\b", re.I),
    ]

    def _contains_non_visible_interaction(text: str) -> bool:
        text = text or ""
        return any(pattern.search(text) for pattern in hidden_state_patterns)

    locations = set()
    for f in report.findings:
        if f.severity.lower() not in severity_values:
            raise ValueError(f"Invalid severity: {f.severity}")
        if f.location.strip().lower() in generic_locations:
            raise ValueError("Generic location not allowed")
        if f.recommendation.strip().lower() in vague_recommendations:
            raise ValueError("Recommendation is too vague")

        issue_hidden = _contains_non_visible_interaction(f.issue)
        recommendation_hidden = _contains_non_visible_interaction(f.recommendation)
        if issue_hidden or recommendation_hidden:
            f.visibility_status = "not_observable"
            if issue_hidden:
                f.issue = "The issue is based on interface behavior not directly visible in the screenshot."
            if recommendation_hidden:
                f.recommendation = (
                    "A static screenshot does not provide enough visible evidence to make a concrete recommendation "
                    "about this interaction state."
                )

        if len(f.user_impact.strip()) < 15:
            raise ValueError("user_impact must explain real user impact")
        if len(f.recommendation.strip()) < 15:
            raise ValueError("recommendation must be concrete and actionable")
        key = (f.principle.strip().lower(), f.location.strip().lower())
        if key in locations:
            raise ValueError("Duplicate finding principle+location")
        locations.add(key)
        if f.confidence < 10:
            raise ValueError("Finding confidence too low")

    if report.critical_count > 0 and all(f.confidence < 50 for f in report.findings):
        report_dict = report.model_dump()
        report_dict["low_confidence"] = True
        return AuditReport(**report_dict)
    return report


def validate_comparison_report(raw: dict) -> ComparisonReport:
    try:
        report = ComparisonReport(**raw)
    except ValidationError:
        raise
    return report
