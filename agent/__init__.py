"""Agent package for design audits."""

from .auditor import analyze_screenshot
from .comparator import compare_screenshots

__all__ = ["analyze_screenshot", "compare_screenshots"]
