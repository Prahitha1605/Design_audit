"""Level 1: single image design analysis."""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any

from PIL import Image
from pydantic import ValidationError

from design_audit_agent.agent.logger import logger
from design_audit_agent.agent.prompts import LEVEL1_PROMPT, LEVEL1_RETRY_PROMPT
from design_audit_agent.agent.validators import AuditReport, Finding, validate_audit_report
from design_audit_agent.config import SUPPORTED_IMAGE_EXT, REPORTS_DIR, settings


class AnalysisError(RuntimeError):
    pass


def _load_image_as_base64(path: Path) -> str:
    img = Image.open(path)
    with path.open("rb") as f:
        data = f.read()
    return base64.b64encode(data).decode("utf-8")


ALLOWED_SEVERITIES = {"critical", "high", "medium", "low", "info"}


def _normalize_severity(value: Any) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ALLOWED_SEVERITIES:
            return normalized
    return ""


def _infer_severity_from_issue(finding: dict[str, Any]) -> str:
    text = " ".join(str(finding.get(field, "")) for field in ("issue", "principle")).lower()
    if any(keyword in text for keyword in ("contrast", "unreadable", "wcag")):
        return "critical"
    if any(keyword in text for keyword in ("truncated", "hierarchy")):
        return "high"
    if any(keyword in text for keyword in ("alignment", "spacing")):
        return "medium"
    return "low"


def _normalize_audit_raw(raw: dict[str, Any], image_path: str, model_name: str) -> dict[str, Any]:
    normalized = raw.copy()
    normalized.setdefault("image_path", image_path)
    normalized.setdefault("model_used", model_name)

    findings = normalized.get("findings", [])
    if isinstance(findings, list):
        normalized_findings: list[dict[str, Any]] = []
        for idx, finding in enumerate(findings, start=1):
            if not isinstance(finding, dict):
                continue
            confidence = finding.get("confidence")
            if confidence is None:
                confidence_value = None
            else:
                try:
                    confidence_value = int(float(confidence))
                except Exception:
                    confidence_value = None

            severity_value = _normalize_severity(
                finding.get("severity", finding.get("level", ""))
            )
            if not severity_value:
                severity_value = _infer_severity_from_issue(finding)

            normalized_findings.append(
                {
                    "id": str(finding.get("id", f"F{idx:03d}")),
                    "principle": str(finding.get("principle", finding.get("Principle", ""))).strip(),
                    "severity": severity_value,
                    "location": str(finding.get("location", finding.get("element", finding.get("target", "")))).strip(),
                    "issue": str(finding.get("issue", finding.get("finding", finding.get("problem", "")))).strip(),
                    "user_impact": str(finding.get("user_impact", finding.get("impact", ""))).strip(),
                    "recommendation": str(
                        finding.get("recommendation", finding.get("fix", finding.get("suggested_fix", "")))
                    ).strip(),
                    "confidence": confidence_value,
                }
            )
        normalized["findings"] = normalized_findings

    normalized.setdefault("summary", raw.get("summary", f"Found {len(normalized.get('findings', []))} findings."))
    normalized.setdefault("total_findings", len(normalized.get("findings", [])))
    normalized.setdefault(
        "critical_count",
        sum(1 for f in normalized.get("findings", []) if str(f.get("severity", "")).lower() == "critical"),
    )
    normalized.setdefault("scan_duration_ms", 0)
    return normalized


def _repair_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    repaired: list[dict[str, Any]] = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        finding.setdefault("recommendation", "")
        finding.setdefault("user_impact", "")
        finding.setdefault("confidence", None)
        finding.setdefault("severity", "")

        if not finding["severity"] or finding["severity"] not in ALLOWED_SEVERITIES:
            finding["severity"] = _infer_severity_from_issue(finding)

        finding["severity"] = _normalize_severity(finding["severity"]) or _infer_severity_from_issue(finding)
        finding["recommendation"] = finding["recommendation"].strip() or "Review and improve the affected UI element."
        finding["user_impact"] = finding["user_impact"].strip() or "This issue may negatively affect usability."
        if finding["confidence"] is None:
            finding["confidence"] = 70

        repaired.append(finding)
    return repaired


def _filter_valid_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    valid: list[dict[str, Any]] = []
    for finding in findings:
        try:
            Finding.model_validate(finding)
        except ValidationError:
            continue
        valid.append(finding)
    return valid


def _select_client(model_name: str | None, mock: bool = False):
    if mock:
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


def analyze_screenshot(image_path: str, model: str | None = None, mock: bool = False) -> AuditReport:
    """Analyze a single screenshot and return an `AuditReport` instance."""
    start = time.time()
    path = Path(image_path)
    logger.info({"agent": "auditor", "action": "load_image", "detail": str(path)})
    if not path.exists():
        logger.error({"agent": "auditor", "action": "file_not_found", "detail": str(path)})
        raise FileNotFoundError(str(path))
    if path.suffix.lower().lstrip(".") not in SUPPORTED_IMAGE_EXT:
        logger.error({"agent": "auditor", "action": "unsupported_format", "detail": path.suffix})
        raise ValueError("Unsupported image format. Supported: png,jpg,jpeg,webp")

    b64 = _load_image_as_base64(path)
    client = _select_client(model, mock=mock)

    prompt = LEVEL1_PROMPT
    logger.info({"agent": "auditor", "action": "api_call", "detail": client.__class__.__name__})
    try_count = 0
    raw_json = None
    while try_count < 3:
        try_count += 1
        try:
            if client.__class__.__name__ == "GeminiClient":
                resp_text = client.analyze_image(str(path), prompt)
            else:
                resp_text = client.analyze_image(str(path), prompt, timeout=settings.request_timeout)
        except Exception as exc:
            logger.error({"agent": "auditor", "action": "model_error", "detail": str(exc)})
            raise AnalysisError(
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
                repaired = _simple_repair(resp_text)
                raw = json.loads(repaired)
            except Exception:
                logger.error({"agent": "auditor", "action": "json_parse_failed", "detail": resp_text[:200]})
                raise AnalysisError("Failed to parse JSON from model response")

        raw = _normalize_audit_raw(raw, str(path), (model or settings.default_model).lower())
        repaired_findings = _repair_findings(raw.get("findings", []))
        valid_findings = _filter_valid_findings(repaired_findings)
        raw["findings"] = valid_findings
        raw["total_findings"] = len(valid_findings)
        raw["critical_count"] = sum(1 for f in valid_findings if str(f.get("severity", "")).lower() == "critical")
        if not raw.get("summary") or raw["summary"].startswith("Found "):
            raw["summary"] = f"Found {len(valid_findings)} valid findings."

        if len(valid_findings) < len(repaired_findings):
            raw["warning"] = (
                f"Dropped {len(repaired_findings) - len(valid_findings)} invalid or incomplete findings."
            )

        # Validate structure
        try:
            report = validate_audit_report(raw)
        except Exception as e:
            logger.warning({"agent": "auditor", "action": "validation_failed", "detail": str(e)})
            if try_count == 1:
                prompt = LEVEL1_RETRY_PROMPT
                continue
            if try_count == 2:
                prompt = LEVEL1_RETRY_PROMPT
                continue
            raise AnalysisError(f"Validation failed: {e}") from e

        if len(report.findings) < 3:
            logger.warning({"agent": "auditor", "action": "too_few_findings", "detail": len(report.findings)})
            if try_count == 1:
                prompt = LEVEL1_RETRY_PROMPT
                continue
            raise AnalysisError("Validation failed: fewer than 3 findings returned by the model")

        raw_json = report
        break

    if raw_json is None:
        raise AnalysisError("Analysis failed after retries")

    duration_ms = int((time.time() - start) * 1000)
    out = raw_json.model_dump()
    out["scan_duration_ms"] = duration_ms

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORTS_DIR / f"audit_{out['scan_id']}.json"
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)

    logger.info({"agent": "auditor", "action": "report_saved", "detail": str(out_path), "duration_ms": duration_ms})
    return AuditReport(**out)
