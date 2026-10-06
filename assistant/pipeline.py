"""One run: evidence -> model proposal -> validated patch on a copy -> fixed checks -> saved review package."""
import json
from datetime import datetime, timezone
from pathlib import Path

from . import checks, config, evidence, integrity, model, proposal as proposals
from .report import write_report


def _now():
    return datetime.now(timezone.utc)


class Run:
    """Owns one run directory. Every artifact is written as soon as it exists, so a crash still leaves evidence."""

    def __init__(self, runs_dir, event_id, source_label):
        stamp = _now().strftime("%Y%m%dT%H%M%SZ")
        base = f"{stamp}-{event_id.lower()}-{source_label}"
        runs_dir = Path(runs_dir).resolve()  # checks run from another cwd, so paths must be absolute
        path, n = runs_dir / base, 1
        while path.exists():
            n += 1
            path = runs_dir / f"{base}-{n}"
        path.mkdir(parents=True)
        self.dir = path
        self.meta = {"run_id": path.name, "created_at": _now().isoformat(timespec="seconds"),
                     "status": "started", "status_reasons": [], "selected_event": event_id,
                     "findings": [], "artifacts": []}

    def save_json(self, name, data):
        (self.dir / name).parent.mkdir(parents=True, exist_ok=True)
        (self.dir / name).write_text(json.dumps(data, indent=2) + "\n")
        self._track(name)

    def save_text(self, name, text):
        (self.dir / name).write_text(text)
        self._track(name)

    def _track(self, name):
        if name not in self.meta["artifacts"]:
            self.meta["artifacts"].append(name)

    def set_status(self, status, *reasons):
        self.meta["status"] = status
        self.meta["status_reasons"].extend(reasons)
        self.save_json("run.json", self.meta)

    def finish(self, status, *reasons):
        self.meta["finished_at"] = _now().isoformat(timespec="seconds")
        self.set_status(status, *reasons)
        write_report(self.dir)
        return self.dir


def _source_label(response_file):
    if response_file is None:
        return "live"
    path = Path(response_file)
    return f"sim-{path.stem}" if "simulated" in path.parts else "replay"


def execute(event_id, response_file=None, model_name=None, effort=None, runs_dir=None):
    model_name = model_name or config.MODEL
    effort = effort or config.EFFORT
    run = Run(runs_dir or config.RUNS_DIR, event_id, _source_label(response_file))
    run.meta["settings"] = {"model": model_name, "effort": effort, "fallbacks": config.USE_FALLBACKS,
                            "check_timeout_s": config.CHECK_TIMEOUT_S,
                            "response_file": str(response_file) if response_file else None}

    # 1. Frozen fixtures must be intact before anything else happens.
    before = integrity.verify_frozen()
    run.meta["integrity"] = {"before": before}
    if not before["ok"]:
        return run.finish("failed", "Frozen fixtures were modified before the run; refusing to continue.")

    # 2. Evidence: events (malformed ones reported, not fatal) and the selected incident.
    events, malformed = evidence.load_events()
    incidents = evidence.group_incidents(events)
    incident = evidence.find_incident(incidents, event_id)
    run.save_json("evidence/events.json", {"events": events, "malformed": malformed,
                                           "incidents": [i.to_dict() for i in incidents]})
    if incident is None:
        return run.finish("failed", f"Event {event_id} was not found among valid events.")
    run.meta["incident"] = incident.to_dict()

    module_path = config.REPO_DIR / incident.module
    context_names = [incident.module, "reference-cases.json", "check.py", "domain.md"]
    files = {}
    for name in context_names:
        path = config.REPO_DIR / name
        if not path.exists():
            return run.finish("failed", f"Repository file {name} is missing.")
        files[name] = path.read_text()
    run.save_json("evidence/files.json", {name: {"sha256": integrity.sha256(config.REPO_DIR / name), "text": text}
                                          for name, text in files.items()})

    # 3. Baseline checks, before any model call. Cases that fail here are the regression targets.
    baseline_full = checks.run_check(module_path)
    run.save_json("checks/baseline-full.json", baseline_full)
    if baseline_full["results"] is None:
        return run.finish("failed", "The fixed check could not evaluate the baseline module.")
    targets = checks.failing_ids(baseline_full)
    run.meta["target_cases"] = targets
    for case_id in targets:
        run.save_json(f"checks/baseline-case-{case_id}.json", checks.run_check(module_path, case_id))

    # 4. Model proposal: a live call, or a saved / simulated response.
    request = model.build_request(event_id, events, malformed, files, model_name, effort)
    run.save_json("model/request.json", request)
    try:
        record = model.call_model(request) if response_file is None else model.load_response_file(response_file)
    except model.ModelError as exc:
        run.save_json("model/error.json", {"kind": exc.kind, "message": str(exc)})
        return run.finish("failed", f"Model call failed ({exc.kind}): {exc}")
    run.save_json("model/response.json", record)
    run.meta["provenance"] = record["provenance"]

    try:
        proposal = proposals.parse_proposal(model.response_text(record))
    except (model.ModelError, proposals.ProposalError) as exc:
        return run.finish("failed", f"Unusable model output: {exc}")
    run.save_json("model/proposal.json", proposal)
    run.set_status("proposed")

    # 5. Validate the diagnosis and apply the patch to a separate copy.
    original = files[incident.module]
    findings = proposals.check_diagnosis(proposal, incident, {e["event_id"] for e in events}, original)
    candidate_text, patch_findings = proposals.apply_patch(proposal, incident, original)
    findings += patch_findings
    run.meta["findings"] = findings
    if candidate_text is None:
        return run.finish("failed", "The proposed patch was rejected; the baseline is unchanged.")
    candidate_path = run.dir / "candidate.py"
    run.save_text("candidate.py", candidate_text)
    run.save_text("patch.diff", "".join(proposals.diff_lines(original, candidate_text, incident.module)))

    # 6. Same fixed checks on the candidate copy.
    candidate_full = checks.run_check(candidate_path)
    run.save_json("checks/candidate-full.json", candidate_full)
    for case_id in targets:
        run.save_json(f"checks/candidate-case-{case_id}.json", checks.run_check(candidate_path, case_id))

    # 7. Supplementary inputs (not part of the fixed check), on both versions.
    extra_cases = json.loads(config.EXTRA_INPUTS_FILE.read_text())["cases"]
    run.save_json("checks/extra-inputs.json", {
        "cases": extra_cases,
        "baseline": checks.run_extra_inputs(module_path, incident.function, extra_cases),
        "candidate": checks.run_extra_inputs(candidate_path, incident.function, extra_cases),
    })

    # 8. Decide. Only recorded evidence counts; the model's own claims do not.
    after = integrity.verify_frozen()
    run.meta["integrity"]["after"] = after
    reasons = []
    if not after["ok"]:
        reasons.append("Frozen fixtures changed during the run.")
    errors = [f for f in findings if f["level"] == "error"]
    if errors:
        reasons.append("Validation errors: " + "; ".join(f["message"] for f in errors))
    if candidate_full["results"] is None:
        reasons.append("The fixed check could not evaluate the candidate module.")
    else:
        candidate_failing = checks.failing_ids(candidate_full)
        still = [c for c in targets if c in candidate_failing]
        regressed = [c for c in candidate_failing if c not in targets]
        if still:
            reasons.append(f"Previously failing case(s) still fail: {', '.join(still)}.")
        if regressed:
            reasons.append(f"Regression: case(s) that passed on the baseline now fail: {', '.join(regressed)}.")
    if candidate_full["timed_out"]:
        reasons.append("The candidate check timed out.")
    if reasons:
        return run.finish("failed", *reasons)
    return run.finish("checked", f"All {len(candidate_full['results'])} reference cases pass on the candidate copy; "
                                 f"previously failing: {', '.join(targets) or 'none'}.")


def recheck(run_dir):
    """Rerun the fixed check on a saved candidate without calling the model, and compare with the saved results."""
    run_dir = Path(run_dir)
    comparison = {"checked_at": _now().isoformat(timespec="seconds")}
    for label, module_path in (("baseline", config.REPO_DIR / json.loads((run_dir / "run.json").read_text())
                                ["incident"]["module"]), ("candidate", run_dir / "candidate.py")):
        saved_path = run_dir / "checks" / f"{label}-full.json"
        if not module_path.exists() or not saved_path.exists():
            comparison[label] = {"available": False}
            continue
        saved = json.loads(saved_path.read_text())
        fresh = checks.run_check(module_path)
        same = (fresh["exit_code"], fresh["results"]) == (saved["exit_code"], saved["results"])
        comparison[label] = {"available": True, "reproduced": same, "exit_code": fresh["exit_code"]}
    comparison["integrity"] = integrity.verify_frozen()
    (run_dir / "replay-recheck.json").write_text(json.dumps(comparison, indent=2) + "\n")
    return comparison
