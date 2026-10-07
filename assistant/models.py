"""Data models shared across modules.

Each model serialises with :func:`assistant.utils.to_jsonable` to the same keys the saved JSON artifacts
use, so runs recorded earlier still load.
"""

from dataclasses import dataclass, field


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


@dataclass
class EventProblem:
    """A missing or unreadable event file, or one rejected event.

    :param file: Event file, relative to the repository root.
    :type file: str
    :param problem: Human-readable description.
    :type problem: str
    :param event_id: ID of the rejected event, if it had one.
    :type event_id: str or None
    :param index: Position of the rejected event in its file; ``None`` for file-level problems.
    :type index: int or None
    """

    file: str
    problem: str
    event_id: str | None = None
    index: int | None = None


@dataclass
class Finding:
    """One result of validating the model's proposal in code.

    :param level: ``"error"`` (the run fails) or ``"warning"`` (shown to the reviewer only).
    :type level: str
    :param code: Stable identifier, e.g. ``"unrelated_event_cited"``.
    :type code: str
    :param message: Human-readable explanation.
    :type message: str
    """

    level: str
    code: str
    message: str

    @classmethod
    def error(cls, code, message):
        """Build a finding that makes the run fail.

        :param code: Stable identifier.
        :type code: str
        :param message: Human-readable explanation.
        :type message: str
        :returns: The finding.
        :rtype: Finding
        """
        return cls("error", code, message)

    @classmethod
    def warning(cls, code, message):
        """Build a finding that is shown to the reviewer but does not fail the run.

        :param code: Stable identifier.
        :type code: str
        :param message: Human-readable explanation.
        :type message: str
        :returns: The finding.
        :rtype: Finding
        """
        return cls("warning", code, message)

    @property
    def is_error(self):
        """Whether this finding makes the run fail.

        :returns: ``True`` for level ``"error"``.
        :rtype: bool
        """
        return self.level == "error"


@dataclass
class CheckResult:
    """One invocation of the fixed check command.

    :param command: The command as typed from the task folder, so it can be rerun by hand.
    :type command: str
    :param case: Single case that was run, or ``None`` for the full set.
    :type case: str or None
    :param exit_code: Process exit code; ``None`` on timeout.
    :type exit_code: int or None
    :param timed_out: Whether the process was killed by the timeout.
    :type timed_out: bool
    :param duration_s: Wall-clock seconds.
    :type duration_s: float
    :param stdout: Everything check.py printed.
    :type stdout: str
    :param stderr: Everything check.py wrote to stderr.
    :type stderr: str
    :param results: check.py's per-case results, or ``None`` if it produced none.
    :type results: list[dict] or None
    :param load_error: check.py's error object if the module could not be imported.
    :type load_error: dict or None
    """

    command: str
    case: str | None
    exit_code: int | None
    timed_out: bool
    duration_s: float
    stdout: str
    stderr: str
    results: list | None = None
    load_error: dict | None = None

    @classmethod
    def from_dict(cls, data):
        """Rebuild a result from a saved ``checks/*.json`` artifact.

        :param data: Parsed JSON, or ``None`` if the artifact is absent.
        :type data: dict or None
        :returns: The result, or ``None``.
        :rtype: CheckResult or None
        """
        return cls(**data) if data else None

    @property
    def failing_ids(self):
        """Reference cases that did not pass.

        :returns: Case IDs; empty if none failed or no results were produced.
        :rtype: list[str]
        """
        return [r["id"] for r in self.results or [] if not r["passed"]]

    @property
    def pass_count(self):
        """Pass count for display.

        :returns: ``"passed/total"``, or an em dash when there are no results.
        :rtype: str
        """
        if not self.results:
            return "—"
        return f"{sum(r['passed'] for r in self.results)}/{len(self.results)}"


@dataclass
class DemoCheck:
    """One graded row of the minimum demonstration.

    :param check: What the brief requires.
    :type check: str
    :param expected: Expected behaviour, from the frozen answer key or hashes.
    :type expected: str
    :param observed: What the saved artifacts show.
    :type observed: str
    :param passed: Whether observed matches expected.
    :type passed: bool
    :param evidence: Artifact paths that support the row.
    :type evidence: list[str]
    """

    check: str
    expected: str
    observed: str
    passed: bool
    evidence: list = field(default_factory=list)
