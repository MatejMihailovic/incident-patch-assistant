"""Render a self-contained HTML review report from the saved artifacts of one run.

The report reads only files in the run directory, so it can be regenerated
offline (no model call, no API key) from a committed run.
"""

import json
from dataclasses import dataclass
from html import escape
from pathlib import Path

from .checks import grade_extra

CSS = """
:root{--bg:#fbfbfa;--fg:#1d1d1b;--muted:#6b6b66;--card:#fff;--line:#e3e2dd;--ok:#1f7a3a;--okbg:#e6f4ea;
--bad:#b3261e;--badbg:#fbe9e7;--warn:#8a5a00;--warnbg:#fff4d6;--info:#2453a6;--infobg:#e8effb;--code:#f4f3ef;--hl:#fff1b8}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#e9e8e3;--muted:#a09f99;--card:#1f1f1d;--line:#34332f;
--ok:#7fd49a;--okbg:#17321f;--bad:#ff9b8f;--badbg:#3a1714;--warn:#f0c062;--warnbg:#3a2c0d;--info:#9dbcf5;--infobg:#172440;--code:#262623;--hl:#4a3f12}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1080px;margin:0 auto;padding:24px 16px 64px}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 10px}
.muted{color:var(--muted)}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:10px 0}
.badge{display:inline-block;padding:2px 10px;border-radius:999px;font-size:13px;font-weight:600;margin-right:6px}
.ok{color:var(--ok);background:var(--okbg)}.bad{color:var(--bad);background:var(--badbg)}.warn{color:var(--warn);background:var(--warnbg)}
.info{color:var(--info);background:var(--infobg)}table{border-collapse:collapse;width:100%;font-size:14px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--muted);font-weight:600}
.scroll{overflow-x:auto}pre,code{font:13px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
pre{background:var(--code);padding:10px 12px;border-radius:8px;overflow-x:auto;margin:6px 0}
.src span{display:block}.src .hl{background:var(--hl)}.add{color:var(--ok)}.del{color:var(--bad)}.hunk{color:var(--info)}
details{margin:6px 0}summary{cursor:pointer;color:var(--muted)}ul{margin:6px 0;padding-left:20px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px}
.label{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
"""


def _load(run_dir, name):
    """Read a JSON artifact if it exists. Runs that failed early have fewer artifacts.

    :param run_dir: Run directory.
    :type run_dir: pathlib.Path
    :param name: Artifact path relative to the run directory.
    :type name: str
    :returns: Parsed JSON, or ``None`` if the artifact is absent.
    :rtype: object or None
    """
    path = run_dir / name
    return json.loads(path.read_text()) if path.exists() else None


def _badge(text, kind):
    """Render a coloured status pill.

    :param text: Label; HTML-escaped.
    :type text: str
    :param kind: CSS class: ``"ok"``, ``"bad"``, ``"warn"`` or ``"info"``.
    :type kind: str
    :returns: HTML fragment.
    :rtype: str
    """
    return f'<span class="badge {kind}">{escape(text)}</span>'


STATUS_KIND = {"checked": "ok", "failed": "bad", "proposed": "warn", "started": "warn"}


def _outcome(result):
    """Render one check result: the returned value or raised error, plus pass/fail when graded.

    :param result: One entry of check.py's result list, or ``None`` if the case was not run.
    :type result: dict or None
    :returns: HTML fragment.
    :rtype: str
    """
    if result is None:
        return '<span class="muted">not run</span>'
    text = f"raises {result['error']}" if "error" in result else json.dumps(result.get("actual"))
    if "passed" in result:
        return f"{escape(text)} {_badge('pass', 'ok') if result['passed'] else _badge('fail', 'bad')}"
    return escape(text)


def _provenance(meta):
    """Render where the model response came from: live call, replay of a saved real response, or simulated.

    :param meta: Contents of ``run.json``.
    :type meta: dict
    :returns: HTML fragment.
    :rtype: str
    """
    prov = meta.get("provenance")
    if not prov:
        return _badge("no model response", "warn")
    if prov["source"] == "live":
        return (
            _badge("LIVE model call", "info")
            + f'<span class="muted">{escape(prov.get("served_model") or "")} · request {escape(prov.get("request_id") or "?")}</span>'
        )
    if prov["source"] == "replay":
        orig = prov.get("original", {})
        return (
            _badge("REPLAY of a saved real response", "info")
            + f'<span class="muted">originally {escape(orig.get("served_model") or "?")} · request '
            f"{escape(orig.get('request_id') or '?')} · {escape(orig.get('received_at') or '')}</span>"
        )
    return (
        _badge("SIMULATED response (negative control)", "warn")
        + f'<span class="muted">{escape(prov.get("description", ""))}</span>'
    )


def _source_listing(text, highlight):
    """Render a numbered source listing.

    :param text: Source file contents.
    :type text: str
    :param highlight: 1-based line numbers to highlight.
    :type highlight: set[int]
    :returns: HTML ``<pre>`` block.
    :rtype: str
    """
    rows = []
    for n, line in enumerate(text.splitlines(), start=1):
        cls = ' class="hl"' if n in highlight else ""
        rows.append(f"<span{cls}>{n:>3}  {escape(line)}</span>")
    return f'<pre class="src">{"".join(rows)}</pre>'


def _diff_html(diff):
    """Render a unified diff with added, removed and hunk lines coloured.

    :param diff: Unified diff text.
    :type diff: str
    :returns: HTML ``<pre>`` block.
    :rtype: str
    """
    out = []
    for line in diff.splitlines():
        cls = (
            "hunk"
            if line.startswith("@@")
            else "add"
            if line.startswith("+") and not line.startswith("+++")
            else "del"
            if line.startswith("-") and not line.startswith("---")
            else ""
        )
        out.append(f'<span class="{cls}">{escape(line)}</span>' if cls else escape(line))
    return "<pre>" + "\n".join(out) + "</pre>"


def _check_details(check):
    """Render one check invocation as a collapsible block with its command, exit state and raw output.

    :param check: Result from :func:`assistant.checks.run_check`, or ``None``.
    :type check: dict or None
    :returns: HTML ``<details>`` block, or an empty string.
    :rtype: str
    """
    if not check:
        return ""
    state = "timed out" if check["timed_out"] else f"exit {check['exit_code']}"
    return (
        f"<details><summary><code>{escape(check['command'])}</code> → {escape(state)} "
        f"({check['duration_s']}s)</summary><pre>{escape(check['stdout'] or '')}{escape(check['stderr'] or '')}</pre></details>"
    )


@dataclass
class RunData:
    """Everything a report needs, loaded once from a run directory. Missing artifacts are ``None``.

    :param dir: Run directory.
    :type dir: pathlib.Path
    :param meta: Contents of ``run.json``.
    :type meta: dict
    :param evidence: Contents of ``evidence/events.json``.
    :type evidence: dict
    :param files: Contents of ``evidence/files.json``.
    :type files: dict
    :param proposal: Parsed proposal, if one was produced.
    :type proposal: dict or None
    :param model_error: Model failure record, if the call failed.
    :type model_error: dict or None
    :param response: Saved model response record.
    :type response: dict or None
    :param base_full: Full-set check on the baseline.
    :type base_full: dict or None
    :param cand_full: Full-set check on the candidate.
    :type cand_full: dict or None
    :param extra: Supplementary-input results.
    :type extra: dict or None
    :param recheck: Offline recheck comparison, if a replay ran one.
    :type recheck: dict or None
    :param diff: Unified diff of the patch, if one was applied.
    :type diff: str or None
    """

    dir: Path
    meta: dict
    evidence: dict
    files: dict
    proposal: dict | None
    model_error: dict | None
    response: dict | None
    base_full: dict | None
    cand_full: dict | None
    extra: dict | None
    recheck: dict | None
    diff: str | None

    @property
    def incident(self):
        """The selected incident, or ``None`` if the run stopped before selecting one.

        :returns: Incident fields from ``run.json``.
        :rtype: dict or None
        """
        return self.meta.get("incident")

    @property
    def targets(self):
        """Reference cases that reproduced the incident on the baseline.

        :returns: Case IDs.
        :rtype: list[str]
        """
        return self.meta.get("target_cases", [])


def load_run(run_dir):
    """Load all report inputs from a run directory.

    :param run_dir: Run directory.
    :type run_dir: pathlib.Path or str
    :returns: The loaded run.
    :rtype: RunData
    """
    run_dir = Path(run_dir)
    diff_path = run_dir / "patch.diff"
    return RunData(
        dir=run_dir,
        meta=_load(run_dir, "run.json") or {},
        evidence=_load(run_dir, "evidence/events.json") or {"events": [], "malformed": []},
        files=_load(run_dir, "evidence/files.json") or {},
        proposal=_load(run_dir, "model/proposal.json"),
        model_error=_load(run_dir, "model/error.json"),
        response=_load(run_dir, "model/response.json"),
        base_full=_load(run_dir, "checks/baseline-full.json"),
        cand_full=_load(run_dir, "checks/candidate-full.json"),
        extra=_load(run_dir, "checks/extra-inputs.json"),
        recheck=_load(run_dir, "replay-recheck.json"),
        diff=diff_path.read_text() if diff_path.exists() else None,
    )


def _pass_count(check):
    """Format a pass count for the headline cards.

    :param check: Result from :func:`assistant.checks.run_check`, or ``None``.
    :type check: dict or None
    :returns: ``"passed/total"``, or an em dash when there are no results.
    :rtype: str
    """
    results = (check or {}).get("results")
    return f"{sum(r['passed'] for r in results)}/{len(results)}" if results else "—"


def _section_header(d):
    """Title, run ID, status badge, provenance and status reasons.

    :param d: Loaded run.
    :type d: RunData
    :returns: HTML fragment.
    :rtype: str
    """
    status = d.meta.get("status", "unknown")
    title = d.incident["id"] if d.incident else d.meta.get("selected_event", "?")
    reasons = "".join(f"<li>{escape(r)}</li>" for r in d.meta.get("status_reasons", []))
    return (
        f"<h1>Incident review: {escape(title)}</h1>"
        f"<div class=muted>Run <code>{escape(d.meta.get('run_id', ''))}</code> · "
        f"{escape(d.meta.get('created_at', ''))}</div>"
        f"<div class=card>{_badge(status.upper(), STATUS_KIND.get(status, 'warn'))} {_provenance(d.meta)}"
        f"<ul>{reasons}</ul></div>"
    )


def _section_headline(d):
    """Four headline cards: target before/after, pass counts, frozen-file integrity.

    :param d: Loaded run.
    :type d: RunData
    :returns: HTML fragment.
    :rtype: str
    """
    target_after = "—"
    if d.cand_full and d.cand_full.get("results") and d.targets:
        ok = all(r["passed"] for r in d.cand_full["results"] if r["id"] in d.targets)
        target_after = "passes" if ok else "still fails"
    integrity = d.meta.get("integrity", {})
    intact = all(v.get("ok") for v in integrity.values()) if integrity else None
    cards = (
        ("Failing case(s) on baseline", ", ".join(d.targets) or "none"),
        ("Failing case(s) after patch", target_after),
        ("Reference cases: baseline → candidate", f"{_pass_count(d.base_full)} → {_pass_count(d.cand_full)}"),
        ("Frozen fixtures unchanged", {True: "yes", False: "NO", None: "—"}[intact]),
    )
    return (
        "<div class=grid>"
        + "".join(
            f"<div class=card><div class=label>{escape(label)}</div><div><b>{escape(value)}</b></div></div>"
            for label, value in cards
        )
        + "</div>"
    )


def _model_view(d, event_id):
    """Describe how the model treated one event.

    :param d: Loaded run.
    :type d: RunData
    :param event_id: Event to describe.
    :type event_id: str
    :returns: ``"cited as evidence"``, ``"excluded: <reason>"``, ``"not mentioned"``, or an em dash without a proposal.
    :rtype: str
    """
    if not d.proposal:
        return "—"
    if event_id in d.proposal["relevant_event_ids"]:
        return "cited as evidence"
    excluded = {e["event_id"]: e["reason"] for e in d.proposal["excluded_events"]}
    return f"excluded: {excluded[event_id]}" if event_id in excluded else "not mentioned"


def _section_evidence(d):
    """The selected incident, every event with code grouping vs. model view, and malformed events.

    :param d: Loaded run.
    :type d: RunData
    :returns: HTML fragment.
    :rtype: str
    """
    parts = ["<h2>Incident and evidence</h2>"]
    inc = d.incident
    if inc:
        parts.append(
            f"<p>Selected event <b>{escape(d.meta['selected_event'])}</b> belongs to incident <b>{escape(inc['id'])}</b>: "
            f"<code>{escape(inc['function'])}</code> raised <code>{escape(inc['error'])}</code> at "
            f"<code>{escape(inc['source'])}</code>. Events are grouped by code on (function, error, source), "
            "independently of the model.</p>"
        )
    cited = set(d.proposal["relevant_event_ids"]) if d.proposal else set()
    rows = []
    for e in d.evidence["events"]:
        same = bool(inc) and e["event_id"] in inc["event_ids"]
        flag = _badge("unrelated but cited", "bad") if e["event_id"] in cited and not same else ""
        grouping = _badge("same failure", "info") if same else _badge("different failure", "warn")
        rows.append(
            f"<tr><td><b>{escape(e['event_id'])}</b></td><td><code>{escape(json.dumps(e['input']))}</code></td>"
            f"<td>{escape(e['error'])}</td><td><code>{escape(e['source'])}</code></td><td>{grouping}</td>"
            f"<td>{escape(_model_view(d, e['event_id']))} {flag}</td>"
            f"<td class=muted>{escape(e.get('note', ''))}<br>{escape(e['_origin'])}</td></tr>"
        )
    parts.append(
        "<div class=scroll><table><tr><th>Event</th><th>Input</th><th>Error</th><th>Source</th><th>Code grouping</th>"
        "<th>Model</th><th>Note / file</th></tr>" + "".join(rows) + "</table></div>"
    )
    if d.evidence["malformed"]:
        items = "".join(
            f"<li><code>{escape(str(m.get('event_id')))}</code> in {escape(m['file'])}: {escape(m['problem'])}</li>"
            for m in d.evidence["malformed"]
        )
        parts.append(f"<div class=card><b>Malformed events (reported, not used):</b><ul>{items}</ul></div>")
    return "".join(parts)


def _section_source(d):
    """Numbered listing of the incident's module with the event line and the model's cited line highlighted.

    :param d: Loaded run.
    :type d: RunData
    :returns: HTML fragment, or an empty string if no source was captured.
    :rtype: str
    """
    inc = d.incident
    if not inc or inc["module"] not in d.files:
        return ""
    cited_line = d.proposal["source_location"]["line"] if d.proposal else None
    lines = {inc["line"], cited_line} - {None}
    note = f"line {inc['line']} (from events)" + (f", line {cited_line} (cited by model)" if cited_line else "")
    return (
        f"<h2>Cited source: {escape(inc['module'])}</h2><div class=muted>Highlighted: {note}</div>"
        + _source_listing(d.files[inc["module"]]["text"], lines)
    )


def _section_diagnosis(d):
    """Observed failure vs. inferred cause, or the model error, plus the code findings on the proposal.

    :param d: Loaded run.
    :type d: RunData
    :returns: HTML fragment.
    :rtype: str
    """
    parts = ["<h2>Diagnosis</h2>"]
    p = d.proposal
    if p:
        parts.append(
            f"<div class=card><div class=label>Observed failure (from events)</div><p>{escape(p['observed_failure'])}</p>"
            f"<div class=label>Inferred cause (model's interpretation, confidence: {escape(p['confidence'])})</div>"
            f"<p>{escape(p['inferred_cause'])}</p><div class=label>Patch rationale</div>"
            f"<p>{escape(p['patch_rationale'])}</p></div>"
        )
    elif d.model_error:
        parts.append(
            f"<div class=card>{_badge('model error', 'bad')} <b>{escape(d.model_error['kind'])}</b>: "
            f"{escape(d.model_error['message'])}</div>"
        )
    else:
        parts.append("<p class=muted>No usable proposal was produced.</p>")
    findings = d.meta.get("findings", [])
    if findings:
        items = "".join(
            f"<li>{_badge(f['level'], 'bad' if f['level'] == 'error' else 'warn')}<code>{escape(f['code'])}</code> "
            f"{escape(f['message'])}</li>"
            for f in findings
        )
        parts.append(f"<div class=card><b>Checks on the proposal (done in code)</b><ul>{items}</ul></div>")
    elif p:
        parts.append(
            f"<div class=card>{_badge('no findings', 'ok')} Cited events exist and belong to the incident, the cited "
            "line exists, and the patch applies cleanly to the chosen module without changing its interface.</div>"
        )
    return "".join(parts)


def _section_diff(d):
    """The proposed diff, or a note that no patch was applied.

    :param d: Loaded run.
    :type d: RunData
    :returns: HTML fragment.
    :rtype: str
    """
    body = _diff_html(d.diff) if d.diff else "<p class=muted>No patch was applied. The baseline is unchanged.</p>"
    return "<h2>Proposed diff</h2>" + body


def _section_checks(d):
    """Per-case before/after table for the fixed check, plus every command with its raw output.

    :param d: Loaded run.
    :type d: RunData
    :returns: HTML fragment.
    :rtype: str
    """
    parts = ["<h2>Fixed check: before and after</h2>"]
    if d.base_full and d.base_full.get("results"):
        cand = {r["id"]: r for r in (d.cand_full or {}).get("results") or []}
        rows = []
        for r in d.base_full["results"]:
            c = cand.get(r["id"])
            changed = c is not None and (c.get("passed") != r["passed"] or c.get("actual") != r.get("actual"))
            target = " " + _badge("target", "info") if r["id"] in d.targets else ""
            rows.append(
                f"<tr><td><b>{escape(r['id'])}</b>{target}</td><td><code>{escape(json.dumps(r['args']))}</code></td>"
                f"<td>{escape(json.dumps(r['expected']))}</td><td>{_outcome(r)}</td><td>{_outcome(c)}</td>"
                f"<td>{'changed' if changed else 'same'}</td></tr>"
            )
        parts.append(
            "<div class=scroll><table><tr><th>Case</th><th>Args</th><th>Expected (frozen)</th><th>Baseline</th>"
            "<th>Candidate</th><th>Change</th></tr>" + "".join(rows) + "</table></div>"
        )
    if d.cand_full and d.cand_full.get("load_error"):
        parts.append(
            f"<div class=card>{_badge('candidate failed to load', 'bad')} "
            f"{escape(json.dumps(d.cand_full['load_error']))}</div>"
        )
    check_files = [p for pattern in ("*-full.json", "*-case-*.json") for p in sorted((d.dir / "checks").glob(pattern))]
    details = "".join(_check_details(_load(d.dir, f"checks/{p.name}")) for p in check_files)
    parts.append(f"<div class=card><b>Commands and raw output</b>{details}</div>")
    return "".join(parts)


def _extra_cell(case, observed):
    """Render one observed supplementary-input outcome with its grade.

    :param case: Case from ``extra-inputs.json``.
    :type case: dict
    :param observed: Observed outcome, or ``None`` if unavailable.
    :type observed: dict or None
    :returns: HTML fragment.
    :rtype: str
    """
    if observed is None:
        return "<span class=muted>—</span>"
    text = f"raises {observed['error']}" if "error" in observed else json.dumps(observed["actual"])
    badge = {"pass": ("pass", "ok"), "fail": ("fail", "bad"), "ungraded": ("ungraded", "warn")}[
        grade_extra(case, observed)
    ]
    return f"{escape(text)} {_badge(*badge)}"


def _section_extra(d):
    """Supplementary inputs on both versions, flagging any change on a documented ambiguity.

    :param d: Loaded run.
    :type d: RunData
    :returns: HTML fragment, or an empty string if the inputs were not evaluated.
    :rtype: str
    """
    if not d.extra or not isinstance(d.extra.get("baseline"), list):
        return ""
    base_obs = {o["id"]: o for o in d.extra["baseline"]}
    cand_obs = {o["id"]: o for o in d.extra["candidate"]} if isinstance(d.extra.get("candidate"), list) else {}
    rows = []
    for case in d.extra["cases"]:
        if "expected_error" in case:
            expected = "raises " + case["expected_error"]
        else:
            expected = "ambiguous" if case.get("ambiguous") else json.dumps(case["expected"])
        b, c = base_obs.get(case["id"]), cand_obs.get(case["id"])
        note = escape(case["rule"])
        if case.get("ambiguous") and b and c and b != c:
            note = _badge("behaviour changed from baseline: reviewer decision needed", "warn") + " " + note
        rows.append(
            f"<tr><td>{escape(case['id'])}</td><td><code>{escape(json.dumps(case['args']))}</code></td>"
            f"<td>{escape(expected)}</td><td>{_extra_cell(case, b)}</td><td>{_extra_cell(case, c)}</td>"
            f"<td class=muted>{note}</td></tr>"
        )
    return (
        "<h2>Supplementary inputs (not part of the fixed check)</h2>"
        "<p class=muted>Handwritten additions from <code>fixtures/additions/extra-inputs.json</code>, verified "
        "independently by <code>scripts/verify_reference.py</code>. They inform the reviewer but do not decide the "
        "run status.</p><div class=scroll><table><tr><th>Case</th><th>Args</th><th>Expected</th><th>Baseline</th>"
        "<th>Candidate</th><th>Rule</th></tr>" + "".join(rows) + "</table></div>"
    )


def _section_limits(d):
    """What the run does not establish, including the model's own (unverified) risk list.

    :param d: Loaded run.
    :type d: RunData
    :returns: HTML fragment.
    :rtype: str
    """
    n_ref = len((d.base_full or {}).get("results") or [])
    extra = f" and is accompanied by {len(d.extra['cases'])} supplementary inputs" if d.extra else ""
    items = [
        (
            f"A passing status covers only the {n_ref} recorded reference cases{extra}. "
            "It is not a proof of correctness for other inputs."
        ),
        (
            "The candidate ran in a subprocess with a timeout, not in a sandbox. The patch was screened for imports "
            "and dynamic-execution builtins before running."
        ),
        (
            "The inferred cause is the model's interpretation. Only the observed failure and the test results are "
            "recorded facts."
        ),
    ]
    html = "".join(f"<li>{escape(i)}</li>" for i in items)
    if d.proposal and d.proposal["untested_risks"]:
        risks = "".join(f"<li>{escape(r)}</li>" for r in d.proposal["untested_risks"])
        html += f"<li>Model-reported risks (not verified by this tool):<ul>{risks}</ul></li>"
    return f"<h2>What this run does not establish</h2><ul>{html}</ul>"


def _section_provenance(d):
    """Settings, provenance, token usage, offline recheck result, artifact list and replay command.

    :param d: Loaded run.
    :type d: RunData
    :returns: HTML fragment.
    :rtype: str
    """
    run_id = escape(d.meta.get("run_id", ""))
    usage = (d.response or {}).get("response", {}).get("usage")
    record = {"settings": d.meta.get("settings"), "provenance": d.meta.get("provenance", {}), "usage": usage}
    parts = [
        (
            f"<h2>Provenance and reproduction</h2><div class=card><p>{_provenance(d.meta)}</p>"
            f"<pre>{escape(json.dumps(record, indent=2))}</pre>"
        )
    ]
    if d.recheck:
        ok = all(d.recheck.get(k, {}).get("reproduced", True) for k in ("baseline", "candidate"))
        parts.append(
            f"<p>{_badge('re-checked offline', 'ok' if ok else 'bad')} at {escape(d.recheck['checked_at'])}: saved "
            f"test results {'reproduced exactly' if ok else 'DID NOT reproduce'} when the fixed check was rerun.</p>"
        )
    artifacts = ", ".join(f"<code>{escape(a)}</code>" for a in d.meta.get("artifacts", []))
    parts.append(
        f"<p class=muted>Artifacts in <code>runs/{run_id}/</code>: {artifacts}</p>"
        f"<p class=muted>Replay without an API key: "
        f"<code>python -m assistant replay runs/{run_id} --recheck</code></p></div>"
    )
    return "".join(parts)


SECTIONS = (
    _section_header,
    _section_headline,
    _section_evidence,
    _section_source,
    _section_diagnosis,
    _section_diff,
    _section_checks,
    _section_extra,
    _section_limits,
    _section_provenance,
)


def build_html(run_dir):
    """Build the review report for one run from its saved artifacts.

    :param run_dir: Run directory created by :func:`assistant.pipeline.execute`.
    :type run_dir: pathlib.Path or str
    :returns: Complete self-contained HTML document.
    :rtype: str
    """
    d = load_run(run_dir)
    head = (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>Incident review {escape(d.meta.get('run_id', ''))}</title><style>{CSS}</style></head><body><main>"
    )
    return "\n".join([head, *(section(d) for section in SECTIONS), "</main></body></html>"])


def write_report(run_dir):
    """Write ``report.html`` into the run directory.

    :param run_dir: Run directory.
    :type run_dir: pathlib.Path or str
    :returns: Path of the written report.
    :rtype: pathlib.Path
    """
    path = Path(run_dir) / "report.html"
    path.write_text(build_html(run_dir))
    return path
