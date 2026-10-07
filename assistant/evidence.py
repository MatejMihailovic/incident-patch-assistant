"""Load error events, report malformed ones, and group events into incidents."""

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import config

REQUIRED_FIELDS = {"event_id": str, "function": str, "input": dict, "error": str, "source": str}
SOURCE_PATTERN = re.compile(r"^(?P<file>[\w./-]+):(?P<line>\d+)$")


@dataclass
class Incident:
    """Events sharing one failure signature: same function, error type and source location.

    :param id: Stable incident ID, ``INC-`` plus the first event ID in the group.
    :type id: str
    :param function: Name of the function that failed.
    :type function: str
    :param error: Exception type recorded by the events.
    :type error: str
    :param source: Reported location in ``file:line`` form.
    :type source: str
    :param module: File part of ``source``; the only module a patch may change.
    :type module: str
    :param line: Line part of ``source``.
    :type line: int
    :param event_ids: IDs of every event with this signature, in load order.
    :type event_ids: list[str]
    """

    id: str
    function: str
    error: str
    source: str
    module: str
    line: int
    event_ids: list = field(default_factory=list)

    def to_dict(self):
        """Serialise the incident for JSON artifacts.

        :returns: All fields as a plain dictionary.
        :rtype: dict
        """
        return asdict(self)


def _relative(path):
    """Express a path relative to the repository root when possible.

    :param path: Any filesystem path.
    :type path: pathlib.Path or str
    :returns: Root-relative path, or the path unchanged if it lies outside the root.
    :rtype: str
    """
    try:
        return str(Path(path).relative_to(config.ROOT))
    except ValueError:
        return str(path)


def load_events(paths=None):
    """Load events from one or more JSON files. A bad file or event is reported, never fatal.

    :param paths: Event files to read in order; defaults to :data:`assistant.config.EVENT_FILES`.
    :type paths: list[pathlib.Path] or None
    :returns: A pair ``(events, problems)``. ``events`` are the valid events, each with an added
        ``_origin`` key naming its file. ``problems`` describes every missing file, unparsable file,
        malformed event or duplicate ID.
    :rtype: tuple[list[dict], list[dict]]
    """
    events, problems, seen = [], [], set()
    for raw_path in paths or config.EVENT_FILES:
        path = Path(raw_path)
        origin = _relative(path)
        if not path.exists():
            problems.append({"file": origin, "event_id": None, "problem": "event file not found"})
            continue
        try:
            items = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            problems.append({"file": origin, "event_id": None, "problem": f"invalid JSON: {exc}"})
            continue
        if not isinstance(items, list):
            problems.append({"file": origin, "event_id": None, "problem": "expected a JSON list of events"})
            continue
        for index, item in enumerate(items):
            event_id = item.get("event_id") if isinstance(item, dict) else None
            issues = _event_issues(item)
            if event_id in seen:
                issues.append("duplicate event_id")
            if issues:
                problems.append({"file": origin, "index": index, "event_id": event_id, "problem": "; ".join(issues)})
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
    :rtype: list[Incident]
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
    :type incidents: list[Incident]
    :param event_id: Event selected by the developer, e.g. ``"EV1"``.
    :type event_id: str
    :returns: The matching incident, or ``None`` if no valid event has that ID.
    :rtype: Incident or None
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
