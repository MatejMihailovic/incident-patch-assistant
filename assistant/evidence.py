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
    """Events sharing one failure signature: same function, error type and source location."""
    id: str
    function: str
    error: str
    source: str
    module: str
    line: int
    event_ids: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def _relative(path):
    try:
        return str(Path(path).relative_to(config.ROOT))
    except ValueError:
        return str(path)


def load_events(paths=None):
    """Return (valid_events, problems). A bad file or event is reported, never fatal."""
    events, problems, seen = [], [], set()
    for path in paths or config.EVENT_FILES:
        path = Path(path)
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
    if not isinstance(item, dict):
        return ["event is not an object"]
    issues = [f"missing or invalid '{name}'" for name, kind in REQUIRED_FIELDS.items()
              if not isinstance(item.get(name), kind)]
    if isinstance(item.get("source"), str) and not SOURCE_PATTERN.match(item["source"]):
        issues.append("source is not in file:line form")
    return issues


def group_incidents(events):
    incidents = {}
    for event in events:
        key = (event["function"], event["error"], event["source"])
        if key not in incidents:
            match = SOURCE_PATTERN.match(event["source"])
            incidents[key] = Incident(
                id=f"INC-{event['event_id']}", function=event["function"], error=event["error"],
                source=event["source"], module=match["file"], line=int(match["line"]))
        incidents[key].event_ids.append(event["event_id"])
    return list(incidents.values())


def find_incident(incidents, event_id):
    return next((inc for inc in incidents if event_id in inc.event_ids), None)


def numbered(text):
    return "\n".join(f"{n:>3}| {line}" for n, line in enumerate(text.splitlines(), start=1))
