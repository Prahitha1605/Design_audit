"""Simple Grok (xAI) API wrapper."""
from __future__ import annotations

import base64
import json
import os
import requests

from design_audit_agent.config import settings


class GrokClient:
    """Minimal Grok client wrapper using the OpenAI-compatible Groq API."""

    ENDPOINT_ENV = "GROK_API_URL"
    DEFAULT_ENDPOINT = "https://api.groq.com/openai/v1"
    DEFAULT_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.grok_api_key
        self.endpoint = os.getenv(self.ENDPOINT_ENV, self.DEFAULT_ENDPOINT).rstrip("/")
        self.model = os.getenv("GROK_MODEL", settings.grok_model) or self.DEFAULT_MODEL

    def _build_image_element(self, image_path: str) -> dict[str, str]:
        if os.path.exists(image_path):
            with open(image_path, "rb") as f:
                encoded = base64.b64encode(f.read()).decode("utf-8")
            return {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{encoded}",
                },
            }

        if image_path.startswith("http://") or image_path.startswith("https://"):
            return {"type": "image_url", "image_url": {"url": image_path}}

        if image_path.startswith("data:image"):
            return {"type": "image_url", "image_url": {"url": image_path}}

        raise ValueError("Grok image input must be a local file path, remote URL, or data URI")

    def analyze_image(
        self,
        image_path: str,
        prompt: str,
        timeout: int = 30,
        second_image_path: str | None = None,
    ) -> str:
        if not self.api_key:
            raise RuntimeError("Grok API key not configured")

        url = self.endpoint
        if not url.endswith("/chat/completions") and not url.endswith("/responses"):
            url = f"{url}/chat/completions"

        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        content = [
            {"type": "text", "text": prompt},
            self._build_image_element(image_path),
        ]
        if second_image_path:
            content.append(self._build_image_element(second_image_path))

        messages = [
            {
                "role": "user",
                "content": content,
            }
        ]
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
        }

        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
            resp.raise_for_status()
        except requests.exceptions.HTTPError as exc:
            raise RuntimeError(
                f"Grok API HTTP error {resp.status_code}: {resp.text[:1000]}"
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise RuntimeError(f"Grok API request failed: {exc}") from exc

        body = resp.json()

        choices = body.get("choices") or []
        if not choices:
            raise RuntimeError("Grok API returned no choices")

        message = choices[0].get("message", {})
        content = message.get("content")
        if content is None:
            raise RuntimeError("Grok API response did not contain message content")

        if isinstance(content, (dict, list)):
            return json.dumps(content)
        return str(content)
