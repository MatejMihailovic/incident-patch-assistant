# Workflow example: the demo, a failing test, and a corrected pipeline

## The instruction

Given to Claude Code (Claude Opus 5.5) on 2026-10-06, after the pipeline and the first live run existed:

> Do the demo and main Readme

## Configuration that shaped the result

- **[CLAUDE.md](../CLAUDE.md), commit `793567f`:** "Expected results come from `scripts/verify_reference.py` and hand arithmetic, never from application output." Because of this rule, the demo grades the brief's five checks against the frozen `reference-cases.json` and the frozen-file hashes, never against what the assistant reported about itself (see [`assistant/demo.py`](../assistant/demo.py)).
- **CLAUDE.md:** "Simulated model responses live only in `fixtures/simulated/` and must carry `"provenance": {"source": "simulated"}`." Check 4 of the demo therefore also verifies that the negative controls are labelled as simulated.
- **User settings ([snapshots/user-settings.json](snapshots/user-settings.json)):** effort `high` for `claude-opus-5-5`.

## How the result was checked

1. I ran `python -m assistant demo` with `ANTHROPIC_API_KEY` removed from the environment, so it could only replay. All 5 checks passed, and [RESULTS.md](../RESULTS.md) was written.
2. I read the generated `RESULTS.md` table and compared it with the five checks in the brief.
3. While writing the README's limitations section, the agent claimed that selecting EV3 (an expected `ValueError`) would "end `failed` or with nothing to fix". Instead of trusting that sentence, it wrote a test that fails if the model is called at all:

```python
def test_unreproduced_incident_stops_before_the_model(tmp_path, monkeypatch):
    def must_not_call(_request):
        raise AssertionError("model must not be called")

    monkeypatch.setattr(model, "call_model", must_not_call)
    run_dir = pipeline.execute("EV3", runs_dir=tmp_path / "runs")
    ...
```

The first version of the fix (stop if no baseline case fails) did **not** pass:

```
E       AssertionError: model must not be called
tests/test_assistant.py:191: AssertionError
```

## The correction

The test exposed a real bug in the generated pipeline. "Target cases" were *every* case failing on the baseline. EV3's incident (`ValueError` at `baseline.py:3`) therefore inherited the unrelated `zero-quantity` failure, and the model would have been asked to "fix" a different incident's case.

The fix ([`_baseline_checks`](../assistant/pipeline.py)) defines targets as the baseline failures **with the incident's error type**. If there is none, the run stops before the model call:

```
No reference case reproduces ValueError on the baseline, so a patch for INC-EV3 could not be verified;
the model was not called. (Check whether the event records expected behaviour.)
```

Regressions are now measured against all baseline failures, not only targets, and unrelated failures that remain are reported separately. The test passes, the README's limitation was rewritten to describe the real behaviour, and the change is in commit `eaa67d4`.

## A second, smaller correction

The first run of the "breaks rounding" negative control ended `failed` for the wrong reason: "the fixed check could not evaluate the candidate". The run directory path was relative, but `check.py` runs from the task folder. The pipeline correctly refused to report success, but its reason was wrong. Paths are now resolved to absolute paths, and the test `test_simulated_negative_controls_fail` asserts the intended reason (`Regression … rounding`). Commit `cde3b1e`.
