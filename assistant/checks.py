"""Run the fixed check command, plus the supplementary inputs, with a timeout."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import config

# Keep __pycache__ out of the frozen task folder and the run directories.
_ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")


def run_check(module_path, case_id=None, timeout=config.CHECK_TIMEOUT_S):
    """Run `python3 check.py <module> [--case ID]` from the frozen task folder and keep everything it printed."""
    module_path = Path(module_path).resolve()
    command = [sys.executable, "check.py", str(module_path)] + (["--case", case_id] if case_id else [])
    display = " ".join(["python3", "check.py", _display_path(module_path)] + (["--case", case_id] if case_id else []))
    started = time.monotonic()
    try:
        proc = subprocess.run(command, cwd=config.REPO_DIR, capture_output=True, text=True, timeout=timeout, env=_ENV)
    except subprocess.TimeoutExpired as exc:
        return {"command": display, "case": case_id, "exit_code": None, "timed_out": True,
                "duration_s": round(time.monotonic() - started, 3),
                "stdout": exc.stdout or "", "stderr": exc.stderr or "", "results": None, "load_error": None}
    result = {"command": display, "case": case_id, "exit_code": proc.returncode, "timed_out": False,
              "duration_s": round(time.monotonic() - started, 3),
              "stdout": proc.stdout, "stderr": proc.stderr, "results": None, "load_error": None}
    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return result
    if isinstance(parsed, dict) and "load_error" in parsed:
        result["load_error"] = parsed
    elif isinstance(parsed, list):
        result["results"] = parsed
    return result


def all_passed(check):
    return (check["exit_code"] == 0 and not check["timed_out"] and check["results"] is not None
            and all(r["passed"] for r in check["results"]))


def failing_ids(check):
    return [r["id"] for r in check["results"] or [] if not r["passed"]]


def _display_path(path):
    """Path as typed from the task folder, so the recorded command can be rerun by hand."""
    return os.path.relpath(path, config.REPO_DIR)


_HARNESS = """
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("candidate", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
function = getattr(module, sys.argv[2])
out = []
for case in json.loads(sys.argv[3]):
    try:
        out.append({"id": case["id"], "actual": function(*case["args"])})
    except Exception as exc:
        out.append({"id": case["id"], "error": type(exc).__name__})
print(json.dumps(out))
"""


def run_extra_inputs(module_path, function, cases, timeout=config.CHECK_TIMEOUT_S):
    """Evaluate the supplementary inputs in a subprocess. Returns a list of observed outcomes or an error dict."""
    payload = json.dumps([{"id": c["id"], "args": c["args"]} for c in cases])
    try:
        proc = subprocess.run([sys.executable, "-c", _HARNESS, str(Path(module_path).resolve()), function, payload],
                              capture_output=True, text=True, timeout=timeout, env=_ENV)
    except subprocess.TimeoutExpired:
        return {"error": f"timed out after {timeout}s"}
    if proc.returncode != 0:
        return {"error": proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else f"exit {proc.returncode}"}
    return json.loads(proc.stdout)


def grade_extra(case, observed):
    """Return 'pass', 'fail' or 'ungraded' (for documented ambiguities)."""
    if case.get("ambiguous"):
        return "ungraded"
    if "expected_error" in case:
        return "pass" if observed.get("error") == case["expected_error"] else "fail"
    return "pass" if "error" not in observed and observed.get("actual") == case["expected"] else "fail"
