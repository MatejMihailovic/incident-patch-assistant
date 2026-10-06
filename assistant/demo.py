"""Minimum demonstration: run the scenarios, then grade the brief's five checks from saved artifacts.

Expected values come from the frozen ``reference-cases.json`` (verified independently by
``scripts/verify_reference.py``) and from the frozen-file hashes, never from application output.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from . import config, evidence, integrity, pipeline


def _load(run_dir, name):
    """Read a JSON artifact from a run directory.

    :param run_dir: Run directory.
    :type run_dir: pathlib.Path
    :param name: Artifact path relative to the run directory.
    :type name: str
    :returns: Parsed JSON, or ``None`` if the artifact is absent.
    :rtype: object or None
    """
    path = run_dir / name
    return json.loads(path.read_text()) if path.exists() else None


def _describe(result):
    """Summarise one check.py result as text.

    :param result: One entry of check.py's result list.
    :type result: dict
    :returns: ``"returns 0"`` or ``"raises ZeroDivisionError"``.
    :rtype: str
    """
    return f"raises {result['error']}" if "error" in result else f"returns {result['actual']}"


def latest_live_run(runs_dir=None):
    """Find the most recent run whose model response came from a real API call.

    :param runs_dir: Folder to search; defaults to :data:`assistant.config.RUNS_DIR`.
    :type runs_dir: pathlib.Path or str or None
    :returns: That run's directory, or ``None`` if no live run exists.
    :rtype: pathlib.Path or None
    """
    for run_dir in sorted(Path(runs_dir or config.RUNS_DIR).glob("*/"), reverse=True):
        response = run_dir / "model" / "response.json"
        if response.exists() and json.loads(response.read_text())["provenance"]["source"] == "live":
            return run_dir
    return None


def run_scenarios(event_id, live=False, runs_dir=None):
    """Run the real proposal and every simulated negative control.

    :param event_id: Event that selects the incident.
    :type event_id: str
    :param live: Make a new live call instead of replaying the latest saved live response.
    :type live: bool
    :param runs_dir: Where run directories are created and searched.
    :type runs_dir: pathlib.Path or str or None
    :returns: ``{"source_run": live run used as the replay source (or the new live run),
        "real": run directory of the real proposal, "simulated": [run directories]}``.
    :rtype: dict
    :raises RuntimeError: If ``live`` is false and no saved live run exists.
    """
    if live:
        real = pipeline.execute(event_id, runs_dir=runs_dir)
        source = real
    else:
        source = latest_live_run(runs_dir)
        if source is None:
            raise RuntimeError("No saved live run found; run `python -m assistant run --event EV1` with an API key first.")
        real = pipeline.execute(event_id, response_file=source / "model" / "response.json", runs_dir=runs_dir)
    simulated = [pipeline.execute(event_id, response_file=path, runs_dir=runs_dir)
                 for path in sorted((config.FIXTURES / "simulated").glob("*.json"))]
    return {"source_run": source, "real": real, "simulated": simulated}


def grade(scenarios):
    """Grade the five minimum-demonstration checks.

    :param scenarios: Result of :func:`run_scenarios`.
    :type scenarios: dict
    :returns: One row per check: ``{"check", "expected", "observed", "passed", "evidence"}``.
    :rtype: list[dict]
    """
    real, source = scenarios["real"], scenarios["source_run"]
    meta = _load(real, "run.json")
    expected = {c["id"]: c["expected"] for c in json.loads((config.REPO_DIR / "reference-cases.json").read_text())}
    targets = meta.get("target_cases", [])
    rows = []

    # 1. The frozen regression case fails before and passes after; both outputs preserved.
    before = [_load(real, f"checks/baseline-case-{c}.json") for c in targets]
    after = [_load(real, f"checks/candidate-case-{c}.json") for c in targets]
    ok = bool(targets) and all(b and a for b, a in zip(before, after)) and all(
        not b["results"][0]["passed"] and a["results"][0]["passed"] for b, a in zip(before, after))
    rows.append({
        "check": "Frozen regression case fails on the original and passes on the proposed patch; both outputs preserved",
        "expected": "; ".join(f"`{c}` expected {expected[c]}: baseline fails, candidate passes" for c in targets) or "a failing case",
        "observed": "; ".join(f"`{c}`: baseline {_describe(b['results'][0])}, candidate {_describe(a['results'][0])}"
                              for c, b, a in zip(targets, before, after) if b and a) or "missing",
        "passed": ok,
        "evidence": [f"checks/baseline-case-{c}.json" for c in targets] + [f"checks/candidate-case-{c}.json" for c in targets],
    })

    # 2. The other reference inputs match their checked expectations before and after.
    base = {r["id"]: r for r in (_load(real, "checks/baseline-full.json") or {}).get("results") or []}
    cand = {r["id"]: r for r in (_load(real, "checks/candidate-full.json") or {}).get("results") or []}
    others = [c for c in expected if c not in targets]
    ok = bool(others) and all(c in base and c in cand and base[c].get("actual") == expected[c] == cand[c].get("actual")
                              for c in others)
    rows.append({
        "check": "Remaining reference inputs match their manually checked expectations before and after the patch",
        "expected": ", ".join(f"`{c}`={expected[c]}" for c in others),
        "observed": ", ".join(f"`{c}`: {base.get(c, {}).get('actual', '?')} → {cand.get(c, {}).get('actual', '?')}" for c in others),
        "passed": ok,
        "evidence": ["checks/baseline-full.json", "checks/candidate-full.json"],
    })

    # 3. Diagnosis cites existing, relevant events and a real source line; unrelated events are not evidence.
    proposal = _load(real, "model/proposal.json") or {}
    events, _ = evidence.load_events()
    incident = meta.get("incident") or {}
    cited = proposal.get("relevant_event_ids", [])
    unrelated = sorted(e["event_id"] for e in events if e["event_id"] not in incident.get("event_ids", []))
    location = proposal.get("source_location", {})
    errors = [f for f in meta.get("findings", []) if f["level"] == "error"]
    ok = (bool(cited) and set(cited) <= set(incident.get("event_ids", [])) and not set(cited) & set(unrelated)
          and location.get("file") == incident.get("module") and location.get("line") == incident.get("line") and not errors)
    rows.append({
        "check": "Diagnosis cites an existing relevant event and source location; the unrelated event is not evidence",
        "expected": f"cites a subset of {incident.get('event_ids')} at `{incident.get('source')}`; does not cite {unrelated}",
        "observed": f"cites {cited} at `{location.get('file')}:{location.get('line')}`; code findings with level error: {len(errors)}",
        "passed": ok,
        "evidence": ["model/proposal.json", "run.json (findings)"],
    })

    # 4. Invalid or unsuccessful patches are marked failed and leave the baseline and answer key unchanged.
    sims = [(p, _load(p, "run.json")) for p in scenarios["simulated"]]
    frozen_now = integrity.verify_frozen()
    ok = bool(sims) and frozen_now["ok"] and all(
        m["status"] == "failed" and m["provenance"]["source"] == "simulated"
        and all(v["ok"] for v in m["integrity"].values()) for _, m in sims)
    rows.append({
        "check": "An invalid or unsuccessful patch leaves the baseline and answer key unchanged and is marked failed",
        "expected": "each simulated control: status failed; frozen-file hashes unchanged",
        "observed": "; ".join(f"`{p.name.split('-sim-')[-1]}`: {m['status']} ({m['status_reasons'][0]})" for p, m in sims)
                    + f"; frozen hashes intact: {frozen_now['ok']}",
        "passed": ok,
        "evidence": [f"runs/{p.name}/run.json" for p, _ in sims],
    })

    # 5. A saved run replays without a model call, keeping diff, provenance and actual test results.
    recheck = pipeline.recheck(source)
    prov = meta.get("provenance", {})
    same_diff = (real / "patch.diff").exists() and (source / "patch.diff").exists() and \
        (real / "patch.diff").read_text() == (source / "patch.diff").read_text()
    reproduced = recheck["baseline"].get("reproduced") and recheck["candidate"].get("reproduced")
    original_id = prov.get("original", {}).get("request_id") if prov.get("source") == "replay" else prov.get("request_id")
    ok = bool(reproduced and same_diff and original_id)
    rows.append({
        "check": "A saved run can be replayed without another model call, keeping its diff, provenance and test results",
        "expected": "replay uses the saved response; same diff; rerunning the fixed check reproduces saved results",
        "observed": f"provenance `{prov.get('source')}` of request `{original_id}`; diff identical: {same_diff}; "
                    f"baseline/candidate results reproduced: {recheck['baseline'].get('reproduced')}/{recheck['candidate'].get('reproduced')}",
        "passed": ok,
        "evidence": [f"runs/{source.name}/replay-recheck.json", f"runs/{real.name}/model/response.json"],
    })
    return rows


def write_results(rows, scenarios, path):
    """Write the results table as Markdown.

    :param rows: Graded checks from :func:`grade`.
    :type rows: list[dict]
    :param scenarios: Result of :func:`run_scenarios`, used to list the runs.
    :type scenarios: dict
    :param path: Output file.
    :type path: pathlib.Path
    :returns: The written path.
    :rtype: pathlib.Path
    """
    real = scenarios["real"]
    lines = [
        "# Minimum demonstration results",
        "",
        f"Generated by `python -m assistant demo` at {datetime.now(timezone.utc).isoformat(timespec='seconds')}. "
        "Expected values come from the frozen `reference-cases.json` (independently verified by "
        "`scripts/verify_reference.py`) and the frozen-file hashes, not from application output.",
        "",
        f"**{sum(r['passed'] for r in rows)}/{len(rows)} checks passed.**",
        "",
        "| # | Check | Expected | Observed | Result |",
        "|---|---|---|---|---|",
    ]
    for n, r in enumerate(rows, start=1):
        lines.append(f"| {n} | {r['check']} | {r['expected']} | {r['observed']} | {'PASS' if r['passed'] else '**FAIL**'} |")
    lines += ["", "## Runs used", "",
              "| Scenario | Model response | Status | Report |", "|---|---|---|---|"]
    for title, run_dir in [("Real proposal", real), ("Replay source (live call)", scenarios["source_run"])] + [
            (f"Negative control: {p.name.split('-sim-')[-1]}", p) for p in scenarios["simulated"]]:
        m = _load(run_dir, "run.json")
        lines.append(f"| {title} | {m['provenance']['source']} | {m['status']} | "
                     f"[runs/{run_dir.name}/report.html](runs/{run_dir.name}/report.html) |")
    lines += ["", "## Evidence files (relative to the real-proposal run unless a path is given)", ""]
    for n, r in enumerate(rows, start=1):
        lines.append(f"{n}. " + ", ".join(f"`{e}`" for e in r["evidence"]))
    lines += ["", "Passing covers only the recorded reference cases and supplementary inputs. "
              "It does not prove correctness for other inputs. See README, Limitations.", ""]
    path.write_text("\n".join(lines))
    return path
