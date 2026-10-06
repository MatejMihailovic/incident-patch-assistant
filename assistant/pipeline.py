"""One run: evidence -> model proposal -> validated patch on a copy -> fixed checks -> saved review package."""
import json
from datetime import datetime, timezone
from pathlib import Path

from . import checks, config, evidence, integrity, model, proposal as proposals
from .report import write_report


def _now():
    """Current UTC time.

    :returns: Timezone-aware timestamp.
    :rtype: datetime.datetime
    """
    return datetime.now(timezone.utc)


class Run:
    """Owns one run directory. Every artifact is written as soon as it exists, so a crash still leaves evidence.

    The directory is named ``<UTC timestamp>-<event>-<source label>``, with a numeric suffix if it
    already exists. ``meta`` is mirrored to ``run.json`` on every status change.

    :param runs_dir: Parent folder for run directories; resolved to an absolute path.
    :type runs_dir: pathlib.Path or str
    :param event_id: Selected event, used in the directory name.
    :type event_id: str
    :param source_label: ``"live"``, ``"replay"`` or ``"sim-<name>"``, used in the directory name.
    :type source_label: str
    """

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
        """Write a JSON artifact and record it in ``meta["artifacts"]``.

        :param name: Path relative to the run directory; parent folders are created.
        :type name: str
        :param data: JSON-serialisable content.
        :type data: object
        """
        (self.dir / name).parent.mkdir(parents=True, exist_ok=True)
        (self.dir / name).write_text(json.dumps(data, indent=2) + "\n")
        self._track(name)

    def save_text(self, name, text):
        """Write a text artifact (candidate module, diff) and record it in ``meta["artifacts"]``.

        :param name: File name relative to the run directory.
        :type name: str
        :param text: Content to write.
        :type text: str
        """
        (self.dir / name).write_text(text)
        self._track(name)

    def _track(self, name):
        """Add an artifact name to the run's artifact list once.

        :param name: Path relative to the run directory.
        :type name: str
        """
        if name not in self.meta["artifacts"]:
            self.meta["artifacts"].append(name)

    def set_status(self, status, *reasons):
        """Update the status, append reasons, and persist ``run.json``.

        :param status: ``"started"``, ``"proposed"``, ``"checked"`` or ``"failed"``.
        :type status: str
        :param reasons: Human-readable explanations to append.
        :type reasons: str
        """
        self.meta["status"] = status
        self.meta["status_reasons"].extend(reasons)
        self.save_json("run.json", self.meta)

    def finish(self, status, *reasons):
        """Set the final status, write ``run.json`` and ``report.html``.

        :param status: Final status, ``"checked"`` or ``"failed"``.
        :type status: str
        :param reasons: Human-readable explanations to append.
        :type reasons: str
        :returns: The run directory.
        :rtype: pathlib.Path
        """
        self.meta["finished_at"] = _now().isoformat(timespec="seconds")
        self.set_status(status, *reasons)
        write_report(self.dir)
        return self.dir


def _source_label(response_file):
    """Choose the run-directory suffix that tells a reader where the model response came from.

    :param response_file: Saved or simulated response, or ``None`` for a live call.
    :type response_file: pathlib.Path or str or None
    :returns: ``"live"``, ``"sim-<file stem>"`` for files under a ``simulated`` folder, otherwise ``"replay"``.
    :rtype: str
    """
    if response_file is None:
        return "live"
    path = Path(response_file)
    return f"sim-{path.stem}" if "simulated" in path.parts else "replay"


def execute(event_id, response_file=None, model_name=None, effort=None, runs_dir=None):
    """Run the full workflow for one incident and save a review package.

    Steps: verify frozen fixtures; load events and select the incident; run the fixed check on the
    baseline; get a proposal (live call, or saved/simulated response); validate the diagnosis and
    patch in code; check a separate candidate copy; evaluate the supplementary inputs; re-verify the
    frozen fixtures; decide the status. Any failure ends the run with status ``"failed"`` and a
    reason. Nothing is raised to the caller, and the baseline is never modified.

    :param event_id: Event that selects the incident, e.g. ``"EV1"``.
    :type event_id: str
    :param response_file: Saved or simulated response to use instead of calling the API.
    :type response_file: pathlib.Path or str or None
    :param model_name: Model ID for a live call; defaults to :data:`assistant.config.MODEL`.
    :type model_name: str or None
    :param effort: Effort level for a live call; defaults to :data:`assistant.config.EFFORT`.
    :type effort: str or None
    :param runs_dir: Where to create the run directory; defaults to :data:`assistant.config.RUNS_DIR`.
    :type runs_dir: pathlib.Path or str or None
    :returns: The run directory, containing ``run.json`` (status ``"checked"`` or ``"failed"``),
        ``report.html`` and every saved artifact.
    :rtype: pathlib.Path
    """
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

    # 3. Baseline checks, before any model call. Cases that fail here with the incident's error are the
    #    regression targets; without one, a patch for this incident could not be verified.
    baseline_full = checks.run_check(module_path)
    run.save_json("checks/baseline-full.json", baseline_full)
    if baseline_full["results"] is None:
        return run.finish("failed", "The fixed check could not evaluate the baseline module.")
    baseline_failing = checks.failing_ids(baseline_full)
    targets = [r["id"] for r in baseline_full["results"] if not r["passed"] and r.get("error") == incident.error]
    run.meta["target_cases"] = targets
    run.meta["baseline_failing_cases"] = baseline_failing
    if not targets:
        return run.finish("failed", f"No reference case reproduces {incident.error} on the baseline, so a patch for "
                                    f"{incident.id} could not be verified; the model was not called. "
                                    "(Check whether the event records expected behaviour.)")
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
        regressed = [c for c in candidate_failing if c not in baseline_failing]
        unrelated = [c for c in candidate_failing if c in baseline_failing and c not in targets]
        if still:
            reasons.append(f"Previously failing case(s) still fail: {', '.join(still)}.")
        if regressed:
            reasons.append(f"Regression: case(s) that passed on the baseline now fail: {', '.join(regressed)}.")
        if unrelated:
            reasons.append(f"Case(s) unrelated to this incident still fail, so the full set does not pass: {', '.join(unrelated)}.")
    if candidate_full["timed_out"]:
        reasons.append("The candidate check timed out.")
    if reasons:
        return run.finish("failed", *reasons)
    return run.finish("checked", f"All {len(candidate_full['results'])} reference cases pass on the candidate copy; "
                                 f"previously failing: {', '.join(targets) or 'none'}.")


def recheck(run_dir):
    """Rerun the fixed check on a saved run without calling the model, and compare with the saved results.

    Also writes the comparison to ``replay-recheck.json`` in the run directory.

    :param run_dir: Directory of an earlier run.
    :type run_dir: pathlib.Path or str
    :returns: ``checked_at``; ``baseline`` and ``candidate``, each ``{"available": False}`` or
        ``{"available": True, "reproduced": bool, "exit_code": int}``; and ``integrity``
        from :func:`assistant.integrity.verify_frozen`.
    :rtype: dict
    """
    run_dir = Path(run_dir)
    incident = json.loads((run_dir / "run.json").read_text()).get("incident")
    baseline_path = config.REPO_DIR / incident["module"] if incident else None
    comparison = {"checked_at": _now().isoformat(timespec="seconds")}
    for label, module_path in (("baseline", baseline_path), ("candidate", run_dir / "candidate.py")):
        if module_path is None:
            comparison[label] = {"available": False}
            continue
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
