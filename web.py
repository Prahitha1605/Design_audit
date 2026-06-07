from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, send_from_directory, url_for
from werkzeug.utils import secure_filename

from design_audit_agent.agent.auditor import analyze_screenshot
from design_audit_agent.agent.browser_agent import run_autonomous_audit
from design_audit_agent.agent.comparator import compare_screenshots
from design_audit_agent.config import OUTPUT_DIR, REPORTS_DIR, SUPPORTED_IMAGE_EXT
from design_audit_agent.agent.logger import logger

UPLOAD_DIR = OUTPUT_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "design-audit-agent-secret")


def _allowed_file(filename: str, allowed_extensions: set[str]) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in allowed_extensions


def _save_upload(upload_field: str, prefix: str, allowed_extensions: set[str]) -> str:
    file_storage = request.files.get(upload_field)
    if file_storage is None or file_storage.filename == "":
        raise ValueError("No file selected")
    if not _allowed_file(file_storage.filename, allowed_extensions):
        raise ValueError("Unsupported file type")
    filename = secure_filename(file_storage.filename)
    path = UPLOAD_DIR / f"{prefix}_{uuid.uuid4().hex}_{filename}"
    file_storage.save(path)
    return str(path)


# ── Dashboard ──────────────────────────────────────────────────────────────
@app.route("/")
def index() -> str:
    return render_template("index.html")


# ── Level pages ────────────────────────────────────────────────────────────
@app.route("/level/1")
def level1() -> str:
    return render_template("level1.html")


@app.route("/level/2")
def level2() -> str:
    return render_template("level2.html")


@app.route("/level/3")
def level3() -> str:
    return render_template("level3.html")


# ── Form handlers ──────────────────────────────────────────────────────────
@app.route("/analyze", methods=["POST"])
def analyze() -> str:
    try:
        image_path = _save_upload("image", "level1", SUPPORTED_IMAGE_EXT)
        model = request.form.get("model") or None
        report = analyze_screenshot(image_path, model=model)
        payload = report.model_dump()
        return render_template(
            "result.html",
            title="Level 1 — Design Audit",
            summary=payload.get("summary"),
            details=payload,
            primary_label="findings",
        )
    except Exception as exc:
        logger.error({"agent": "web", "action": "analyze_failed", "detail": str(exc)})
        flash(str(exc), "danger")
        return redirect(url_for("level1"))


@app.route("/compare", methods=["POST"])
def compare() -> str:
    try:
        baseline_path = _save_upload("baseline", "baseline", SUPPORTED_IMAGE_EXT)
        current_path = _save_upload("current", "current", SUPPORTED_IMAGE_EXT)
        model = request.form.get("model") or None
        report = compare_screenshots(baseline_path, current_path, model=model)
        payload = report.model_dump()
        return render_template(
            "result.html",
            title="Level 2 — Regression Analysis",
            summary=payload.get("summary"),
            details=payload,
            primary_label="differences",
        )
    except Exception as exc:
        logger.error({"agent": "web", "action": "compare_failed", "detail": str(exc)})
        flash(str(exc), "danger")
        return redirect(url_for("level2"))


@app.route("/scan", methods=["POST"])
def scan() -> str:
    try:
        config_path = _save_upload("config", "scan", {"json"})
        refresh_baseline = request.form.get("refresh_baseline") == "on"
        result = run_autonomous_audit(config_path, refresh_baseline=refresh_baseline)
        return render_template(
            "result.html",
            title="Level 3 — Autonomous Scan",
            summary=result.get("summary", "Autonomous scan completed."),
            details=result,
            primary_label="scan_pages",
        )
    except Exception as exc:
        logger.error({"agent": "web", "action": "scan_failed", "detail": str(exc)})
        flash(str(exc), "danger")
        return redirect(url_for("level3"))


# ── Reports ────────────────────────────────────────────────────────────────
@app.route("/reports")
def reports() -> str:
    saved = sorted(REPORTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return render_template("reports.html", reports=saved)


@app.route("/reports/<path:filename>")
def report_file(filename: str):
    return send_from_directory(REPORTS_DIR, filename, as_attachment=False)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)