"""Validate the model's proposal in code and apply its patch to a separate copy.

Findings have a level: "error" makes the run fail; "warning" is shown to the reviewer.
"""
import ast
import difflib
import json

from . import config
from .model import PROPOSAL_SCHEMA

MAX_CHANGED_LINES = 15


class ProposalError(Exception):
    pass


def finding(level, code, message):
    return {"level": level, "code": code, "message": message}


def parse_proposal(text):
    """Parse and structurally validate. Replayed and simulated responses never passed API-side schema
    enforcement, so the check is repeated here for every source."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProposalError(f"model output is not valid JSON: {exc}") from exc
    problems = _schema_problems(data, PROPOSAL_SCHEMA, "proposal")
    if problems:
        raise ProposalError("model output does not match the schema: " + "; ".join(problems[:5]))
    return data


_TYPES = {"object": dict, "array": list, "string": str, "integer": int}


def _schema_problems(value, schema, where):
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
    """Check that cited evidence exists, belongs to the selected incident, and points at a real line."""
    findings = []
    cited = proposal["relevant_event_ids"]
    if not cited:
        findings.append(finding("error", "no_evidence", "The diagnosis cites no event."))
    for event_id in cited:
        if event_id not in all_event_ids:
            findings.append(finding("error", "unknown_event", f"Cited event {event_id} does not exist."))
        elif event_id not in incident.event_ids:
            findings.append(finding("error", "unrelated_event_cited",
                                    f"Cited event {event_id} records a different failure than {incident.id}."))
    missing = [e for e in incident.event_ids if e not in cited]
    if missing:
        findings.append(finding("warning", "evidence_not_cited",
                                f"Events with the same failure signature were not cited: {', '.join(missing)}."))

    location = proposal["source_location"]
    line_count = len(module_text.splitlines())
    if location["file"] != incident.module:
        findings.append(finding("error", "wrong_file",
                                f"Diagnosis points at {location['file']}, but the events report {incident.module}."))
    elif not 1 <= location["line"] <= line_count:
        findings.append(finding("error", "line_out_of_range",
                                f"Line {location['line']} does not exist ({incident.module} has {line_count} lines)."))
    elif location["line"] != incident.line:
        findings.append(finding("warning", "line_differs",
                                f"Diagnosis cites line {location['line']}; the events report line {incident.line}."))
    return findings


def apply_patch(proposal, incident, original_text):
    """Return (candidate_text or None, findings). Never touches the file on disk."""
    patch = proposal["patch"]
    target = patch["file"]
    if target != incident.module or target in config.PROTECTED_FILES:
        return None, [finding("error", "patch_outside_module",
                              f"Patch targets {target}; only {incident.module} may change.")]
    if not patch["edits"]:
        return None, [finding("error", "empty_patch", "The proposal contains no edits.")]

    text = original_text
    for number, edit in enumerate(patch["edits"], start=1):
        count = text.count(edit["find"]) if edit["find"] else 0
        if count != 1:
            reason = "is empty" if not edit["find"] else f"matches {count} times (must match exactly once)"
            return None, [finding("error", "edit_does_not_apply", f"Edit {number}: find text {reason}.")]
        text = text.replace(edit["find"], edit["replace"], 1)

    if text == original_text:
        return None, [finding("error", "no_change", "The edits leave the module unchanged.")]
    try:
        new_tree = ast.parse(text)
    except SyntaxError as exc:
        return None, [finding("error", "syntax_error", f"Patched module does not parse: {exc}")]

    unsafe = _unsafe_constructs(new_tree) - _unsafe_constructs(ast.parse(original_text))
    if unsafe:
        return None, [finding("error", "unsafe_construct",
                              f"Patch introduces {', '.join(sorted(unsafe))}; it was not executed.")]

    findings = []
    old_sig = _signature(ast.parse(original_text), incident.function)
    new_sig = _signature(new_tree, incident.function)
    if new_sig != old_sig:
        findings.append(finding("error", "interface_changed",
                                f"Signature of {incident.function} changed from {old_sig} to {new_sig}."))
    changed = sum(1 for line in diff_lines(original_text, text, target)
                  if line[:1] in "+-" and line[:3] not in ("+++", "---"))
    if changed > MAX_CHANGED_LINES:
        findings.append(finding("warning", "large_patch",
                                f"{changed} changed lines; expected a minimal fix (≤ {MAX_CHANGED_LINES})."))
    return text, findings


_BLOCKED_CALLS = {"exec", "eval", "compile", "open", "__import__", "globals", "setattr", "delattr"}


def _unsafe_constructs(tree):
    """Imports and dynamic-execution builtins. The fixed check imports the candidate, so a patch is code we run."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            found.add("an import")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _BLOCKED_CALLS:
            found.add(f"a call to {node.func.id}()")
    return found


def _signature(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            args = node.args
            return {"args": [a.arg for a in args.args], "defaults": len(args.defaults),
                    "vararg": bool(args.vararg), "kwarg": bool(args.kwarg)}
    return None


def diff_lines(old, new, name):
    return list(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                     fromfile=f"a/{name}", tofile=f"b/{name}"))
