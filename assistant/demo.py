"""Minimum demonstration: run the scenarios, then grade the brief's five checks from saved artifacts.

Expected values come from the frozen ``reference-cases.json`` (verified independently by
``scripts/verify_reference.py``) and from the frozen-file hashes, never from application output.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from . import config, evidence, integrity, pipeline
from .models import CheckResult, DemoCheck
from .utils import read_json, utc_iso


@dataclass
class Scenarios:
    """The runs produced by one demonstration.

    :param source_run: The live run whose saved response is replayed (or the new live run with ``--live``).
    :type source_run: pathlib.Path
    :param real: Run of the real model proposal.
    :type real: pathlib.Path
    :param simulated: Runs of the simulated negative controls.
    :type simulated: list[pathlib.Path]
    """

    source_run: Path
    real: Path
    simulated: list


def _describe(result):
    """Summarise one check.py result as text.

    :param result: One entry of check.py's result list.
    :type result: dict
    :returns: ``"returns 0"`` or ``"raises ZeroDivisionError"``.
    :rtype: str
    """
    return f"raises {result['error']}" if "error" in result else f"returns {result['actual']}"


def _control_name(run_dir):
    """Name of a negative control, taken from its run directory.

    :param run_dir: Run directory such as ``...-ev1-sim-patch-breaks-rounding``.
    :type run_dir: pathlib.Path
    :returns: ``"patch-breaks-rounding"``.
    :rtype: str
    """
    return run_dir.name.split("-sim-")[-1]


def latest_live_run(runs_dir=None):
    """Find the most recent run whose model response came from a real API call.

    :param runs_dir: Folder to search; defaults to :data:`assistant.config.RUNS_DIR`.
    :type runs_dir: pathlib.Path or str or None
    :returns: That run's directory, or ``None`` if no live run exists.
    :rtype: pathlib.Path or None
    """
    # Run directories start with a UTC timestamp, so reverse name order is newest first.
    for run_dir in sorted(Path(runs_dir or config.RUNS_DIR).glob("*/"), reverse=True):
        response = read_json(run_dir / "model" / "response.json")
        if response and response["provenance"]["source"] == "live":
            return run_dir
    return None


def run_scenarios(event_id, *, live=False, runs_dir=None):
    """Run the real proposal and every simulated negative control.

    :param event_id: Event that selects the incident.
    :type event_id: str
    :param live: Make a new live call instead of replaying the latest saved live response.
    :type live: bool
    :param runs_dir: Where run directories are created and searched.
    :type runs_dir: pathlib.Path or str or None
    :returns: The runs that were produced.
    :rtype: Scenarios
    :raises RuntimeError: If ``live`` is false and no saved live run exists.
    """
    if live:
        real = source = pipeline.execute(event_id, runs_dir=runs_dir)
    else:
        source = latest_live_run(runs_dir)
        if source is None:
            raise RuntimeError(
                "No saved live run found; run `python -m assistant run --event EV1` with an API key first."
            )
        real = pipeline.execute(event_id, response_file=source / "model" / "response.json", runs_dir=runs_dir)
    simulated = [
        pipeline.execute(event_id, response_file=path, runs_dir=runs_dir)
        for path in sorted((config.FIXTURES / "simulated").glob("*.json"))
    ]
    return Scenarios(source_run=source, real=real, simulated=simulated)


def grade(scenarios):
    """Grade the five minimum-demonstration checks.

    :param scenarios: Runs from :func:`run_scenarios`.
    :type scenarios: Scenarios
    :returns: One graded row per check, in the brief's order.
    :rtype: list[assistant.models.DemoCheck]
    """
    meta = read_json(scenarios.real / "run.json")
    cases = json.loads((config.REPO_DIR / "reference-cases.json").read_text())
    expected = {c["id"]: c["expected"] for c in cases}
    return [
        _grade_regression_case(scenarios.real, meta, expected),
        _grade_other_cases(scenarios.real, meta, expected),
        _grade_evidence(scenarios.real, meta),
        _grade_negative_controls(scenarios.simulated),
        _grade_replay(scenarios.real, scenarios.source_run, meta),
    ]


def _grade_regression_case(real, meta, expected):
    """Check 1: the frozen regression case fails before and passes after; both outputs preserved.

    :param real: Run of the real proposal.
    :type real: pathlib.Path
    :param meta: That run's ``run.json``.
    :type meta: dict
    :param expected: Frozen expected value per reference case.
    :type expected: dict[str, int]
    :returns: The graded row.
    :rtype: assistant.models.DemoCheck
    """
    targets = meta.get("target_cases", [])
    before = [CheckResult.from_dict(read_json(real / f"checks/baseline-case-{c}.json")) for c in targets]
    after = [CheckResult.from_dict(read_json(real / f"checks/candidate-case-{c}.json")) for c in targets]
    pairs = [(c, b.results[0], a.results[0]) for c, b, a in zip(targets, before, after, strict=True) if b and a]
    # Every target needs both saved outputs, failing before and passing after.
    passed = bool(targets) and len(pairs) == len(targets) and all(not b["passed"] and a["passed"] for _, b, a in pairs)
    return DemoCheck(
        check="Frozen regression case fails on the original and passes on the proposed patch; both outputs preserved",
        expected="; ".join(f"`{c}` expected {expected[c]}: baseline fails, candidate passes" for c in targets)
        or "a failing case",
        observed="; ".join(f"`{c}`: baseline {_describe(b)}, candidate {_describe(a)}" for c, b, a in pairs)
        or "missing",
        passed=passed,
        evidence=[f"checks/baseline-case-{c}.json" for c in targets]
        + [f"checks/candidate-case-{c}.json" for c in targets],
    )


def _grade_other_cases(real, meta, expected):
    """Check 2: the other reference inputs match their checked expectations before and after.

    :param real: Run of the real proposal.
    :type real: pathlib.Path
    :param meta: That run's ``run.json``.
    :type meta: dict
    :param expected: Frozen expected value per reference case.
    :type expected: dict[str, int]
    :returns: The graded row.
    :rtype: assistant.models.DemoCheck
    """
    baseline = CheckResult.from_dict(read_json(real / "checks/baseline-full.json"))
    candidate = CheckResult.from_dict(read_json(real / "checks/candidate-full.json"))
    base = {r["id"]: r for r in (baseline.results if baseline else None) or []}
    cand = {r["id"]: r for r in (candidate.results if candidate else None) or []}
    others = [c for c in expected if c not in meta.get("target_cases", [])]
    passed = bool(others) and all(
        c in base and c in cand and base[c].get("actual") == expected[c] == cand[c].get("actual") for c in others
    )
    return DemoCheck(
        check="Remaining reference inputs match their manually checked expectations before and after the patch",
        expected=", ".join(f"`{c}`={expected[c]}" for c in others),
        observed=", ".join(
            f"`{c}`: {base.get(c, {}).get('actual', '?')} → {cand.get(c, {}).get('actual', '?')}" for c in others
        ),
        passed=passed,
        evidence=["checks/baseline-full.json", "checks/candidate-full.json"],
    )


def _grade_evidence(real, meta):
    """Check 3: the diagnosis cites existing, relevant events and the real source line.

    :param real: Run of the real proposal.
    :type real: pathlib.Path
    :param meta: That run's ``run.json``.
    :type meta: dict
    :returns: The graded row.
    :rtype: assistant.models.DemoCheck
    """
    proposal = read_json(real / "model/proposal.json") or {}
    incident = meta.get("incident") or {}
    related = set(incident.get("event_ids", []))
    events, _ = evidence.load_events()
    unrelated = sorted(e["event_id"] for e in events if e["event_id"] not in related)
    cited = proposal.get("relevant_event_ids", [])
    location = proposal.get("source_location", {})
    errors = [f for f in meta.get("findings", []) if f["level"] == "error"]
    passed = (
        bool(cited)
        and set(cited) <= related
        and location.get("file") == incident.get("module")
        and location.get("line") == incident.get("line")
        and not errors
    )
    return DemoCheck(
        check="Diagnosis cites an existing relevant event and source location; the unrelated event is not evidence",
        expected=f"cites a subset of {incident.get('event_ids')} at `{incident.get('source')}`; does not cite {unrelated}",
        observed=f"cites {cited} at `{location.get('file')}:{location.get('line')}`; "
        f"code findings with level error: {len(errors)}",
        passed=passed,
        evidence=["model/proposal.json", "run.json (findings)"],
    )


def _grade_negative_controls(simulated):
    """Check 4: invalid or unsuccessful patches are marked failed and leave the frozen files unchanged.

    :param simulated: Runs of the simulated negative controls.
    :type simulated: list[pathlib.Path]
    :returns: The graded row.
    :rtype: assistant.models.DemoCheck
    """
    runs = [(p, read_json(p / "run.json")) for p in simulated]
    frozen_now = integrity.verify_frozen()
    passed = (
        bool(runs)
        and frozen_now["ok"]
        and all(
            m["status"] == "failed"
            and m["provenance"]["source"] == "simulated"
            and all(v["ok"] for v in m["integrity"].values())
            for _, m in runs
        )
    )
    observed = "; ".join(f"`{_control_name(p)}`: {m['status']} ({m['status_reasons'][0]})" for p, m in runs)
    return DemoCheck(
        check="An invalid or unsuccessful patch leaves the baseline and answer key unchanged and is marked failed",
        expected="each simulated control: status failed; frozen-file hashes unchanged",
        observed=f"{observed}; frozen hashes intact: {frozen_now['ok']}",
        passed=passed,
        evidence=[f"runs/{p.name}/run.json" for p, _ in runs],
    )


def _grade_replay(real, source, meta):
    """Check 5: a saved run replays without a model call, keeping its diff, provenance and test results.

    :param real: Run that replayed the saved response.
    :type real: pathlib.Path
    :param source: The live run that made the original call.
    :type source: pathlib.Path
    :param meta: The replaying run's ``run.json``.
    :type meta: dict
    :returns: The graded row.
    :rtype: assistant.models.DemoCheck
    """
    recheck = pipeline.recheck(source)
    prov = meta.get("provenance", {})
    real_diff, source_diff = real / "patch.diff", source / "patch.diff"
    same_diff = real_diff.exists() and source_diff.exists() and real_diff.read_text() == source_diff.read_text()
    base_ok, cand_ok = recheck["baseline"].get("reproduced"), recheck["candidate"].get("reproduced")
    # A replay keeps the live call's provenance under "original".
    original_id = (
        prov.get("original", {}).get("request_id") if prov.get("source") == "replay" else prov.get("request_id")
    )
    return DemoCheck(
        check="A saved run can be replayed without another model call, keeping its diff, provenance and test results",
        expected="replay uses the saved response; same diff; rerunning the fixed check reproduces saved results",
        observed=f"provenance `{prov.get('source')}` of request `{original_id}`; diff identical: {same_diff}; "
        f"baseline/candidate results reproduced: {base_ok}/{cand_ok}",
        passed=bool(base_ok and cand_ok and same_diff and original_id),
        evidence=[f"runs/{source.name}/replay-recheck.json", f"runs/{real.name}/model/response.json"],
    )


def write_results(rows, scenarios, path):
    """Write the results table as Markdown.

    :param rows: Graded checks from :func:`grade`.
    :type rows: list[assistant.models.DemoCheck]
    :param scenarios: Runs from :func:`run_scenarios`, listed in the table.
    :type scenarios: Scenarios
    :param path: Output file.
    :type path: pathlib.Path
    :returns: The written path.
    :rtype: pathlib.Path
    """
    lines = [
        "# Minimum demonstration results",
        "",
        (
            f"Generated by `python -m assistant demo` at {utc_iso()}. "
            "Expected values come from the frozen `reference-cases.json` (independently verified by "
            "`scripts/verify_reference.py`) and the frozen-file hashes, not from application output."
        ),
        "",
        f"**{sum(r.passed for r in rows)}/{len(rows)} checks passed.**",
        "",
        "| # | Check | Expected | Observed | Result |",
        "|---|---|---|---|---|",
    ]
    for n, r in enumerate(rows, start=1):
        lines.append(f"| {n} | {r.check} | {r.expected} | {r.observed} | {'PASS' if r.passed else '**FAIL**'} |")
    lines += ["", "## Runs used", "", "| Scenario | Model response | Status | Report |", "|---|---|---|---|"]
    listed = [("Real proposal", scenarios.real), ("Replay source (live call)", scenarios.source_run)]
    listed += [(f"Negative control: {_control_name(p)}", p) for p in scenarios.simulated]
    for title, run_dir in listed:
        m = read_json(run_dir / "run.json")
        lines.append(
            f"| {title} | {m['provenance']['source']} | {m['status']} | "
            f"[runs/{run_dir.name}/report.html](runs/{run_dir.name}/report.html) |"
        )
    lines += ["", "## Evidence files (relative to the real-proposal run unless a path is given)", ""]
    lines += [f"{n}. " + ", ".join(f"`{e}`" for e in r.evidence) for n, r in enumerate(rows, start=1)]
    lines += [
        "",
        (
            "Passing covers only the recorded reference cases and supplementary inputs. "
            "It does not prove correctness for other inputs. See README, Limitations."
        ),
        "",
    ]
    path.write_text("\n".join(lines))
    return path
