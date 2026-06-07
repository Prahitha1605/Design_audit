"""Mock model client for local CLI validation."""
from __future__ import annotations

import json

from design_audit_agent.agent.prompts import LEVEL1_PROMPT, LEVEL2_PROMPT


class MockClient:
    """Return canned JSON responses for audits and comparisons."""

    def analyze_image(self, image_path: str, prompt: str, timeout: int = 30) -> str:
        if LEVEL2_PROMPT in prompt:
            data = {
                "baseline_path": "baseline.png",
                "current_path": "current.png",
                "model_used": "mock",
                "differences": [
                    {
                        "id": "D001",
                        "location": "hero banner",
                        "what_changed": "button color #1A73E8 -> #0F62FE",
                        "direction": "improvement",
                        "reasoning": "higher contrast and clearer CTA",
                        "ux_impact": "easier to notice the action",
                        "is_accessibility_regression": False,
                        "confidence": 95,
                    },
                    {
                        "id": "D002",
                        "location": "body text",
                        "what_changed": "font size 16px -> 14px",
                        "direction": "regression",
                        "reasoning": "text size decreased for readability",
                        "ux_impact": "harder to read for some users",
                        "is_accessibility_regression": True,
                        "confidence": 90,
                    },
                    {
                        "id": "D003",
                        "location": "sidebar spacing",
                        "what_changed": "padding 24px -> 16px",
                        "direction": "regression",
                        "reasoning": "compressed layout increases cognitive load",
                        "ux_impact": "denser visual structure",
                        "is_accessibility_regression": True,
                        "confidence": 88,
                    },
                    {
                        "id": "D004",
                        "location": "navigation links",
                        "what_changed": "link spacing increased by 8px",
                        "direction": "improvement",
                        "reasoning": "links are easier to click",
                        "ux_impact": "better usability",
                        "is_accessibility_regression": False,
                        "confidence": 80,
                    },
                    {
                        "id": "D005",
                        "location": "footer contrast",
                        "what_changed": "text color #6B6B6B -> #3C3C3C",
                        "direction": "improvement",
                        "reasoning": "stronger contrast for secondary text",
                        "ux_impact": "easier scanning",
                        "is_accessibility_regression": False,
                        "confidence": 75,
                    },
                ],
                "verdict": "net_regression",
                "accessibility_regressions": 2,
                "summary": "The current version has useful visual improvements but introduces accessibility regressions in text size and spacing.",
                "scan_duration_ms": 45,
            }
            return json.dumps(data)

        data = {
            "image_path": "screenshot.png",
            "model_used": "mock",
            "findings": [
                {
                    "id": "F001",
                    "principle": "Contrast",
                    "severity": "high",
                    "location": "primary CTA button",
                    "issue": "White button text on #1A73E8 blue background fails WCAG AA contrast for body text.",
                    "user_impact": "Users with low vision may struggle to read the call-to-action label quickly.",
                    "recommendation": "Darken the CTA background to #174EA6 or use #FFFFFF text on a darker button color to meet 4.5:1 contrast.",
                    "confidence": 92,
                },
                {
                    "id": "F002",
                    "principle": "Spacing",
                    "severity": "medium",
                    "location": "card grid",
                    "issue": "Product cards are separated by only 8px, creating a cramped visual rhythm.",
                    "user_impact": "Dense card spacing makes it harder for users to scan individual products quickly.",
                    "recommendation": "Increase horizontal and vertical card gutters to at least 16px for clearer separation.",
                    "confidence": 85,
                },
                {
                    "id": "F003",
                    "principle": "Alignment",
                    "severity": "low",
                    "location": "footer link group",
                    "issue": "Footer links are not aligned with the copyright text baseline.",
                    "user_impact": "Misalignment reduces visual polish and makes the footer feel less organized.",
                    "recommendation": "Align footer link items to the same baseline as the copyright line.",
                    "confidence": 75,
                },
            ],
            "summary": "Mock audit produced three valid, visible findings with concrete recommendations.",
            "total_findings": 3,
            "critical_count": 0,
            "scan_duration_ms": 30,
        }
        return json.dumps(data)
