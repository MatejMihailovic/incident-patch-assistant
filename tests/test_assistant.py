"""Tests for the assistant's guardrails. No test calls the real API.

TEST_DOUBLE_FIX below is a handwritten correct patch used only to exercise the
"checked" path of the pipeline. The application never sees it; real proposals
come from the model at runtime.
"""
import json

import pytest

from assistant import config, evidence, integrity, model, pipeline, proposal as proposals

TEST_DOUBLE_FIX = {
    "find": "    return (2 * total_cents + quantity) // (2 * quantity)",
    "replace": "    if quantity == 0:\n        return 0\n    return (2 * total_cents + quantity) // (2 * quantity)",
}


def make_proposal(**overrides):
    base = {
        "relevant_event_ids": ["EV1", "EV2", "EV4"],
        "excluded_events": [{"event_id": "EV3", "reason": "different failure"}],
        "observed_failure": "ZeroDivisionError at baseline.py:4 for quantity 0",
        "source_location": {"file": "baseline.py", "line": 4},
        "inferred_cause": "division by 2 * quantity when quantity is 0",
        "confidence": "high",
        "patch": {"file": "baseline.py", "edits": [dict(TEST_DOUBLE_FIX)]},
        "patch_rationale": "guard zero quantity",
        "untested_risks": [],
    }
    base.update(overrides)
    return base


def write_response(tmp_path, proposal_obj, source="simulated"):
    path = tmp_path / "responses" / "response.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"provenance": {"source": source, "description": "test double"},
                                "response": {"model": "test", "stop_reason": "end_turn",
                                             "content": [{"type": "text", "text": json.dumps(proposal_obj)}]}}))
    return path


@pytest.fixture
def incident():
    events, _ = evidence.load_events()
    return evidence.find_incident(evidence.group_incidents(events), "EV1")


@pytest.fixture
def baseline_text():
    return (config.REPO_DIR / "baseline.py").read_text()


# Evidence ---------------------------------------------------------------------------------------

def test_events_grouped_by_failure_signature():
    events, malformed = evidence.load_events()
    groups = {i.id: i.event_ids for i in evidence.group_incidents(events)}
    assert groups == {"INC-EV1": ["EV1", "EV2", "EV4"], "INC-EV3": ["EV3"]}
    assert [m["event_id"] for m in malformed] == ["EV5"]


def test_missing_and_invalid_event_files_are_reported(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    events, problems = evidence.load_events([tmp_path / "absent.json", bad, config.REPO_DIR / "events.json"])
    assert len(events) == 3
    assert [p["problem"].split(":")[0] for p in problems] == ["event file not found", "invalid JSON"]


# Proposal validation ------------------------------------------------------------------------------

def test_parse_rejects_non_json_and_schema_violations():
    with pytest.raises(proposals.ProposalError, match="not valid JSON"):
        proposals.parse_proposal("Sure! Here is the fix:")
    with pytest.raises(proposals.ProposalError, match="confidence"):
        proposals.parse_proposal(json.dumps(make_proposal(confidence="certain")))
    with pytest.raises(proposals.ProposalError, match="missing"):
        proposals.parse_proposal(json.dumps({"patch": {}}))


def test_citing_unrelated_or_unknown_event_is_an_error(incident, baseline_text):
    ids = {"EV1", "EV2", "EV3", "EV4"}
    findings = proposals.check_diagnosis(make_proposal(relevant_event_ids=["EV1", "EV3", "EV9"]), incident, ids, baseline_text)
    codes = {f["code"]: f["level"] for f in findings}
    assert codes["unrelated_event_cited"] == "error"
    assert codes["unknown_event"] == "error"
    assert codes["evidence_not_cited"] == "warning"


def test_cited_line_must_exist(incident, baseline_text):
    findings = proposals.check_diagnosis(make_proposal(source_location={"file": "baseline.py", "line": 40}),
                                         incident, {"EV1", "EV2", "EV4"}, baseline_text)
    assert [f["code"] for f in findings] == ["line_out_of_range"]


@pytest.mark.parametrize("patch, code", [
    ({"file": "check.py", "edits": [TEST_DOUBLE_FIX]}, "patch_outside_module"),
    ({"file": "reference-cases.json", "edits": [TEST_DOUBLE_FIX]}, "patch_outside_module"),
    ({"file": "baseline.py", "edits": []}, "empty_patch"),
    ({"file": "baseline.py", "edits": [{"find": "return 1", "replace": "return 0"}]}, "edit_does_not_apply"),
    ({"file": "baseline.py", "edits": [{"find": "quantity", "replace": "qty"}]}, "edit_does_not_apply"),
    ({"file": "baseline.py", "edits": [{"find": "    return (2", "replace": "    return ((2"}]}, "syntax_error"),
    ({"file": "baseline.py", "edits": [{"find": "def unit_price", "replace": "import os\ndef unit_price"}]}, "unsafe_construct"),
])
def test_bad_patches_are_rejected_without_a_candidate(incident, baseline_text, patch, code):
    candidate, findings = proposals.apply_patch(make_proposal(patch=patch), incident, baseline_text)
    assert candidate is None
    assert findings[0]["code"] == code


def test_interface_change_is_an_error(incident, baseline_text):
    patch = {"file": "baseline.py", "edits": [{"find": "def unit_price(total_cents, quantity):",
                                               "replace": "def unit_price(total_cents, quantity, strict=False):"}]}
    _, findings = proposals.apply_patch(make_proposal(patch=patch), incident, baseline_text)
    assert [f["code"] for f in findings] == ["interface_changed"]


def test_valid_patch_applies_only_to_a_copy(incident, baseline_text):
    candidate, findings = proposals.apply_patch(make_proposal(), incident, baseline_text)
    assert findings == []
    assert "if quantity == 0" in candidate
    assert (config.REPO_DIR / "baseline.py").read_text() == baseline_text


# Pipeline end to end (no network) ---------------------------------------------------------------

def load_meta(run_dir):
    return json.loads((run_dir / "run.json").read_text())


def test_correct_patch_is_checked_and_baseline_untouched(tmp_path):
    before = integrity.verify_frozen()
    run_dir = pipeline.execute("EV1", response_file=write_response(tmp_path, make_proposal()), runs_dir=tmp_path / "runs")
    meta = load_meta(run_dir)
    assert meta["status"] == "checked", meta["status_reasons"]
    assert meta["target_cases"] == ["zero-quantity"]
    base = json.loads((run_dir / "checks/baseline-case-zero-quantity.json").read_text())
    cand = json.loads((run_dir / "checks/candidate-case-zero-quantity.json").read_text())
    assert base["results"][0]["error"] == "ZeroDivisionError" and cand["results"][0]["passed"]
    assert integrity.verify_frozen() == before and before["ok"]
    assert (run_dir / "report.html").exists() and (run_dir / "patch.diff").read_text().startswith("--- a/baseline.py")


def test_unrelated_evidence_fails_even_when_tests_pass(tmp_path):
    response = write_response(tmp_path, make_proposal(relevant_event_ids=["EV1", "EV3"]))
    meta = load_meta(pipeline.execute("EV1", response_file=response, runs_dir=tmp_path / "runs"))
    assert meta["status"] == "failed"
    assert any("different failure" in r for r in meta["status_reasons"])


@pytest.mark.parametrize("name, reason", [
    ("patch-does-not-apply", "patch was rejected"),
    ("patch-breaks-rounding", "Regression"),
])
def test_simulated_negative_controls_fail(tmp_path, name, reason):
    meta = load_meta(pipeline.execute("EV1", response_file=config.FIXTURES / "simulated" / f"{name}.json",
                                      runs_dir=tmp_path / "runs"))
    assert meta["status"] == "failed"
    assert meta["provenance"]["source"] == "simulated"
    assert any(reason in r for r in meta["status_reasons"])
    assert integrity.verify_frozen()["ok"]


def test_model_unavailable_is_a_visible_failure(tmp_path, monkeypatch):
    def unavailable(_request):
        raise model.ModelError("unavailable", "cannot reach the API")
    monkeypatch.setattr(model, "call_model", unavailable)
    run_dir = pipeline.execute("EV1", runs_dir=tmp_path / "runs")
    meta = load_meta(run_dir)
    assert meta["status"] == "failed" and "unavailable" in meta["status_reasons"][0]
    assert json.loads((run_dir / "model/error.json").read_text())["kind"] == "unavailable"
    assert not (run_dir / "candidate.py").exists()


@pytest.mark.parametrize("stop_reason, kind", [("refusal", "refusal"), ("max_tokens", "truncated")])
def test_refusal_and_truncation_are_not_parsed(stop_reason, kind):
    with pytest.raises(model.ModelError) as exc:
        model.response_text({"response": {"stop_reason": stop_reason, "content": [{"type": "text", "text": "{"}]}})
    assert exc.value.kind == kind


def test_unknown_event_fails_cleanly(tmp_path):
    meta = load_meta(pipeline.execute("EV42", response_file=tmp_path / "unused.json", runs_dir=tmp_path / "runs"))
    assert meta["status"] == "failed" and "EV42" in meta["status_reasons"][0]


def test_tampered_fixture_stops_the_run_before_the_model(tmp_path, monkeypatch):
    manifest = json.loads(config.FROZEN_MANIFEST.read_text())
    manifest["files"]["fixtures/incidents/reference-cases.json"] = "0" * 64
    fake = tmp_path / "frozen.json"
    fake.write_text(json.dumps(manifest))
    monkeypatch.setattr(config, "FROZEN_MANIFEST", fake)
    run_dir = pipeline.execute("EV1", response_file=write_response(tmp_path, make_proposal()), runs_dir=tmp_path / "runs")
    assert load_meta(run_dir)["status"] == "failed"
    assert not (run_dir / "model").exists()


def test_saved_live_response_replays_and_rechecks(tmp_path):
    response = write_response(tmp_path, make_proposal(), source="live")
    run_dir = pipeline.execute("EV1", response_file=response, runs_dir=tmp_path / "runs")
    meta = load_meta(run_dir)
    assert meta["provenance"]["source"] == "replay"
    comparison = pipeline.recheck(run_dir)
    assert comparison["baseline"]["reproduced"] and comparison["candidate"]["reproduced"]
