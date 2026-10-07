"""Load error events, report malformed ones, and group events into incidents."""

import json
import re
from pathlib import Path

from . import config
from .models import EventProblem, Incident
from .utils import relative_to_root

REQUIRED_FIELDS = {"event_id": str, "function": str, "input": dict, "error": str, "source": str}
SOURCE_PATTERN = re.compile(r"^(?P<file>[\w./-]+):(?P<line>\d+)$")


def load_events(paths=None):
    """Load events from one or more JSON files. A bad file or event is reported, never fatal.

    :param paths: Event files to read in order; defaults to :data:`assistant.config.EVENT_FILES`.
    :type paths: list[pathlib.Path] or None
    :returns: A pair ``(events, problems)``. ``events`` are the valid events, each with an added
        ``_origin`` key naming its file. ``problems`` describes every missing file, unparsable file,
        malformed event or duplicate ID.
    :rtype: tuple[list[dict], list[assistant.models.EventProblem]]
    """
    events, problems, seen = [], [], set()
    for raw_path in paths or config.EVENT_FILES:
        path = Path(raw_path)
        origin = relative_to_root(path)
        if not path.exists():
            problems.append(EventProblem(origin, "event file not found"))
            continue
        try:
            items = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            problems.append(EventProblem(origin, f"invalid JSON: {exc}"))
            continue
        if not isinstance(items, list):
            problems.append(EventProblem(origin, "expected a JSON list of events"))
            continue
        for index, item in enumerate(items):
            event_id = item.get("event_id") if isinstance(item, dict) else None
            issues = _event_issues(item)
            # IDs must be unique across all files, because the model cites events by ID.
            if event_id in seen:
                issues.append("duplicate event_id")
            if issues:
                problems.append(EventProblem(origin, "; ".join(issues), event_id=event_id, index=index))
                continue
            seen.add(event_id)
            events.append(dict(item, _origin=origin))
    return events, problems


def _event_issues(item):
    """List what is wrong with one raw event.

    :param item: One element of an event file.
    :type item: object
    :returns: Human-readable problems; empty when the event is valid.
    :rtype: list[str]
    """
    if not isinstance(item, dict):
        return ["event is not an object"]
    issues = [
        f"missing or invalid '{name}'" for name, kind in REQUIRED_FIELDS.items() if not isinstance(item.get(name), kind)
    ]
    if isinstance(item.get("source"), str) and not SOURCE_PATTERN.match(item["source"]):
        issues.append("source is not in file:line form")
    return issues


def group_incidents(events):
    """Group valid events by ``(function, error, source)``.

    Grouping is done in code, independently of the model, so the pipeline can later
    check whether the model cited evidence of a different failure.

    :param events: Valid events as returned by :func:`load_events`.
    :type events: list[dict]
    :returns: One incident per distinct signature, in order of first appearance.
    :rtype: list[assistant.models.Incident]
    """
    incidents = {}
    for event in events:
        key = (event["function"], event["error"], event["source"])
        if key not in incidents:
            match = SOURCE_PATTERN.match(event["source"])
            incidents[key] = Incident(
                id=f"INC-{event['event_id']}",
                function=event["function"],
                error=event["error"],
                source=event["source"],
                module=match["file"],
                line=int(match["line"]),
            )
        incidents[key].event_ids.append(event["event_id"])
    return list(incidents.values())


def find_incident(incidents, event_id):
    """Find the incident that contains an event.

    :param incidents: Incidents from :func:`group_incidents`.
    :type incidents: list[assistant.models.Incident]
    :param event_id: Event selected by the developer, e.g. ``"EV1"``.
    :type event_id: str
    :returns: The matching incident, or ``None`` if no valid event has that ID.
    :rtype: assistant.models.Incident or None
    """
    return next((inc for inc in incidents if event_id in inc.event_ids), None)


def numbered(text):
    """Prefix each line with its 1-based line number, as shown to the model.

    :param text: Source file contents.
    :type text: str
    :returns: The text with ``"  4| "``-style prefixes.
    :rtype: str
    """
    return "\n".join(f"{n:>3}| {line}" for n, line in enumerate(text.splitlines(), start=1))
