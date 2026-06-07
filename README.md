design-audit-agent
===================

What this agent does
- A CLI tool that analyzes screenshots for visual design issues (Level 1).
- Compares baseline and current screenshots to detect visual regressions (Level 2).

Architecture (ASCII)

agent/
  - auditor.py (level1)
  - comparator.py (level2)
models/
  - gemini_client.py
  - grok_client.py
main.py (CLI)

Setup (Windows)
1. Create and activate a Python 3.11+ virtualenv.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
```

2. Copy `.env.example` to `.env` and set `GEMINI_API_KEY` and/or `GROK_API_KEY`.
   If your provider requires a custom endpoint, also set `GEMINI_API_URL` or `GROK_API_URL`.

How to get API keys
- Gemini: https://cloud.google.com/ (search Gemini API console)
- Grok/xAI: https://console.groq.com/keys

Groq notes
- The Grok client now uses the OpenAI-compatible Groq endpoint by default.
- Default Grok vision model: `meta-llama/llama-4-scout-17b-16e-instruct`
- If your Groq project requires a different base URL, set `GROK_API_URL=https://api.groq.com/openai/v1`.

Usage examples
- Audit an image:
  `python main.py audit path/to/screenshot.png`
- Compare two images:
  `python main.py compare baseline.png current.png`
- Use mock mode for local CLI verification without real API access:
  `python main.py audit path/to/screenshot.png --mock`
  `python main.py compare baseline.png current.png --mock`
- Run the web UI locally:
  `python web.py`
  Then open `http://127.0.0.1:5000` in your browser.
  The UI supports drag-and-drop image upload for both Level 1 and Level 2 flows.
- Run a Level 3 autonomous scan:
  `python main.py scan --config path/to/scan-config.json`

Sample Level 3 scan config:
```json
{
  "mode": "compare",
  "page_name": "home",
  "baseline_url": "https://example.com/before",
  "current_url": "https://example.com/after",
  "model": "gemini",
  "mock": false,
  "viewport": { "width": 1280, "height": 900 }
}
```

Run tests
```powershell
pytest -q
```

Known limitations
- Level 3 autonomous browser scanning is not implemented in this build.
- Model clients are minimal wrappers; tests mock API calls.
