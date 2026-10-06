"""Build the diagnosis request, call Claude, and load saved responses for replay.

Every response record carries provenance: "live" (a real call made in this run),
"replay" (a saved real response reused without a call), or "simulated" (a
handwritten negative-control fixture).
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .evidence import numbered

PROPOSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "relevant_event_ids": {"type": "array", "items": {"type": "string"}},
        "excluded_events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"event_id": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["event_id", "reason"],
                "additionalProperties": False,
            },
        },
        "observed_failure": {"type": "string"},
        "source_location": {
            "type": "object",
            "properties": {"file": {"type": "string"}, "line": {"type": "integer"}},
            "required": ["file", "line"],
            "additionalProperties": False,
        },
        "inferred_cause": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "patch": {
            "type": "object",
            "properties": {
                "file": {"type": "string"},
                "edits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"find": {"type": "string"}, "replace": {"type": "string"}},
                        "required": ["find", "replace"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["file", "edits"],
            "additionalProperties": False,
        },
        "patch_rationale": {"type": "string"},
        "untested_risks": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["relevant_event_ids", "excluded_events", "observed_failure", "source_location",
                 "inferred_cause", "confidence", "patch", "patch_rationale", "untested_risks"],
    "additionalProperties": False,
}


class ModelError(Exception):
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_request(selected_event_id, events, malformed, files, model, effort):
    """Return the exact Messages API parameters. `files` maps a repo-relative name to its text."""
    public_events = [{k: v for k, v in e.items() if not k.startswith("_")} for e in events]
    parts = [
        f"<task>The developer selected event {selected_event_id}. Diagnose that failure and propose a patch.</task>",
        f"<domain_rules path=\"domain.md\">\n{files['domain.md']}\n</domain_rules>",
        f"<events>\n{json.dumps(public_events, indent=2)}\n</events>",
        f"<malformed_events note=\"rejected by the loader; not usable as evidence\">\n{json.dumps(malformed, indent=2)}\n</malformed_events>",
    ]
    for name, text in files.items():
        if name == "domain.md":
            continue
        if name.endswith(".py"):
            parts.append(f"<file path=\"{name}\" numbered=\"true\">\n{numbered(text)}\n</file>")
        else:
            parts.append(f"<file path=\"{name}\">\n{text}\n</file>")
    request = {
        "model": model,
        "max_tokens": config.MAX_TOKENS,
        "system": config.SYSTEM_PROMPT_FILE.read_text(),
        "messages": [{"role": "user", "content": "\n\n".join(parts)}],
        "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": PROPOSAL_SCHEMA}},
    }
    if config.USE_FALLBACKS:
        request["betas"] = ["server-side-fallback-2026-07-01"]
        request["fallbacks"] = "default"
    return request


def call_model(request):
    """Make one real API call. Raises ModelError with a stable `kind` on any failure."""
    import anthropic

    try:
        client = anthropic.Anthropic(timeout=config.API_TIMEOUT_S)
        api = client.beta.messages if "betas" in request else client.messages
        message = api.create(**request)
    except anthropic.AuthenticationError as exc:
        raise ModelError("auth", f"authentication failed: {exc.message}") from exc
    except anthropic.RateLimitError as exc:
        raise ModelError("rate_limited", f"rate limited after SDK retries: {exc.message}") from exc
    except anthropic.BadRequestError as exc:
        raise ModelError("bad_request", exc.message) from exc
    except anthropic.APIStatusError as exc:
        raise ModelError("api_error", f"HTTP {exc.status_code}: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise ModelError("unavailable", f"cannot reach the API: {exc}") from exc
    except anthropic.AnthropicError as exc:  # e.g. no credentials configured
        raise ModelError("client_error", str(exc)) from exc

    record = {
        "provenance": {
            "source": "live",
            "requested_model": request["model"],
            "served_model": message.model,
            "request_id": message._request_id,
            "received_at": _now(),
            "effort": request["output_config"]["effort"],
            "fallbacks": request.get("fallbacks"),
        },
        "response": message.to_dict(),
    }
    return record


def load_response_file(path):
    """Load a saved response. A saved live response is relabelled as a replay; its original provenance is kept."""
    path = Path(path)
    if not path.exists():
        raise ModelError("missing_response_file", f"response file not found: {path}")
    try:
        record = json.loads(path.read_text())
        source = record["provenance"]["source"]
        record["response"]["content"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ModelError("invalid_response_file", f"not a saved response record: {exc}") from exc
    if source in ("live", "replay"):
        original = record["provenance"].get("original", record["provenance"])
        record["provenance"] = {"source": "replay", "replayed_from": str(path), "replayed_at": _now(), "original": original}
    return record


def response_text(record):
    """Return the JSON text of the final answer, or raise ModelError when there is none."""
    response = record["response"]
    stop = response.get("stop_reason")
    if stop == "refusal":
        raise ModelError("refusal", f"model declined: {response.get('stop_details')}")
    if stop == "max_tokens":
        raise ModelError("truncated", "response hit max_tokens before finishing")
    texts = [b["text"] for b in response.get("content", []) if b.get("type") == "text"]
    if not texts:
        raise ModelError("no_text", f"response had no text block (stop_reason={stop})")
    return texts[-1]
