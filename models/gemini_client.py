"""Official Gemini API wrapper using google-generativeai."""
from __future__ import annotations

import os
import time

import google.generativeai as genai
from google.api_core import exceptions as google_exceptions
from PIL import Image


class GeminiError(RuntimeError):
    pass


class GeminiQuotaError(GeminiError):
    pass


class GeminiClient:
    MAX_RETRIES = 3
    BACKOFF_FACTOR = 2

    def __init__(self) -> None:
        api_key = os.getenv("GEMINI_API_KEY")

        if not api_key:
            raise ValueError("Gemini API key not configured")

        model_name = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
        if model_name.startswith("models/"):
            model_name = model_name.split("/", 1)[1]

        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(model_name)

    @staticmethod
    def _extract_retry_delay(exc: Exception) -> int | None:
        if hasattr(exc, "retry_delay") and exc.retry_delay is not None:
            try:
                return int(exc.retry_delay)
            except Exception:
                pass
        return None

    def analyze_image(self, image_path: str, prompt: str, current_image_path: str | None = None) -> str:
        image = Image.open(image_path)
        media = [prompt, image]
        if current_image_path:
            current_image = Image.open(current_image_path)
            media.append(current_image)

        last_error: Exception | None = None
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                response = self.model.generate_content(media)
                return response.text
            except google_exceptions.ResourceExhausted as exc:
                last_error = exc
                retry_delay = self._extract_retry_delay(exc) or min(self.BACKOFF_FACTOR**attempt, 30)
                if attempt == self.MAX_RETRIES:
                    raise GeminiQuotaError(
                        "Gemini quota exceeded. Please wait and retry later, or switch to a different model."
                    ) from exc
                time.sleep(retry_delay)
            except google_exceptions.GoogleAPICallError as exc:
                raise GeminiError(f"Gemini API call failed: {exc}") from exc
            except Exception as exc:
                last_error = exc
                if attempt == self.MAX_RETRIES:
                    raise GeminiError(f"Gemini client error: {exc}") from exc
                time.sleep(min(self.BACKOFF_FACTOR**attempt, 30))

        raise GeminiError("Gemini analyze_image failed after retries") from last_error
