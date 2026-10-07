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
    """Run ``python3 check.py <module> [--case ID]`` from the frozen task folder and keep everything it printed.

    :param module_path: Module to evaluate: the frozen baseline or a candidate copy.
    :type module_path: pathlib.Path or str
    :param case_id: Run only this reference case; ``None`` runs the full set.
    :type case_id: str or None
    :param timeout: Seconds before the subprocess is killed.
    :type timeout: float
    :returns: ``command`` (as typed from the task folder, so it can be rerun by hand), ``case``,
        ``exit_code`` (``None`` on timeout), ``timed_out``, ``duration_s``, ``stdout``, ``stderr``,
        ``results`` (check.py's per-case list, or ``None`` if it did not produce one) and
        ``load_error`` (check.py's error object if the module could not be imported).
    :rtype: dict
    """
    module_path = Path(module_path).resolve()
    command = [sys.executable, "check.py", str(module_path)] + (["--case", case_id] if case_id else [])
    display = " ".join(["python3", "check.py", _display_path(module_path)] + (["--case", case_id] if case_id else []))
    started = time.monotonic()
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell; the module path is our own run directory
            command, cwd=config.REPO_DIR, capture_output=True, text=True, timeout=timeout, env=_ENV, check=False
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "command": display,
            "case": case_id,
            "exit_code": None,
            "timed_out": True,
            "duration_s": round(time.monotonic() - started, 3),
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "results": None,
            "load_error": None,
        }
    result = {
        "command": display,
        "case": case_id,
        "exit_code": proc.returncode,
        "timed_out": False,
        "duration_s": round(time.monotonic() - started, 3),
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "results": None,
        "load_error": None,
    }
    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return result
    if isinstance(parsed, dict) and "load_error" in parsed:
        result["load_error"] = parsed
    elif isinstance(parsed, list):
        result["results"] = parsed
    return result


def failing_ids(check):
    """List the reference cases that did not pass.

    :param check: Result from :func:`run_check`.
    :type check: dict
    :returns: IDs of failing cases; empty if none failed or no results were produced.
    :rtype: list[str]
    """
    return [r["id"] for r in check["results"] or [] if not r["passed"]]


def _display_path(path):
    """Express a module path relative to the task folder, so the recorded command can be rerun by hand.

    :param path: Absolute module path.
    :type path: pathlib.Path
    :returns: Path relative to :data:`assistant.config.REPO_DIR`.
    :rtype: str
    """
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
    """Evaluate the supplementary inputs in a subprocess, separately from the fixed check.

    :param module_path: Module to evaluate.
    :type module_path: pathlib.Path or str
    :param function: Name of the function to call, taken from the incident.
    :type function: str
    :param cases: Cases from ``extra-inputs.json``; only ``id`` and ``args`` are sent to the subprocess.
    :type cases: list[dict]
    :param timeout: Seconds before the subprocess is killed.
    :type timeout: float
    :returns: One ``{"id", "actual"}`` or ``{"id", "error"}`` per case, or a single ``{"error": ...}``
        if the module could not be loaded or timed out.
    :rtype: list[dict] or dict
    """
    payload = json.dumps([{"id": c["id"], "args": c["args"]} for c in cases])
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell; the module path is our own run directory
            [sys.executable, "-c", _HARNESS, str(Path(module_path).resolve()), function, payload],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_ENV,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"error": f"timed out after {timeout}s"}
    if proc.returncode != 0:
        return {"error": proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else f"exit {proc.returncode}"}
    return json.loads(proc.stdout)


def grade_extra(case, observed):
    """Grade one supplementary input against its handwritten expectation.

    :param case: Case from ``extra-inputs.json`` with ``expected``, ``expected_error`` or ``ambiguous``.
    :type case: dict
    :param observed: Matching entry from :func:`run_extra_inputs`.
    :type observed: dict
    :returns: ``"pass"``, ``"fail"``, or ``"ungraded"`` for a documented ambiguity.
    :rtype: str
    """
    if case.get("ambiguous"):
        return "ungraded"
    if "expected_error" in case:
        return "pass" if observed.get("error") == case["expected_error"] else "fail"
    return "pass" if "error" not in observed and observed.get("actual") == case["expected"] else "fail"
