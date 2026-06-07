"""Configuration and constants for design-audit-agent."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
REPORTS_DIR = OUTPUT_DIR / "reports"
BASELINES_DIR = OUTPUT_DIR / "baselines"
LOG_FILE = OUTPUT_DIR / "agent.log"

SUPPORTED_IMAGE_EXT = {"png", "jpg", "jpeg", "webp"}


@dataclass
class Settings:
    gemini_api_key: str | None = os.getenv("GEMINI_API_KEY")
    grok_api_key: str | None = os.getenv("GROK_API_KEY")
    grok_model: str = os.getenv("GROK_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
    default_model: str = os.getenv("DEFAULT_MODEL", "gemini")
    request_timeout: int = int(os.getenv("REQUEST_TIMEOUT", "30"))


settings = Settings()
