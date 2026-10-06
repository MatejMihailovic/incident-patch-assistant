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
    """Any failure to obtain a usable model response.

    :param kind: Stable machine-readable category, e.g. ``"auth"``, ``"unavailable"``,
        ``"refusal"``, ``"truncated"`` or ``"invalid_response_file"``.
    :type kind: str
    :param message: Human-readable explanation shown in the run status and report.
    :type message: str
    """

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


def _now():
    """Current UTC time for provenance records.

    :returns: ISO 8601 timestamp with second precision.
    :rtype: str
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_request(selected_event_id, events, malformed, files, model, effort):
    """Build the exact Messages API parameters for one diagnosis request.

    Repository content is wrapped in XML-style tags so the system prompt can tell the
    model to treat it as data rather than instructions.

    :param selected_event_id: Event the developer selected, e.g. ``"EV1"``.
    :type selected_event_id: str
    :param events: All valid events; internal keys starting with ``_`` are stripped.
    :type events: list[dict]
    :param malformed: Loader problems, shown to the model as unusable evidence.
    :type malformed: list[dict]
    :param files: Maps a repository-relative file name to its text. Must include ``"domain.md"``.
        Python files are sent with line numbers.
    :type files: dict[str, str]
    :param model: Model ID, e.g. ``"claude-opus-5-5"``.
    :type model: str
    :param effort: Effort level: ``"low"``, ``"medium"``, ``"high"``, ``"xhigh"`` or ``"max"``.
    :type effort: str
    :returns: Keyword arguments for ``messages.create`` (or ``beta.messages.create`` when
        ``betas`` is present). JSON-serialisable, so the request is saved verbatim with the run.
    :rtype: dict
    """
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
    """Make one real API call.

    The SDK already retries connection errors, 408, 409, 429 and 5xx responses.

    :param request: Parameters from :func:`build_request`.
    :type request: dict
    :returns: ``{"provenance": {...}, "response": {...}}``. Provenance has ``source="live"``, the
        requested and served model, the request ID and a timestamp. ``response`` is the full API
        message as a dict.
    :rtype: dict
    :raises ModelError: On authentication, rate-limit, bad-request, server, connection or
        client-configuration errors (for example, no credentials).
    """
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
    """Load a saved or simulated response instead of calling the API.

    A saved live response is relabelled ``source="replay"`` and keeps its original provenance under
    ``original``. Simulated fixtures keep ``source="simulated"``.

    :param path: A ``model/response.json`` from an earlier run, or a file in ``fixtures/simulated/``.
    :type path: pathlib.Path or str
    :returns: A response record in the same shape as :func:`call_model` returns.
    :rtype: dict
    :raises ModelError: ``missing_response_file`` if the path does not exist, or
        ``invalid_response_file`` if it is not a response record.
    """
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
    """Extract the final answer text from a response record.

    :param record: Record from :func:`call_model` or :func:`load_response_file`.
    :type record: dict
    :returns: Text of the last ``text`` content block, which should be the proposal JSON.
    :rtype: str
    :raises ModelError: ``refusal`` if the model declined, ``truncated`` if it hit ``max_tokens``,
        or ``no_text`` if there is no text block.
    """
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
