# Incident-to-Patch Assistant

A command-line assistant for a developer investigating a recorded software failure. It reads error events and source files from a tiny local repository, and asks Claude for a diagnosis and a minimal patch. It then applies the patch to a **separate copy**, runs the fixed regression checks before and after, and writes an HTML review package.

This is my submission for the Junior AI Engineer take-home, **Alternative E: Incident-to-patch**, starter pack version `2026-10-02`. All data is fictional.

**Result:** the live `claude-opus-5-5` run produced a 2-line patch that fixes the zero-quantity defect without changing the four other reference cases. All five minimum-demonstration checks pass. See [RESULTS.md](RESULTS.md).

## Quick start

Requires Python 3.10+. It was developed with 3.12.10 on Linux.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q                # 26 tests, no API calls
python3 scripts/verify_reference.py          # recompute expected values from the rules
```

### Reproduce the demonstration without an API key

```bash
.venv/bin/python -m assistant demo
```

This replays the saved real model response and runs the two simulated negative controls. It then grades the brief's five checks from the saved artifacts and rewrites [RESULTS.md](RESULTS.md). No model call is made. Each invocation adds three new folders under `runs/`.

To regenerate one saved run's report and confirm that its test results reproduce:

```bash
.venv/bin/python -m assistant replay runs/20261006T161408Z-ev1-live --recheck
```

### Make a live model call

```bash
echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env   # .env is git-ignored
set -a; . ./.env; set +a                      # the app reads the key from the environment only
.venv/bin/python -m assistant run --event EV1
```

The run folder name, status and report path are printed. The exit code is `0` only for status `checked`.

### All commands

| Command | Purpose |
|---|---|
| `python -m assistant incidents` | List incidents grouped from the events, plus any malformed events |
| `python -m assistant run --event EV1 [--model ID] [--effort LEVEL]` | Live diagnosis, patch, checks and report |
| `python -m assistant run --event EV1 --response-file PATH` | Same pipeline using a saved or simulated response, with no API call |
| `python -m assistant replay RUN_DIR [--recheck]` | Rebuild a report offline; `--recheck` reruns the fixed check and compares the results |
| `python -m assistant demo [--live]` | Minimum demonstration plus `RESULTS.md` |

The fixed check command can always be run by hand. From `fixtures/incidents/`:

```bash
timeout 30 python3 check.py baseline.py
timeout 30 python3 check.py ../../runs/20261006T161408Z-ev1-live/candidate.py
```

## How it works

```
events.json + extra-events.json ──► load & validate ──► group by (function, error, source) ──► select incident
                                       │ malformed events reported, not fatal
baseline.py, check.py, reference-cases.json, domain.md ──► read at runtime, hashed
                                                                      │
                     frozen-hash check ──► fixed check on BASELINE (cases failing with the incident's error = targets; none → stop)
                                                                      │
                     Claude (structured JSON) ◄── rules + all events + numbered source + tests
                                                                      │
                     validate in code: schema · cited events exist and belong to the incident ·
                     cited line exists · patch only touches the incident's module · each find matches once ·
                     parses · no imports/exec · signature unchanged
                                                                      │
                     apply to runs/<id>/candidate.py (baseline never written)
                                                                      │
                     fixed check on CANDIDATE (full set + each target) · supplementary inputs on both
                                                                      │
                     frozen-hash check again ──► status checked | failed ──► report.html
```

| Module | Responsibility |
|---|---|
| [`assistant/evidence.py`](assistant/evidence.py) | Load events from several files, report malformed ones, group them into incidents by failure signature |
| [`assistant/model.py`](assistant/model.py) | Build the request, call the API, load saved or simulated responses, record provenance |
| [`assistant/proposal.py`](assistant/proposal.py) | Re-validate the schema, check the diagnosis against the evidence, apply and screen the patch |
| [`assistant/checks.py`](assistant/checks.py) | Run `check.py` with a timeout, run the supplementary inputs |
| [`assistant/integrity.py`](assistant/integrity.py) | Compare frozen files against `fixtures/frozen.json` |
| [`assistant/pipeline.py`](assistant/pipeline.py) | Orchestrate one run and save every artifact as soon as it exists; offline recheck |
| [`assistant/report.py`](assistant/report.py) | Self-contained HTML report built only from a run's saved files |
| [`assistant/demo.py`](assistant/demo.py) | Minimum demonstration and grading of the five checks |

### What the model decides vs. what the code verifies

The model **proposes**:
- which events are evidence
- the source location
- the inferred cause
- the find/replace patch
- the untested risks

The code **decides the status**, using only recorded facts. A run is `checked` only if all of these hold:
1. every cited event exists and has the same failure signature as the selected incident (grouped in code, independently of the model)
2. the cited file and line exist
3. the patch applies cleanly to the incident's module only and keeps the function signature
4. all frozen reference cases pass on the candidate, including every case that failed on the baseline
5. the frozen files are unchanged

Anything else is `failed`, with the reasons listed. The model's own claims, such as "this should pass the tests", never count as evidence. The report separates the **observed failure** (from events) from the **inferred cause** (the model's interpretation).

### A run's saved package

Each run writes `runs/<UTC time>-<event>-<live|replay|sim-name>/`:

| File | Content |
|---|---|
| `run.json` | Status, reasons, incident, settings, provenance, code findings, frozen-hash results before and after |
| `evidence/events.json`, `evidence/files.json` | Events as loaded (with origin file), malformed events, incidents; source snapshots with hashes |
| `model/request.json` | The exact API parameters sent |
| `model/response.json` | The full API response with provenance: `live` (with request ID), `replay` (original kept) or `simulated` |
| `model/proposal.json`, `model/error.json` | The parsed proposal, or the model failure |
| `candidate.py`, `patch.diff` | The patched copy and its unified diff (only if the patch applied) |
| `checks/*.json` | Every check invocation: command, exit code, stdout, stderr, duration |
| `checks/extra-inputs.json` | Supplementary inputs on the baseline and the candidate |
| `report.html` | The review report |

## Model configuration

| Setting | Value | Why |
|---|---|---|
| Provider / SDK | Anthropic, `anthropic==1.11.0` Python SDK | Model access agreed for the exercise |
| Model | `claude-opus-5-5` (`INCIDENT_MODEL` or `--model` to change) | Current default Claude model |
| Effort | `medium` (`INCIDENT_EFFORT` or `--effort`) | Small, well-specified task; the first live run used about 1.2k output tokens |
| Thinking | Adaptive (the model default; cannot be disabled on this model) | — |
| Output format | `output_config.format` JSON schema ([`PROPOSAL_SCHEMA`](assistant/model.py)) | The API enforces the shape on live calls; the code re-validates every source |
| Refusal fallback | Server-side `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`); `INCIDENT_FALLBACKS=0` to disable | A refusal becomes a recorded failure, not a crash |
| `max_tokens` / timeout / retries | 16000 / 300 s / SDK default of 2 retries on 408, 409, 429 and 5xx | — |
| Temperature | Not set (not supported on this model) | Replay, not sampling settings, provides reproducibility |
| System prompt | [`prompts/system.md`](prompts/system.md) | Repository content is wrapped in tags and declared to be data, not instructions |

One model attempt is made per run. A bounded retry using test feedback was an optional enhancement and is not implemented.

## Data and assumptions

The fixtures are documented in [fixtures/README.md](fixtures/README.md): the generation method, the hand-arithmetic table and the independent verification. In summary:

- `fixtures/incidents/` is the starter pack, **byte-for-byte**, with SHA-256 hashes in `fixtures/frozen.json`. It is never modified, and the app refuses to run if the hashes differ.
- **Additions, all handwritten with no randomness:** EV4 is the same defect with a different input and a traceback. EV5 is a deliberately malformed event. There are seven supplementary boundary inputs, reported separately from the fixed check.
- **The five reference cases** were verified before any model run, by hand and by [`scripts/verify_reference.py`](scripts/verify_reference.py), which re-derives the rules with `Decimal` half-up rounding without importing the baseline. The baseline check output was saved in `fixtures/baseline-results/` before the first model call.
- **Money and comparison:** integer cents. A case passes when `check.py` reports `actual == expected`, an exact integer comparison.
- **Ambiguity left open:** for `(-1, 0)`, domain rule 1 says both that quantity zero returns zero and that negative arguments raise `ValueError`. The reference cases don't settle which wins. It is reported, not graded. The live patch kept validation first (`ValueError`), and the model independently listed this conflict among its untested risks.
- **Negative controls:** `fixtures/simulated/` holds two handwritten responses labelled `"source": "simulated"`. One patch does not apply; the other applies but breaks half-up rounding. Both must end `failed` with the frozen files intact.

## Known limitations

- **Limited evidence.** A `checked` status covers five reference cases and seven supplementary inputs. It is not a proof of correctness, and the defect is a toy example that says little about unfamiliar production code.
- **Not a sandbox.** The candidate runs in a subprocess with a timeout. The AST screen (no imports, no `exec`/`eval`/`open`/…) is a heuristic, not a security boundary. Don't point this at untrusted models or repositories.
- **Strict grouping.** Incidents are grouped by exact `(function, error, source)`. The same bug reported at a different line would form a separate incident.
- **Exact-text patches.** Patches are find/replace edits that must match the current text exactly once. That keeps them auditable, but a model that misquotes whitespace gets `failed`, not a fuzzy match.
- **One attempt, one module.** There is no retry with test feedback.
- **Unreproduced incidents are not patched.** Target cases are the reference cases that fail on the baseline *with the incident's error type*. If there is none, as with EV3, which records expected `ValueError` validation, the run ends `failed` *before* the model call, because a patch could not be verified. The reference cases are frozen, so the assistant cannot add a reproducing test itself. A failure that returns a wrong value, rather than raising, cannot be linked to an incident this way.
- **Variable live output.** Live calls are not deterministic. Reproducibility comes from replaying `model/response.json`, which `demo` and `replay` do.
- **No `.env` loading in the app.** The app reads `ANTHROPIC_API_KEY` from the environment only, so you must export it, as in Quick start.

## Time spent

These figures are approximate, taken from commit times and the working session (2026-10-06).

| Phase | Time |
|---|---|
| Reading the brief and starter pack, planning | ~0:20 |
| Data preparation: copy and freeze, independent verification, additions, ambiguity notes | ~0:20 |
| Pipeline, validation, report, CLI, negative controls, tests | ~0:45 |
| Docstrings, first live run, demo grading, target-selection fix, README | ~1:05 |
| **Total so far** | **~2:30** |

Still to do: completing the `ai-workflow/` manifest and README, and the short presentation.

## LLM usage

- **Development:** Claude Code (VS Code extension) running Claude Opus 5.5 generated most of the code, fixtures and docs, from my instructions and review. The project instructions it followed are in [CLAUDE.md](CLAUDE.md).
- **Inside the application:** `claude-opus-5-5` through the Anthropic API, as described in Model configuration.
- **Corrections made during development:**
  1. The first run of the "breaks rounding" negative control ended `failed` for the wrong reason, "check could not evaluate the candidate". The candidate path was relative but `check.py` runs from the task folder. The pipeline correctly refused to report success, but the failure reason was wrong. Paths are now resolved, and a test asserts the expected reason (`Regression … rounding`).
  2. While documenting limitations, I asked what happens if a developer selects EV3. The generated pipeline treated *every* baseline failure as the target, so EV3 (an expected `ValueError`) would have sent the model off to "fix" the unrelated zero-quantity case. A new test, which fails if the model is called at all, exposed this. Targets are now the failures matching the incident's error type, and unreproduced incidents stop before the model call.
- The full record of tools, configuration and versions is in [`ai-workflow/`](ai-workflow/).

## Repository layout

```
assistant/            application package (python -m assistant)
prompts/system.md     system prompt sent with every request
fixtures/incidents/   frozen starter files (never modified)
fixtures/additions/   handwritten extra events and supplementary inputs
fixtures/simulated/   labelled simulated responses (negative controls)
fixtures/baseline-results/  fixed-check output on the baseline, saved before any model call
scripts/verify_reference.py independent expectation check
runs/                 saved runs: live, replays, negative controls
tests/                pytest suite (no network)
RESULTS.md            minimum-demonstration results table (generated)
ai-workflow/          AI tooling configuration and manifest
CLAUDE.md             project instructions for the coding agent
```
