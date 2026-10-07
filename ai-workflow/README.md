# AI workflow used during this exercise

The machine-readable record is in [manifest.json](manifest.json). This page explains it.

## Tools and models

| Use | Tool / model | Settings |
|---|---|---|
| Development | Claude Code VS Code extension `anthropic.claude-code` 2.1.289, auto-updated to 2.1.292 during the exercise; VS Code 1.138.0 on Linux | Model **Claude Opus 5.5** (`claude-opus-5-5`), effort `high` from user-level `modelSettings` ([snapshot](snapshots/user-settings.json)). Default permission mode. One session. |
| Inside the application | Anthropic Messages API through `anthropic==1.11.0` | `claude-opus-5-5`, effort `medium`, `max_tokens` 16000, structured output (JSON schema), server-side refusal fallback (beta), 300 s timeout, 2 SDK retries. Details in the main [README](../README.md#model-configuration). |

The two are kept separate. Claude Code wrote and refactored the code. The application makes its own API call at runtime, and that call's settings live in [`assistant/config.py`](../assistant/config.py) and [`assistant/model.py`](../assistant/model.py).

Nothing was left at an unknown value. Defaults that were not changed: temperature (not supported on this model), thinking (adaptive, always on for this model) and SDK retries.

## Configuration files

| What | Where | Status |
|---|---|---|
| Project instructions for the coding agent | [`CLAUDE.md`](../CLAUDE.md), in its normal location | used |
| Application system prompt | [`prompts/system.md`](../prompts/system.md) | used |
| User-level Claude Code settings | `~/.claude/settings.json` → [snapshots/user-settings.json](snapshots/user-settings.json) | used. The whole file, which only sets effort levels; nothing removed. |
| Auto-memory (this project) | `~/.claude/projects/<project>/memory/` → [snapshots/memory/](snapshots/memory/) | redacted. The email address is replaced. |
| Git pre-commit hooks and their scripts | [`.pre-commit-config.yaml`](../.pre-commit-config.yaml), [`scripts/check_frozen.py`](../scripts/check_frozen.py), [`scripts/verify_reference.py`](../scripts/verify_reference.py) | used |
| Lint, format, test and docstring rules | [`pyproject.toml`](../pyproject.toml) (replaced `pytest.ini` in `a8f848a`) | used |
| Skill: `claude-api` | Bundled with Claude Code | used once, as an SDK reference; not exportable |
| Starter-pack skill `generate-assignment-data` | `client-ai-starter-pack/skills/` | not-used. It was read, not installed. Fixtures were written by hand following its guidance. |
| Subagents, custom skills, Claude Code hooks | — | not-used |
| MCP servers | — | not-used. None configured; a claude.ai connector was available but not used. |
| Project `.claude/settings*.json`, permission allowlists | — | not-used. All commands were approved interactively. |
| Environment variable names | [.env.example](.env.example) | Values redacted; the real `.env` is git-ignored |

**Earlier versions.** Every configuration file is in git, and the manifest lists the commit of each change. `git log --follow -- CLAUDE.md` shows the three versions of the agent instructions.

**Redactions.** One email address in the memory snapshot. Nothing else in the user-level files was relevant: `~/.claude.json` holds account and cache data, has no MCP servers, and was not copied. Credentials were not read.

## One workflow example

See [workflow-example.md](workflow-example.md). The instruction was "Do the demo and main Readme". It was shaped by the `CLAUDE.md` rule that expected values must never come from application output. It was checked by an offline demo run and a test written specifically to contradict a claim in the draft README. That test failed and exposed a real bug: an incident could inherit another incident's failing case as its target. The pipeline was corrected in `eaa67d4`.

Two corrections came from me, the user, rather than from tests:
- I rejected a commit that carried an AI attribution trailer. Commits are made under my name only, and the rule is stored in memory.
- I declined a headless-browser screenshot of the report. Reports were checked by opening them myself and by comparing their text before and after the report refactor.

## Reproduce or replay

| Goal | Command (from the repository root) | Needs |
|---|---|---|
| Replay the saved real response and grade the brief's checks | `.venv/bin/python -m assistant demo` | Python 3.10+, `pip install -r requirements.txt`; **no API key** |
| Re-check one saved run against the fixed check | `.venv/bin/python -m assistant replay runs/20261006T161408Z-ev1-live --recheck` | Same |
| New live call | `set -a; . ./.env; set +a; .venv/bin/python -m assistant run --event EV1` | `ANTHROPIC_API_KEY` (see [.env.example](.env.example)) |
| All checks | `.venv/bin/pre-commit run --all-files` | `pip install -r requirements-dev.txt` |

**Where configuration belongs.** `CLAUDE.md` and `prompts/system.md` stay where they are. To reproduce the development setup, copy `snapshots/user-settings.json` into `~/.claude/settings.json`, or merge its `modelSettings`. The memory snapshots are for reference only; Claude Code writes its own.

**Hooks.** The pre-commit hooks run on `git commit` only after `pre-commit install`. They read the repository and modify only formatting, and never in `fixtures/incidents/` or `runs/`. The `pytest` hook runs the test suite, which makes no network calls. Read `.pre-commit-config.yaml` before enabling them.

**Saved real responses.** The live call's full response, request ID and token usage are in [`runs/20261006T161408Z-ev1-live/model/response.json`](../runs/20261006T161408Z-ev1-live/model/response.json). Replays are labelled `replay` and keep the original provenance. Simulated responses are labelled `simulated`.

## Decisions and limitations

- **Why this setup.** The task is small and well specified, so one agent session with written project rules (`CLAUDE.md`) fitted better than subagents or custom skills. The risks I most wanted to control were the agent editing the frozen answer key, hardcoding the known fix, or grading itself. Each is a written rule, and three are also enforced in code or hooks: the frozen hashes, the test-double rule and the independent verifier.
- **Why effort `medium` in the app.** One focused diagnosis of a 4-line module. The live call used about 3.7k input and 1.2k output tokens and produced a correct, minimal patch. Higher effort would cost more without a measured benefit.
- **What I would change.** Commit `.claude/settings.json` with a small allowlist for read-only commands (`git status`, `pytest`, `ruff`) to cut permission prompts. Add a Claude Code `PostToolUse` hook that runs `scripts/check_frozen.py` after edits, so a frozen-file change is caught immediately instead of at commit time. Neither was needed for this exercise, so I didn't add them just to fill the manifest.
- **Not exportable.** The bundled `claude-api` skill ships with the Claude Code install and is not a project file. The version is recorded instead.
