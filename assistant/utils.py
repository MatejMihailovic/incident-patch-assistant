"""Small helpers reused across modules: time, JSON files and paths."""

import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path

from . import config


def utc_now():
    """Current time in UTC.

    :returns: Timezone-aware timestamp.
    :rtype: datetime.datetime
    """
    return datetime.now(timezone.utc)


def utc_iso():
    """Current UTC time as recorded in artifacts.

    :returns: ISO 8601 timestamp with second precision.
    :rtype: str
    """
    return utc_now().isoformat(timespec="seconds")


def to_jsonable(value):
    """Convert data models, including ones nested in lists and dicts, to plain JSON types.

    :param value: Any value built from dataclasses, dicts, lists and JSON scalars.
    :type value: object
    :returns: The same data with every dataclass replaced by a dict.
    :rtype: object
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    return value


def read_json(path):
    """Read a JSON file if it exists. Runs that stopped early have fewer artifacts.

    :param path: File to read.
    :type path: pathlib.Path
    :returns: Parsed JSON, or ``None`` if the file is absent.
    :rtype: object or None
    """
    return json.loads(path.read_text()) if path.exists() else None


def write_json(path, data):
    """Write indented JSON with a trailing newline, creating parent folders.

    :param path: Destination file.
    :type path: pathlib.Path
    :param data: JSON-serialisable content; data models are converted.
    :type data: object
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_jsonable(data), indent=2) + "\n")


def relative_to_root(path):
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
