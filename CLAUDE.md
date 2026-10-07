# Incident-to-patch assistant: project instructions for coding agents

This repository is a take-home exercise (Junior AI Engineer, task E "incidents"). All data is fictional.

## Hard rules

- `fixtures/incidents/` is copied byte-for-byte from the starter pack and is **frozen**. Never edit `baseline.py`, `check.py`, `reference-cases.json`, `events.json` or `domain.md`. `fixtures/frozen.json` holds their hashes.
- `fixtures/incidents/domain.md` is the only source of business rules. Don't add rules. Record anything unclear as an ambiguity in `fixtures/README.md`.
- The application must find the fix by calling a model at runtime. Never hardcode the known zero-quantity fix in `assistant/`. A correct patch may appear only in `tests/` as a labelled test double.
- Expected results come from `scripts/verify_reference.py` and hand arithmetic, never from application output.
- Simulated model responses live only in `fixtures/simulated/` and must carry `"provenance": {"source": "simulated"}`.
- Never commit credentials. The API key is read from `ANTHROPIC_API_KEY` in the environment.

## Conventions

- Use `loguru`'s `logger` in `assistant/`, never `print`. Ruff's `T20` rule enforces this. Only the stdlib-only `scripts/` print.
- Sphinx docstrings (`:param:`, `:type:`, `:returns:`, `:rtype:`, `:raises:`) on every function. pydoclint checks them.
- Never run formatters or fixers on `fixtures/incidents/` or `runs/`.

## Commands

- All checks: `.venv/bin/pre-commit run --all-files`
- Tests: `.venv/bin/python -m pytest -q`
- Independent expectation check: `python3 scripts/verify_reference.py`
- Fixed check (from `fixtures/incidents/`): `python3 check.py baseline.py` (always with a timeout)
