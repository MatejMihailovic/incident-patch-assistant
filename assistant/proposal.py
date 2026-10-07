"""Validate the model's proposal in code and apply its patch to a separate copy.

Every check produces :class:`assistant.models.Finding` objects; errors fail the run, warnings are shown only.
"""

import ast
import difflib
import json

from . import config
from .model import PROPOSAL_SCHEMA
from .models import Finding

# More changed lines than this earns a warning: the brief asks for a patch limited to the defect.
MAX_CHANGED_LINES = 15


class ProposalError(Exception):
    """The model's output is not valid JSON or does not match :data:`assistant.model.PROPOSAL_SCHEMA`."""


def parse_proposal(text):
    """Parse the model's answer and check its structure.

    Replayed and simulated responses never passed API-side schema enforcement, so the
    check is repeated here for every source.

    :param text: Raw answer text, expected to be a JSON object.
    :type text: str
    :returns: The parsed proposal.
    :rtype: dict
    :raises ProposalError: If the text is not JSON or does not match the schema. The message
        lists up to five problems.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProposalError(f"model output is not valid JSON: {exc}") from exc
    problems = _schema_problems(data, PROPOSAL_SCHEMA, "proposal")
    if problems:
        raise ProposalError("model output does not match the schema: " + "; ".join(problems[:5]))
    return data


# JSON Schema type names used in PROPOSAL_SCHEMA, mapped to Python types.
_TYPES = {"object": dict, "array": list, "string": str, "integer": int}


def _schema_problems(value, schema, where):
    """Recursively validate a value against the subset of JSON Schema used by the proposal schema.

    Supports ``type`` (object, array, string, integer), ``enum``, ``required``, ``properties``,
    ``items`` and ``additionalProperties: false``.

    :param value: Value to check.
    :type value: object
    :param schema: Schema node for this value.
    :type schema: dict
    :param where: Dotted path used in messages, e.g. ``"proposal.patch.edits[0]"``.
    :type where: str
    :returns: Problems found; empty when the value is valid.
    :rtype: list[str]
    """
    kind = _TYPES[schema["type"]]
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        return [f"{where} should be {schema['type']}"]
    if "enum" in schema and value not in schema["enum"]:
        return [f"{where} should be one of {schema['enum']}"]
    problems = []
    if kind is dict:
        problems += [f"{where}.{k} is missing" for k in schema["required"] if k not in value]
        problems += [f"{where}.{k} is not allowed" for k in value if k not in schema["properties"]]
        for key, sub in schema["properties"].items():
            if key in value:
                problems += _schema_problems(value[key], sub, f"{where}.{key}")
    elif kind is list:
        for i, item in enumerate(value):
            problems += _schema_problems(item, schema["items"], f"{where}[{i}]")
    return problems


def check_diagnosis(proposal, incident, all_event_ids, module_text):
    """Check that cited evidence exists, belongs to the selected incident, and points at a real line.

    :param proposal: Parsed proposal from :func:`parse_proposal`.
    :type proposal: dict
    :param incident: The incident the developer selected, grouped in code.
    :type incident: assistant.models.Incident
    :param all_event_ids: IDs of every valid event, used to detect invented IDs.
    :type all_event_ids: set[str]
    :param module_text: Current text of the incident's module, used to check the cited line.
    :type module_text: str
    :returns: Findings. Errors: ``no_evidence``, ``unknown_event``, ``unrelated_event_cited``,
        ``wrong_file``, ``line_out_of_range``. Warnings: ``evidence_not_cited``, ``line_differs``.
    :rtype: list[assistant.models.Finding]
    """
    findings = []
    cited = proposal["relevant_event_ids"]
    if not cited:
        findings.append(Finding.error("no_evidence", "The diagnosis cites no event."))
    for event_id in cited:
        if event_id not in all_event_ids:
            findings.append(Finding.error("unknown_event", f"Cited event {event_id} does not exist."))
        elif event_id not in incident.event_ids:
            # Exists, but the code grouped it under a different failure signature.
            message = f"Cited event {event_id} records a different failure than {incident.id}."
            findings.append(Finding.error("unrelated_event_cited", message))
    missing = [e for e in incident.event_ids if e not in cited]
    if missing:
        message = f"Events with the same failure signature were not cited: {', '.join(missing)}."
        findings.append(Finding.warning("evidence_not_cited", message))

    location = proposal["source_location"]
    line_count = len(module_text.splitlines())
    if location["file"] != incident.module:
        message = f"Diagnosis points at {location['file']}, but the events report {incident.module}."
        findings.append(Finding.error("wrong_file", message))
    elif not 1 <= location["line"] <= line_count:
        message = f"Line {location['line']} does not exist ({incident.module} has {line_count} lines)."
        findings.append(Finding.error("line_out_of_range", message))
    elif location["line"] != incident.line:
        # Not an error: the defect can sit near the reported line.
        message = f"Diagnosis cites line {location['line']}; the events report line {incident.line}."
        findings.append(Finding.warning("line_differs", message))
    return findings


def apply_patch(proposal, incident, original_text):
    """Apply the proposal's find/replace edits to an in-memory copy of the module.

    Never touches the file on disk. Each ``find`` must match exactly once, in order.

    :param proposal: Parsed proposal from :func:`parse_proposal`.
    :type proposal: dict
    :param incident: The selected incident; its ``module`` is the only file a patch may target.
    :type incident: assistant.models.Incident
    :param original_text: Current text of the module.
    :type original_text: str
    :returns: A pair ``(candidate_text, findings)``. ``candidate_text`` is ``None`` when the patch is
        rejected (``patch_outside_module``, ``empty_patch``, ``edit_does_not_apply``, ``no_change``,
        ``syntax_error``, ``unsafe_construct``); the candidate must then not be executed. When text
        is returned, ``findings`` may still hold an ``interface_changed`` error or a ``large_patch``
        warning.
    :rtype: tuple[str or None, list[assistant.models.Finding]]
    """
    patch = proposal["patch"]
    target = patch["file"]
    if target != incident.module or target in config.PROTECTED_FILES:
        return None, [
            Finding.error("patch_outside_module", f"Patch targets {target}; only {incident.module} may change.")
        ]
    if not patch["edits"]:
        return None, [Finding.error("empty_patch", "The proposal contains no edits.")]

    # Exact, unique matches keep the patch auditable: no fuzzy matching, no guessing which occurrence.
    text = original_text
    for number, edit in enumerate(patch["edits"], start=1):
        count = text.count(edit["find"]) if edit["find"] else 0
        if count != 1:
            reason = "is empty" if not edit["find"] else f"matches {count} times (must match exactly once)"
            return None, [Finding.error("edit_does_not_apply", f"Edit {number}: find text {reason}.")]
        text = text.replace(edit["find"], edit["replace"], 1)

    if text == original_text:
        return None, [Finding.error("no_change", "The edits leave the module unchanged.")]
    try:
        new_tree = ast.parse(text)
    except SyntaxError as exc:
        return None, [Finding.error("syntax_error", f"Patched module does not parse: {exc}")]

    # Only constructs the patch adds count; the baseline's own code is trusted.
    unsafe = _unsafe_constructs(new_tree) - _unsafe_constructs(ast.parse(original_text))
    if unsafe:
        message = f"Patch introduces {', '.join(sorted(unsafe))}; it was not executed."
        return None, [Finding.error("unsafe_construct", message)]

    findings = []
    old_sig = _signature(ast.parse(original_text), incident.function)
    new_sig = _signature(new_tree, incident.function)
    if new_sig != old_sig:
        message = f"Signature of {incident.function} changed from {old_sig} to {new_sig}."
        findings.append(Finding.error("interface_changed", message))
    changed = sum(
        1 for line in diff_lines(original_text, text, target) if line[:1] in "+-" and line[:3] not in ("+++", "---")
    )
    if changed > MAX_CHANGED_LINES:
        message = f"{changed} changed lines; expected a minimal fix (≤ {MAX_CHANGED_LINES})."
        findings.append(Finding.warning("large_patch", message))
    return text, findings


_BLOCKED_CALLS = {"exec", "eval", "compile", "open", "__import__", "globals", "setattr", "delattr"}


def _unsafe_constructs(tree):
    """Find imports and dynamic-execution builtins.

    The fixed check imports the candidate, so a patch is code that this tool runs.

    :param tree: Parsed module.
    :type tree: ast.Module
    :returns: Descriptions such as ``"an import"`` or ``"a call to eval()"``.
    :rtype: set[str]
    """
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            found.add("an import")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _BLOCKED_CALLS:
            found.add(f"a call to {node.func.id}()")
    return found


def _signature(tree, name):
    """Describe a function's parameters so an interface change can be detected.

    :param tree: Parsed module.
    :type tree: ast.Module
    :param name: Function name to look up.
    :type name: str
    :returns: ``{"args", "defaults", "vararg", "kwarg"}``, or ``None`` if the function is absent.
    :rtype: dict or None
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            args = node.args
            return {
                "args": [a.arg for a in args.args],
                "defaults": len(args.defaults),
                "vararg": bool(args.vararg),
                "kwarg": bool(args.kwarg),
            }
    return None


def diff_lines(old, new, name):
    """Produce a unified diff between two versions of a file.

    :param old: Original text.
    :type old: str
    :param new: Patched text.
    :type new: str
    :param name: File name used in the ``a/`` and ``b/`` headers.
    :type name: str
    :returns: Diff lines, each keeping its line ending.
    :rtype: list[str]
    """
    return list(
        difflib.unified_diff(
            old.splitlines(keepends=True), new.splitlines(keepends=True), fromfile=f"a/{name}", tofile=f"b/{name}"
        )
    )
