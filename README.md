# 🧠 Design Audit Agent

An AI-powered design auditing system that analyzes UI screenshots and web pages to detect visual issues, regressions, and usability problems using multimodal AI models.

---

# 🚀 What this project does

This system evaluates UI/UX designs across **three progressive intelligence levels**:

---

# 🟢 Level 1 — Design Audit (Single Image Analysis)

Analyzes a single screenshot and identifies:

- Layout issues
- Alignment problems
- Color contrast issues
- Accessibility concerns
- UI/UX improvements

### ▶️ Run command:

python main.py audit path/to/screenshot.png


🟡 Level 2 — Visual Regression Comparison

Compares two screenshots:

Baseline (before)
Current (after)

Detects:

UI regressions
Visual improvements
Layout shifts
Accessibility changes
Pixel-level differences
▶️ Run command:
python main.py compare baseline.png current.png


🔵 Level 3 — Autonomous UI Audit System

Fully automated pipeline that:

Loads configuration file
Opens URLs or test pages
Captures screenshots automatically
Compares baseline vs current states
Generates structured regression reports
▶️ Run command:
python main.py scan --config path/to/scan-config.json
📄 Example config (Level 3)
{
  "mode": "compare",
  "page_name": "home",
  "baseline_url": "https://example.com/before",
  "current_url": "https://example.com/after",
  "model": "gemini",
  "mock": false,
  "viewport": {
    "width": 1280,
    "height": 900
  }
}
🖥️ Web Interface (Flask UI)

Run the web app locally:

python web.py

Then open:

http://127.0.0.1:5000


⚙️ Setup Instructions
1. Create virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1
2. Install dependencies
pip install -r requirements.txt
3. Install browser automation tool
python -m playwright install chromium
4. Environment setup

Copy .env.example → .env

Add API keys:

GEMINI_API_KEY=your_key_here
GROK_API_KEY=your_key_here
🧪 Run Tests
pytest -q

👨‍💻 Author

Prahitha1605
