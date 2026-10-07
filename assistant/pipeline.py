"""One run: evidence -> model proposal -> validated patch on a copy -> fixed checks -> saved review package."""

import json
from pathlib import Path

from loguru import logger

from . import checks, config, evidence, integrity, model
from . import proposal as proposals
from .models import CheckResult
from .report import write_report
from .utils import read_json, to_jsonable, utc_iso, utc_now, write_json

LOG_FORMAT = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {message}"


class _Stop(Exception):
    r"""End the run early with status ``"failed"``.

    :param \*reasons: Human-readable explanations recorded in ``run.json`` and the report.
    :type \*reasons: str
    """

    def __init__(self, *reasons):
        super().__init__(*reasons)
        self.reasons = reasons


class Run:
    """Owns one run directory. Every artifact is written as soon as it exists, so a crash still leaves evidence.

    The directory is named ``<UTC timestamp>-<event>-<source label>``, with a numeric suffix if it
    already exists. ``meta`` is mirrored to ``run.json`` on every status change. Use it as a context
    manager: while open, every log record of this run is also written to ``run.log``.

    :param runs_dir: Parent folder for run directories; resolved to an absolute path.
    :type runs_dir: pathlib.Path or str
    :param event_id: Selected event, used in the directory name.
    :type event_id: str
    :param source_label: ``"live"``, ``"replay"`` or ``"sim-<name>"``, used in the directory name.
    :type source_label: str
    """

    def __init__(self, runs_dir, event_id, source_label):
        base = f"{utc_now():%Y%m%dT%H%M%SZ}-{event_id.lower()}-{source_label}"
        runs_dir = Path(runs_dir).resolve()  # checks run from another cwd, so paths must be absolute
        path, n = runs_dir / base, 1
        while path.exists():  # two runs in the same second
            n += 1
            path = runs_dir / f"{base}-{n}"
        path.mkdir(parents=True)
        self.dir = path
        self.meta = {
            "run_id": path.name,
            "created_at": utc_iso(),
            "status": "started",
            "status_reasons": [],
            "selected_event": event_id,
            "findings": [],
            "artifacts": [],
        }
        self._sink_id = None
        self._context = None

    def __enter__(self):
        """Start writing this run's log records to ``run.log``.

        :returns: This run.
        :rtype: Run
        """
        run_id = self.meta["run_id"]
        # The filter keeps records from other runs (e.g. the next demo scenario) out of this file.
        self._sink_id = logger.add(
            self.dir / "run.log",
            level="DEBUG",
            format=LOG_FORMAT,
            filter=lambda record: record["extra"].get("run_id") == run_id,
        )
        self._context = logger.contextualize(run_id=run_id)
        self._context.__enter__()
        self._track("run.log")
        return self

    def __exit__(self, exc_type, exc, tb):
        """Stop logging to ``run.log``, also when the run raised an unexpected exception.

        :param exc_type: Exception type, if one is propagating.
        :type exc_type: type or None
        :param exc: Exception instance, if one is propagating.
        :type exc: BaseException or None
        :param tb: Traceback, if an exception is propagating.
        :type tb: types.TracebackType or None
        :returns: ``False``, so exceptions propagate.
        :rtype: bool
        """
        if exc is not None:
            logger.exception("Run aborted by an unexpected error")
        self._context.__exit__(exc_type, exc, tb)
        logger.remove(self._sink_id)
        return False

    def save_json(self, name, data):
        """Write a JSON artifact and record it in ``meta["artifacts"]``.

        :param name: Path relative to the run directory; parent folders are created.
        :type name: str
        :param data: JSON-serialisable content; data models are converted.
        :type data: object
        """
        write_json(self.dir / name, data)
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
        r"""Update the status, append reasons, and persist ``run.json``.

        :param status: ``"started"``, ``"proposed"``, ``"checked"`` or ``"failed"``.
        :type status: str
        :param \*reasons: Human-readable explanations to append.
        :type \*reasons: str
        """
        self.meta["status"] = status
        self.meta["status_reasons"].extend(reasons)
        self.save_json("run.json", self.meta)

    def finish(self, status, *reasons):
        r"""Set the final status, write ``run.json`` and ``report.html``.

        :param status: Final status, ``"checked"`` or ``"failed"``.
        :type status: str
        :param \*reasons: Human-readable explanations to append.
        :type \*reasons: str
        :returns: The run directory.
        :rtype: pathlib.Path
        """
        log = logger.success if status == "checked" else logger.error
        log("Run {}: {}", status.upper(), " ".join(reasons))
        self.meta["finished_at"] = utc_iso()
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
        ``report.html``, ``run.log`` and every saved artifact.
    :rtype: pathlib.Path
    """
    model_name = model_name or config.MODEL
    effort = effort or config.EFFORT
    with Run(runs_dir or config.RUNS_DIR, event_id, _source_label(response_file)) as run:
        run.meta["settings"] = {
            "model": model_name,
            "effort": effort,
            "fallbacks": config.USE_FALLBACKS,
            "check_timeout_s": config.CHECK_TIMEOUT_S,
            "response_file": str(response_file) if response_file else None,
        }
        logger.info("Run {} started for event {}", run.meta["run_id"], event_id)
        try:
            reasons = _stages(run, event_id, response_file, model_name, effort)
        except _Stop as stop:
            return run.finish("failed", *stop.reasons)
        return run.finish("checked", *reasons)


def _stages(run, event_id, response_file, model_name, effort):
    """Run every stage in order.

    :param run: The open run.
    :type run: Run
    :param event_id: Selected event.
    :type event_id: str
    :param response_file: Saved or simulated response, or ``None`` for a live call.
    :type response_file: pathlib.Path or str or None
    :param model_name: Model ID for a live call.
    :type model_name: str
    :param effort: Effort level for a live call.
    :type effort: str
    :returns: Reasons for status ``"checked"``. Any failing stage raises :class:`_Stop`, which propagates.
    :rtype: list[str]
    """
    _verify_frozen(run, "before")
    events, malformed, incident, files = _load_context(run, event_id)
    module_path = config.REPO_DIR / incident.module
    # Everything up to here is free; stop before the model call if there is nothing to verify.
    targets, baseline_failing = _baseline_checks(run, incident, module_path)

    request = model.build_request(event_id, events, malformed, files, model=model_name, effort=effort)
    run.save_json("model/request.json", request)
    proposal = _get_proposal(run, request, response_file)

    candidate_path, findings = _build_candidate(run, proposal, incident, files[incident.module], events)
    candidate = _candidate_checks(run, candidate_path, targets)
    _extra_inputs(run, module_path, candidate_path, incident.function)
    # Not fatal here: _decide reports a change together with the other results.
    _verify_frozen(run, "after", stop=False)
    return _decide(run, findings, candidate, targets, baseline_failing)


def _verify_frozen(run, moment, *, stop=True):
    """Check the frozen-file hashes and record the result under ``meta["integrity"][moment]``.

    :param run: The open run.
    :type run: Run
    :param moment: ``"before"`` or ``"after"``.
    :type moment: str
    :param stop: Raise :class:`_Stop` on a mismatch; when false, :func:`_decide` reports it instead.
    :type stop: bool
    :raises _Stop: If ``stop`` is true and a frozen file changed.
    """
    result = integrity.verify_frozen()
    run.meta.setdefault("integrity", {})[moment] = result
    if result["ok"]:
        logger.debug("Frozen fixtures intact ({})", moment)
    else:
        logger.error("Frozen fixtures differ from their hashes ({}): {}", moment, result["mismatches"])
        if stop:
            raise _Stop("Frozen fixtures were modified before the run; refusing to continue.")


def _load_context(run, event_id):
    """Load events, select the incident, and snapshot the repository files sent to the model.

    :param run: The open run.
    :type run: Run
    :param event_id: Selected event.
    :type event_id: str
    :returns: ``(events, malformed, incident, files)``, where ``files`` maps a file name to its text.
    :rtype: tuple[list[dict], list[assistant.models.EventProblem], assistant.models.Incident, dict[str, str]]
    :raises _Stop: If the event is unknown or a repository file is missing.
    """
    events, malformed = evidence.load_events()
    incidents = evidence.group_incidents(events)
    for problem in malformed:
        logger.warning("Malformed event {} in {}: {}", problem.event_id, problem.file, problem.problem)
    incident = evidence.find_incident(incidents, event_id)
    run.save_json("evidence/events.json", {"events": events, "malformed": malformed, "incidents": incidents})
    if incident is None:
        raise _Stop(f"Event {event_id} was not found among valid events.")
    run.meta["incident"] = to_jsonable(incident)
    logger.info("Selected {}: {} at {} (events {})", incident.id, incident.error, incident.source, incident.event_ids)

    # The model sees the tests as context; only incident.module may ever be patched.
    files = {}
    for name in (incident.module, "reference-cases.json", "check.py", "domain.md"):
        path = config.REPO_DIR / name
        if not path.exists():
            raise _Stop(f"Repository file {name} is missing.")
        files[name] = path.read_text()
    run.save_json(
        "evidence/files.json",
        {name: {"sha256": integrity.sha256(config.REPO_DIR / name), "text": text} for name, text in files.items()},
    )
    return events, malformed, incident, files


def _baseline_checks(run, incident, module_path):
    """Run the fixed check on the baseline before any model call.

    Target cases are those failing with the incident's error type. Without one, a patch for this
    incident could not be verified, so the run stops before spending a model call.

    :param run: The open run.
    :type run: Run
    :param incident: Selected incident.
    :type incident: assistant.models.Incident
    :param module_path: The frozen baseline module.
    :type module_path: pathlib.Path
    :returns: ``(targets, baseline_failing)``: target case IDs and all case IDs failing on the baseline.
    :rtype: tuple[list[str], list[str]]
    :raises _Stop: If the check cannot evaluate the baseline or no case reproduces the incident.
    """
    baseline = checks.run_check(module_path)
    run.save_json("checks/baseline-full.json", baseline)
    if baseline.results is None:
        raise _Stop("The fixed check could not evaluate the baseline module.")
    baseline_failing = baseline.failing_ids
    # Match on error type so another incident's failing case is never treated as this one's target.
    targets = [r["id"] for r in baseline.results if not r["passed"] and r.get("error") == incident.error]
    run.meta["target_cases"] = targets
    run.meta["baseline_failing_cases"] = baseline_failing
    logger.info(
        "Baseline: failing {}; reproducing {}: {}", baseline_failing or "none", incident.error, targets or "none"
    )
    if not targets:
        raise _Stop(
            f"No reference case reproduces {incident.error} on the baseline, so a patch for {incident.id} "
            "could not be verified; the model was not called. (Check whether the event records expected behaviour.)"
        )
    for case_id in targets:
        run.save_json(f"checks/baseline-case-{case_id}.json", checks.run_check(module_path, case_id))
    return targets, baseline_failing


def _get_proposal(run, request, response_file):
    """Obtain and parse the model's proposal: a live call, or a saved / simulated response.

    :param run: The open run.
    :type run: Run
    :param request: Parameters from :func:`assistant.model.build_request`.
    :type request: dict
    :param response_file: Saved or simulated response, or ``None`` for a live call.
    :type response_file: pathlib.Path or str or None
    :returns: The parsed, schema-valid proposal.
    :rtype: dict
    :raises _Stop: If the model call fails or its output is unusable.
    """
    if response_file is None:
        logger.info("Calling {} (effort {})", request["model"], request["output_config"]["effort"])
    else:
        logger.info("Using saved response {} instead of calling the API", response_file)
    try:
        record = model.call_model(request) if response_file is None else model.load_response_file(response_file)
    except model.ModelError as exc:
        run.save_json("model/error.json", {"kind": exc.kind, "message": str(exc)})
        raise _Stop(f"Model call failed ({exc.kind}): {exc}") from exc
    # Saved before parsing, so even unusable output stays available for review.
    run.save_json("model/response.json", record)
    run.meta["provenance"] = record["provenance"]
    usage = record["response"].get("usage") or {}  # simulated responses have no usage
    logger.info(
        "Response source {} (input {} / output {} tokens)",
        record["provenance"]["source"],
        usage.get("input_tokens", "?"),
        usage.get("output_tokens", "?"),
    )
    try:
        proposal = proposals.parse_proposal(model.response_text(record))
    except (model.ModelError, proposals.ProposalError) as exc:
        raise _Stop(f"Unusable model output: {exc}") from exc
    run.save_json("model/proposal.json", proposal)
    run.set_status("proposed")
    location = proposal["source_location"]
    logger.info("Proposal cites {} at {}:{}", proposal["relevant_event_ids"], location["file"], location["line"])
    return proposal


def _build_candidate(run, proposal, incident, original, events):
    """Validate the diagnosis and apply the patch to a separate copy in the run directory.

    :param run: The open run.
    :type run: Run
    :param proposal: Parsed proposal.
    :type proposal: dict
    :param incident: Selected incident.
    :type incident: assistant.models.Incident
    :param original: Current text of the incident's module.
    :type original: str
    :param events: All valid events, used to detect invented event IDs.
    :type events: list[dict]
    :returns: ``(candidate_path, findings)``.
    :rtype: tuple[pathlib.Path, list[assistant.models.Finding]]
    :raises _Stop: If the patch is rejected; the baseline is never written.
    """
    findings = proposals.check_diagnosis(proposal, incident, {e["event_id"] for e in events}, original)
    candidate_text, patch_findings = proposals.apply_patch(proposal, incident, original)
    findings += patch_findings
    run.meta["findings"] = to_jsonable(findings)
    for item in findings:
        (logger.error if item.is_error else logger.warning)("{}: {}", item.code, item.message)
    if candidate_text is None:
        raise _Stop("The proposed patch was rejected; the baseline is unchanged.")
    # Diagnosis errors don't stop the run here: the checks still run so the reviewer sees the full picture.
    candidate_path = run.dir / "candidate.py"
    run.save_text("candidate.py", candidate_text)
    run.save_text("patch.diff", "".join(proposals.diff_lines(original, candidate_text, incident.module)))
    logger.info("Patch applied to a separate copy: {}", candidate_path)
    return candidate_path, findings


def _candidate_checks(run, candidate_path, targets):
    """Run the same fixed checks on the candidate copy.

    :param run: The open run.
    :type run: Run
    :param candidate_path: The patched copy.
    :type candidate_path: pathlib.Path
    :param targets: Target case IDs, each also run on its own.
    :type targets: list[str]
    :returns: Full-set result on the candidate.
    :rtype: assistant.models.CheckResult
    """
    candidate = checks.run_check(candidate_path)
    run.save_json("checks/candidate-full.json", candidate)
    for case_id in targets:
        run.save_json(f"checks/candidate-case-{case_id}.json", checks.run_check(candidate_path, case_id))
    logger.info("Candidate: failing {}", candidate.failing_ids or "none")
    return candidate


def _extra_inputs(run, module_path, candidate_path, function):
    """Evaluate the supplementary inputs (not part of the fixed check) on both versions.

    :param run: The open run.
    :type run: Run
    :param module_path: The frozen baseline module.
    :type module_path: pathlib.Path
    :param candidate_path: The patched copy.
    :type candidate_path: pathlib.Path
    :param function: Function to call.
    :type function: str
    """
    cases = json.loads(config.EXTRA_INPUTS_FILE.read_text())["cases"]
    run.save_json(
        "checks/extra-inputs.json",
        {
            "cases": cases,
            "baseline": checks.run_extra_inputs(module_path, function, cases),
            "candidate": checks.run_extra_inputs(candidate_path, function, cases),
        },
    )


def _decide(run, findings, candidate, targets, baseline_failing):
    """Decide the status from recorded evidence only; the model's own claims do not count.

    :param run: The open run.
    :type run: Run
    :param findings: Code findings on the proposal.
    :type findings: list[assistant.models.Finding]
    :param candidate: Full-set check result on the candidate.
    :type candidate: assistant.models.CheckResult
    :param targets: Cases that reproduced the incident on the baseline.
    :type targets: list[str]
    :param baseline_failing: All cases failing on the baseline.
    :type baseline_failing: list[str]
    :returns: Reasons for status ``"checked"``.
    :rtype: list[str]
    :raises _Stop: With every failure reason, if any check did not hold.
    """
    # Collect every reason instead of stopping at the first, so the report explains the whole failure.
    reasons = []
    if not run.meta["integrity"]["after"]["ok"]:
        reasons.append("Frozen fixtures changed during the run.")
    errors = [f for f in findings if f.is_error]
    if errors:
        reasons.append("Validation errors: " + "; ".join(f.message for f in errors))
    if candidate.results is None:
        reasons.append("The fixed check could not evaluate the candidate module.")
    else:
        failing = candidate.failing_ids
        still = [c for c in targets if c in failing]
        regressed = [c for c in failing if c not in baseline_failing]
        unrelated = [c for c in failing if c in baseline_failing and c not in targets]
        if still:
            reasons.append(f"Previously failing case(s) still fail: {', '.join(still)}.")
        if regressed:
            reasons.append(f"Regression: case(s) that passed on the baseline now fail: {', '.join(regressed)}.")
        if unrelated:
            reasons.append(
                f"Case(s) unrelated to this incident still fail, so the full set does not pass: {', '.join(unrelated)}."
            )
    if candidate.timed_out:
        reasons.append("The candidate check timed out.")
    if reasons:
        raise _Stop(*reasons)
    summary = (
        f"All {len(candidate.results)} reference cases pass on the candidate copy; "
        f"previously failing: {', '.join(targets)}."
    )
    return [summary]


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
    incident = read_json(run_dir / "run.json").get("incident")
    baseline_path = config.REPO_DIR / incident["module"] if incident else None
    comparison = {"checked_at": utc_iso()}
    for label, module_path in (("baseline", baseline_path), ("candidate", run_dir / "candidate.py")):
        saved = CheckResult.from_dict(read_json(run_dir / "checks" / f"{label}-full.json"))
        if module_path is None or not module_path.exists() or saved is None:
            comparison[label] = {"available": False}
            continue
        fresh = checks.run_check(module_path)
        # Compare outcomes only; durations and absolute paths legitimately differ between runs.
        same = (fresh.exit_code, fresh.results) == (saved.exit_code, saved.results)
        comparison[label] = {"available": True, "reproduced": same, "exit_code": fresh.exit_code}
        logger.info("Recheck {}: {}", label, "reproduced" if same else "DIFFERENT from saved results")
    comparison["integrity"] = integrity.verify_frozen()
    write_json(run_dir / "replay-recheck.json", comparison)
    return comparison
