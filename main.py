"""CLI entry point for design-audit-agent."""
from __future__ import annotations

import argparse
import json
import sys

from design_audit_agent.agent.auditor import analyze_screenshot
from design_audit_agent.agent.browser_agent import run_autonomous_audit
from design_audit_agent.agent.comparator import compare_screenshots
from design_audit_agent.agent.logger import logger
from design_audit_agent.config import REPORTS_DIR


def _format_markdown_report(data: dict) -> str:
    lines: list[str] = ["# Audit Summary"]
    if "baseline_path" in data and "current_path" in data:
        lines.extend(
            [
                f"- **Baseline:** {data['baseline_path']}",
                f"- **Current:** {data['current_path']}",
                f"- **Verdict:** {data.get('verdict', 'unknown')}",
                f"- **Accessibility regressions:** {data.get('accessibility_regressions', 0)}",
                f"- **Scan duration:** {data.get('scan_duration_ms', 0)} ms",
            ]
        )
    else:
        lines.extend(
            [
                f"- **Image:** {data.get('image_path', '')}",
                f"- **Total findings:** {data.get('total_findings', 0)}",
                f"- **Critical issues:** {data.get('critical_count', 0)}",
                f"- **Scan duration:** {data.get('scan_duration_ms', 0)} ms",
            ]
        )
    lines.append(f"- **Summary:** {data.get('summary', '')}")

    details_key = "differences" if "differences" in data else "findings"
    items = data.get(details_key, [])
    if isinstance(items, list) and items:
        title = "## Top differences" if details_key == "differences" else "## Top findings"
        lines.append("")
        lines.append(title)
        for item in items[:3]:
            if not isinstance(item, dict):
                continue
            if details_key == "differences":
                location = item.get("location", "Unknown")
                what_changed = item.get("what_changed", "No description")
                direction = item.get("direction", "neutral")
                confidence = item.get("confidence", 0)
                lines.append(f"- **{location}**: {what_changed} ({direction}, confidence {confidence}%)")
            else:
                location = item.get("location", "Unknown")
                issue = item.get("issue", "No issue specified")
                severity = item.get("severity", "unknown")
                confidence = item.get("confidence", 0)
                lines.append(f"- **{location}**: {issue} ({severity}, confidence {confidence}%)")
    return "\n".join(lines)


def _print_output(data: dict, fmt: str) -> None:
    if fmt in ("json", "both"):
        print(json.dumps(data, indent=2))
    if fmt in ("markdown", "both"):
        print(_format_markdown_report(data))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="design-audit-agent")
    sub = parser.add_subparsers(dest="cmd")

    p_audit = sub.add_parser("audit")
    p_audit.add_argument("image_path")
    p_audit.add_argument("--output-format", choices=["json", "markdown", "both"], default="both")
    p_audit.add_argument("--model", choices=["gemini", "grok"], default=None)
    p_audit.add_argument("--mock", action="store_true", help="Use local mock model responses")

    p_compare = sub.add_parser("compare")
    p_compare.add_argument("baseline")
    p_compare.add_argument("current")
    p_compare.add_argument("--output-format", choices=["json", "markdown", "both"], default="both")
    p_compare.add_argument("--model", choices=["gemini", "grok"], default=None)
    p_compare.add_argument("--mock", action="store_true", help="Use local mock model responses")

    p_scan = sub.add_parser("scan")
    p_scan.add_argument("--config", required=True)
    p_scan.add_argument("--refresh-baseline", action="store_true")

    args = parser.parse_args(argv)
    if args.cmd == "audit":
        try:
            report = analyze_screenshot(args.image_path, model=args.model, mock=args.mock)
        except Exception as e:
            logger.error({"agent": "cli", "action": "audit_failed", "detail": str(e)})
            print(str(e), file=sys.stderr)
            return 1
        data = report.model_dump()
        _print_output(data, args.output_format)
        return 0

    if args.cmd == "compare":
        try:
            report = compare_screenshots(args.baseline, args.current, model=args.model, mock=args.mock)
        except Exception as e:
            logger.error({"agent": "cli", "action": "compare_failed", "detail": str(e)})
            print(str(e), file=sys.stderr)
            return 1
        data = report.model_dump()
        _print_output(data, args.output_format)
        return 0

    if args.cmd == "scan":
        try:
            result = run_autonomous_audit(args.config, refresh_baseline=args.refresh_baseline)
        except Exception as e:
            logger.error({"agent": "cli", "action": "scan_failed", "detail": str(e)})
            print(str(e), file=sys.stderr)
            return 1
        print(json.dumps(result, indent=2))
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
