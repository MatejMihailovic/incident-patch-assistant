"""Render a self-contained HTML review report from the saved artifacts of one run.

The report reads only files in the run directory, so it can be regenerated
offline (no model call, no API key) from a committed run.
"""
import json
from html import escape
from pathlib import Path

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
    path = run_dir / name
    return json.loads(path.read_text()) if path.exists() else None


def _badge(text, kind):
    return f'<span class="badge {kind}">{escape(text)}</span>'


STATUS_KIND = {"checked": "ok", "failed": "bad", "proposed": "warn", "started": "warn"}


def _outcome(result):
    if result is None:
        return '<span class="muted">not run</span>'
    if "error" in result:
        text = f"raises {result['error']}"
    else:
        text = json.dumps(result.get("actual"))
    if "passed" in result:
        return f"{escape(text)} {_badge('pass', 'ok') if result['passed'] else _badge('fail', 'bad')}"
    return escape(text)


def _provenance(meta):
    prov = meta.get("provenance")
    if not prov:
        return _badge("no model response", "warn")
    if prov["source"] == "live":
        return (_badge("LIVE model call", "info") +
                f'<span class="muted">{escape(prov.get("served_model") or "")} · request {escape(prov.get("request_id") or "?")}</span>')
    if prov["source"] == "replay":
        orig = prov.get("original", {})
        return (_badge("REPLAY of a saved real response", "info") +
                f'<span class="muted">originally {escape(orig.get("served_model") or "?")} · request '
                f'{escape(orig.get("request_id") or "?")} · {escape(orig.get("received_at") or "")}</span>')
    return (_badge("SIMULATED response (negative control)", "warn") +
            f'<span class="muted">{escape(prov.get("description", ""))}</span>')


def _source_listing(text, highlight):
    rows = []
    for n, line in enumerate(text.splitlines(), start=1):
        cls = ' class="hl"' if n in highlight else ""
        rows.append(f"<span{cls}>{n:>3}  {escape(line)}</span>")
    return f'<pre class="src">{"".join(rows)}</pre>'


def _diff_html(diff):
    out = []
    for line in diff.splitlines():
        cls = ("hunk" if line.startswith("@@") else "add" if line.startswith("+") and not line.startswith("+++")
               else "del" if line.startswith("-") and not line.startswith("---") else "")
        out.append(f'<span class="{cls}">{escape(line)}</span>' if cls else escape(line))
    return "<pre>" + "\n".join(out) + "</pre>"


def _check_details(check):
    if not check:
        return ""
    state = "timed out" if check["timed_out"] else f"exit {check['exit_code']}"
    return (f"<details><summary><code>{escape(check['command'])}</code> → {escape(state)} "
            f"({check['duration_s']}s)</summary><pre>{escape(check['stdout'] or '')}{escape(check['stderr'] or '')}</pre></details>")


def build_html(run_dir):
    run_dir = Path(run_dir)
    meta = _load(run_dir, "run.json") or {}
    ev = _load(run_dir, "evidence/events.json") or {"events": [], "malformed": []}
    files = _load(run_dir, "evidence/files.json") or {}
    proposal = _load(run_dir, "model/proposal.json")
    model_error = _load(run_dir, "model/error.json")
    base_full = _load(run_dir, "checks/baseline-full.json")
    cand_full = _load(run_dir, "checks/candidate-full.json")
    extra = _load(run_dir, "checks/extra-inputs.json")
    recheck = _load(run_dir, "replay-recheck.json")
    diff = (run_dir / "patch.diff").read_text() if (run_dir / "patch.diff").exists() else None
    incident = meta.get("incident")
    targets = meta.get("target_cases", [])
    status = meta.get("status", "unknown")

    h = [f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
         f"<title>Incident review {escape(meta.get('run_id', ''))}</title><style>{CSS}</style></head><body><main>"]
    h.append(f"<h1>Incident review: {escape(incident['id'] if incident else meta.get('selected_event', '?'))}</h1>")
    h.append(f"<div class=muted>Run <code>{escape(meta.get('run_id', ''))}</code> · {escape(meta.get('created_at', ''))}</div>")
    h.append(f'<div class=card>{_badge(status.upper(), STATUS_KIND.get(status, "warn"))} {_provenance(meta)}<ul>'
             + "".join(f"<li>{escape(r)}</li>" for r in meta.get("status_reasons", [])) + "</ul></div>")

    # Headline numbers
    def passed(check):
        res = (check or {}).get("results")
        return f"{sum(r['passed'] for r in res)}/{len(res)}" if res else "—"
    target_after = "—"
    if cand_full and cand_full.get("results") and targets:
        ok = all(r["passed"] for r in cand_full["results"] if r["id"] in targets)
        target_after = "passes" if ok else "still fails"
    integ = meta.get("integrity", {})
    integ_ok = all(v.get("ok") for v in integ.values()) if integ else None
    h.append("<div class=grid>")
    for label, value in (("Failing case(s) on baseline", ", ".join(targets) or "none"),
                         ("Failing case(s) after patch", target_after),
                         ("Reference cases: baseline → candidate", f"{passed(base_full)} → {passed(cand_full)}"),
                         ("Frozen fixtures unchanged", "yes" if integ_ok else "NO" if integ_ok is False else "—")):
        h.append(f"<div class=card><div class=label>{escape(label)}</div><div><b>{escape(value)}</b></div></div>")
    h.append("</div>")

    # Evidence
    h.append("<h2>Incident and evidence</h2>")
    if incident:
        h.append(f"<p>Selected event <b>{escape(meta['selected_event'])}</b> belongs to incident <b>{escape(incident['id'])}</b>: "
                 f"<code>{escape(incident['function'])}</code> raised <code>{escape(incident['error'])}</code> at "
                 f"<code>{escape(incident['source'])}</code>. Events are grouped by code on (function, error, source), "
                 f"independently of the model.</p>")
    cited = set(proposal["relevant_event_ids"]) if proposal else set()
    excluded = {e["event_id"]: e["reason"] for e in proposal["excluded_events"]} if proposal else {}
    rows = []
    for e in ev["events"]:
        same = incident and e["event_id"] in incident["event_ids"]
        model_view = ("cited as evidence" if e["event_id"] in cited else
                      f"excluded: {excluded[e['event_id']]}" if e["event_id"] in excluded else "not mentioned") if proposal else "—"
        flag = _badge("unrelated but cited", "bad") if (e["event_id"] in cited and not same) else ""
        rows.append(f"<tr><td><b>{escape(e['event_id'])}</b></td><td><code>{escape(json.dumps(e['input']))}</code></td>"
                    f"<td>{escape(e['error'])}</td><td><code>{escape(e['source'])}</code></td>"
                    f"<td>{_badge('same failure', 'info') if same else _badge('different failure', 'warn')}</td>"
                    f"<td>{escape(model_view)} {flag}</td><td class=muted>{escape(e.get('note', ''))}<br>{escape(e['_origin'])}</td></tr>")
    h.append("<div class=scroll><table><tr><th>Event</th><th>Input</th><th>Error</th><th>Source</th><th>Code grouping</th>"
             "<th>Model</th><th>Note / file</th></tr>" + "".join(rows) + "</table></div>")
    if ev["malformed"]:
        h.append("<div class=card><b>Malformed events (reported, not used):</b><ul>" + "".join(
            f"<li><code>{escape(str(m.get('event_id')))}</code> in {escape(m['file'])}: {escape(m['problem'])}</li>"
            for m in ev["malformed"]) + "</ul></div>")

    # Cited source
    if incident and incident["module"] in files:
        lines = {incident["line"]}
        cited_line = proposal["source_location"]["line"] if proposal else None
        if cited_line:
            lines.add(cited_line)
        h.append(f"<h2>Cited source: {escape(incident['module'])}</h2>")
        h.append(f"<div class=muted>Highlighted: line {incident['line']} (from events)"
                 + (f", line {cited_line} (cited by model)" if cited_line else "") + "</div>")
        h.append(_source_listing(files[incident["module"]]["text"], lines))

    # Diagnosis
    h.append("<h2>Diagnosis</h2>")
    if proposal:
        h.append(f"<div class=card><div class=label>Observed failure (from events)</div><p>{escape(proposal['observed_failure'])}</p>"
                 f"<div class=label>Inferred cause (model's interpretation, confidence: {escape(proposal['confidence'])})</div>"
                 f"<p>{escape(proposal['inferred_cause'])}</p><div class=label>Patch rationale</div>"
                 f"<p>{escape(proposal['patch_rationale'])}</p></div>")
    elif model_error:
        h.append(f"<div class='card'>{_badge('model error', 'bad')} <b>{escape(model_error['kind'])}</b>: {escape(model_error['message'])}</div>")
    else:
        h.append("<p class=muted>No usable proposal was produced.</p>")
    findings = meta.get("findings", [])
    if findings:
        h.append("<div class=card><b>Checks on the proposal (done in code)</b><ul>" + "".join(
            f"<li>{_badge(f['level'], 'bad' if f['level'] == 'error' else 'warn')}<code>{escape(f['code'])}</code> {escape(f['message'])}</li>"
            for f in findings) + "</ul></div>")
    elif proposal:
        h.append(f"<div class=card>{_badge('no findings', 'ok')} Cited events exist and belong to the incident, the cited line "
                 "exists, and the patch applies cleanly to the chosen module without changing its interface.</div>")

    # Diff
    h.append("<h2>Proposed diff</h2>")
    h.append(_diff_html(diff) if diff else "<p class=muted>No patch was applied. The baseline is unchanged.</p>")

    # Before / after
    h.append("<h2>Fixed check: before and after</h2>")
    if base_full and base_full.get("results"):
        cand = {r["id"]: r for r in (cand_full or {}).get("results") or []}
        rows = []
        for r in base_full["results"]:
            c = cand.get(r["id"])
            changed = c is not None and (c.get("passed") != r["passed"] or c.get("actual") != r.get("actual"))
            rows.append(f"<tr><td><b>{escape(r['id'])}</b>{' ' + _badge('target', 'info') if r['id'] in targets else ''}</td>"
                        f"<td><code>{escape(json.dumps(r['args']))}</code></td><td>{escape(json.dumps(r['expected']))}</td>"
                        f"<td>{_outcome(r)}</td><td>{_outcome(c)}</td><td>{'changed' if changed else 'same'}</td></tr>")
        h.append("<div class=scroll><table><tr><th>Case</th><th>Args</th><th>Expected (frozen)</th><th>Baseline</th>"
                 "<th>Candidate</th><th>Change</th></tr>" + "".join(rows) + "</table></div>")
    if cand_full and cand_full.get("load_error"):
        h.append(f"<div class=card>{_badge('candidate failed to load', 'bad')} {escape(json.dumps(cand_full['load_error']))}</div>")
    h.append("<div class=card><b>Commands and raw output</b>")
    for name in sorted(p.name for p in (run_dir / "checks").glob("*-full.json")) + sorted(
            p.name for p in (run_dir / "checks").glob("*-case-*.json")) if (run_dir / "checks").exists() else []:
        h.append(_check_details(_load(run_dir, f"checks/{name}")))
    h.append("</div>")

    # Supplementary inputs
    if extra and isinstance(extra.get("baseline"), list):
        from .checks import grade_extra
        base_obs = {o["id"]: o for o in extra["baseline"]}
        cand_obs = {o["id"]: o for o in extra["candidate"]} if isinstance(extra.get("candidate"), list) else {}
        rows = []
        for case in extra["cases"]:
            expected = ("raises " + case["expected_error"]) if "expected_error" in case else (
                "ambiguous" if case.get("ambiguous") else json.dumps(case["expected"]))
            cells = []
            for obs in (base_obs.get(case["id"]), cand_obs.get(case["id"])):
                if obs is None:
                    cells.append('<span class=muted>—</span>')
                    continue
                text = f"raises {obs['error']}" if "error" in obs else json.dumps(obs["actual"])
                grade = grade_extra(case, obs)
                cells.append(escape(text) + " " + {"pass": _badge("pass", "ok"), "fail": _badge("fail", "bad"),
                                                    "ungraded": _badge("ungraded", "warn")}[grade])
            note = escape(case["rule"])
            b, c = base_obs.get(case["id"]), cand_obs.get(case["id"])
            if case.get("ambiguous") and b and c and b != c:
                note = _badge("behaviour changed from baseline: reviewer decision needed", "warn") + " " + note
            rows.append(f"<tr><td>{escape(case['id'])}</td><td><code>{escape(json.dumps(case['args']))}</code></td>"
                        f"<td>{escape(expected)}</td><td>{cells[0]}</td><td>{cells[1]}</td><td class=muted>{note}</td></tr>")
        h.append("<h2>Supplementary inputs (not part of the fixed check)</h2>")
        h.append("<p class=muted>Handwritten additions from <code>fixtures/additions/extra-inputs.json</code>, verified independently "
                 "by <code>scripts/verify_reference.py</code>. They inform the reviewer but do not decide the run status.</p>")
        h.append("<div class=scroll><table><tr><th>Case</th><th>Args</th><th>Expected</th><th>Baseline</th><th>Candidate</th>"
                 "<th>Rule</th></tr>" + "".join(rows) + "</table></div>")

    # Untested / uncertain
    h.append("<h2>What this run does not establish</h2><ul>")
    n_ref = len((base_full or {}).get("results") or [])
    h.append(f"<li>A passing status covers only the {n_ref} recorded reference cases"
             + (f" and is accompanied by {len(extra['cases'])} supplementary inputs" if extra else "")
             + ". It is not a proof of correctness for other inputs.</li>")
    h.append("<li>The candidate ran in a subprocess with a timeout, not in a sandbox. The patch was screened for imports and "
             "dynamic-execution builtins before running.</li>")
    h.append("<li>The inferred cause is the model's interpretation. Only the observed failure and the test results are recorded facts.</li>")
    if proposal and proposal["untested_risks"]:
        h.append("<li>Model-reported risks (not verified by this tool):<ul>"
                 + "".join(f"<li>{escape(r)}</li>" for r in proposal["untested_risks"]) + "</ul></li>")
    h.append("</ul>")

    # Provenance
    h.append("<h2>Provenance and reproduction</h2><div class=card>")
    prov = meta.get("provenance", {})
    resp = _load(run_dir, "model/response.json")
    usage = (resp or {}).get("response", {}).get("usage")
    h.append(f"<p>{_provenance(meta)}</p><pre>{escape(json.dumps({'settings': meta.get('settings'), 'provenance': prov, 'usage': usage}, indent=2))}</pre>")
    if recheck:
        ok = all(recheck.get(k, {}).get("reproduced", True) for k in ("baseline", "candidate"))
        h.append(f"<p>{_badge('re-checked offline', 'ok' if ok else 'bad')} at {escape(recheck['checked_at'])}: saved test "
                 f"results {'reproduced exactly' if ok else 'DID NOT reproduce'} when the fixed check was rerun on the saved candidate.</p>")
    h.append(f"<p class=muted>Artifacts in <code>runs/{escape(meta.get('run_id', ''))}/</code>: "
             + ", ".join(f"<code>{escape(a)}</code>" for a in meta.get("artifacts", [])) + "</p>")
    h.append(f"<p class=muted>Replay without an API key: <code>python -m assistant replay runs/{escape(meta.get('run_id', ''))} --recheck</code></p></div>")
    h.append("</main></body></html>")
    return "\n".join(h)


def write_report(run_dir):
    path = Path(run_dir) / "report.html"
    path.write_text(build_html(run_dir))
    return path
