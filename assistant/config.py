"""Paths and settings.

Model settings can be overridden with environment variables or CLI flags.
"""
import os
from pathlib import Path

#: Repository root.
ROOT = Path(__file__).resolve().parent.parent
#: Folder holding all fixtures (frozen starter files, additions, simulated responses).
FIXTURES = ROOT / "fixtures"
#: The tiny "repository" under investigation, copied byte-for-byte from the starter pack.
REPO_DIR = FIXTURES / "incidents"
#: Event files loaded in order: the original seed events, then handwritten additions.
EVENT_FILES = [REPO_DIR / "events.json", FIXTURES / "additions" / "extra-events.json"]
#: Supplementary inputs reported alongside, but not part of, the fixed check.
EXTRA_INPUTS_FILE = FIXTURES / "additions" / "extra-inputs.json"
#: SHA-256 manifest of files that must never change.
FROZEN_MANIFEST = FIXTURES / "frozen.json"
#: System prompt sent with every diagnosis request.
SYSTEM_PROMPT_FILE = ROOT / "prompts" / "system.md"
#: Default location for run directories.
RUNS_DIR = ROOT / "runs"

#: Files the model sees as context but may never patch.
PROTECTED_FILES = {"check.py", "reference-cases.json", "events.json", "domain.md"}

#: Timeout, in seconds, for each invocation of the fixed check command.
CHECK_TIMEOUT_S = 30

#: Model ID used for live calls (env ``INCIDENT_MODEL``).
MODEL = os.environ.get("INCIDENT_MODEL", "claude-opus-5-5")
#: Effort level for live calls (env ``INCIDENT_EFFORT``).
EFFORT = os.environ.get("INCIDENT_EFFORT", "medium")
#: Output-token ceiling per request.
MAX_TOKENS = 16000
#: HTTP timeout, in seconds, for one API request.
API_TIMEOUT_S = 300
#: Server-side refusal fallback (beta); set ``INCIDENT_FALLBACKS=0`` to send a plain request.
USE_FALLBACKS = os.environ.get("INCIDENT_FALLBACKS", "1") != "0"
