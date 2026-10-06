"""Paths and settings. Model settings can be overridden with environment variables or CLI flags."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"
REPO_DIR = FIXTURES / "incidents"  # the tiny "repository" under investigation
EVENT_FILES = [REPO_DIR / "events.json", FIXTURES / "additions" / "extra-events.json"]
EXTRA_INPUTS_FILE = FIXTURES / "additions" / "extra-inputs.json"
FROZEN_MANIFEST = FIXTURES / "frozen.json"
SYSTEM_PROMPT_FILE = ROOT / "prompts" / "system.md"
RUNS_DIR = ROOT / "runs"

# Files the model sees as context but may never patch.
PROTECTED_FILES = {"check.py", "reference-cases.json", "events.json", "domain.md"}

CHECK_TIMEOUT_S = 30

MODEL = os.environ.get("INCIDENT_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("INCIDENT_EFFORT", "medium")
MAX_TOKENS = 16000
API_TIMEOUT_S = 300
# Server-side refusal fallback (beta); set INCIDENT_FALLBACKS=0 to send a plain request.
USE_FALLBACKS = os.environ.get("INCIDENT_FALLBACKS", "1") != "0"
