"""Command line: list incidents, run the assistant, replay a saved run, or run the demonstration set."""
import argparse
import json
import sys
from pathlib import Path

from . import config, demo, evidence, pipeline
from .report import write_report


def cmd_incidents(_args):
    """Print incidents grouped from the recorded events, then any malformed events.

    :param _args: Parsed arguments (unused).
    :type _args: argparse.Namespace
    :returns: Process exit code, always ``0``.
    :rtype: int
    """
    events, malformed = evidence.load_events()
    for inc in evidence.group_incidents(events):
        print(f"{inc.id}: {inc.function} raised {inc.error} at {inc.source}  events={','.join(inc.event_ids)}")
    for m in malformed:
        print(f"MALFORMED {m.get('event_id')} in {m['file']}: {m['problem']}")
    return 0


def _summary(run_dir):
    """Print a run's status, reasons and report path.

    :param run_dir: Run directory.
    :type run_dir: pathlib.Path or str
    :returns: Contents of the run's ``run.json``.
    :rtype: dict
    """
    meta = json.loads((Path(run_dir) / "run.json").read_text())
    source = (meta.get("provenance") or {}).get("source", "none")
    print(f"{meta['status'].upper():8} {meta['run_id']}  (model response: {source})")
    for reason in meta["status_reasons"]:
        print(f"         - {reason}")
    print(f"         report: {Path(run_dir) / 'report.html'}")
    return meta


def cmd_run(args):
    """Run the pipeline for one incident.

    :param args: Parsed arguments with ``event``, ``response_file``, ``model`` and ``effort``.
    :type args: argparse.Namespace
    :returns: ``0`` if the run ends ``checked``, otherwise ``1``.
    :rtype: int
    """
    run_dir = pipeline.execute(args.event, response_file=args.response_file, model_name=args.model, effort=args.effort)
    meta = _summary(run_dir)
    return 0 if meta["status"] == "checked" else 1


def cmd_replay(args):
    """Regenerate a saved run's report without calling the model, optionally rerunning the fixed check.

    :param args: Parsed arguments with ``run_dir`` and ``recheck``.
    :type args: argparse.Namespace
    :returns: ``0`` on success, ``2`` if ``run_dir`` is not a run directory.
    :rtype: int
    """
    run_dir = Path(args.run_dir)
    if not (run_dir / "run.json").exists():
        print(f"not a run directory: {run_dir}", file=sys.stderr)
        return 2
    if args.recheck:
        comparison = pipeline.recheck(run_dir)
        for label in ("baseline", "candidate"):
            c = comparison[label]
            state = "not available" if not c["available"] else "reproduced" if c["reproduced"] else "DIFFERENT"
            print(f"recheck {label:9}: {state}")
    write_report(run_dir)
    _summary(run_dir)
    return 0


def cmd_demo(args):
    """Run the minimum demonstration set, grade the brief's five checks, and write ``RESULTS.md``.

    Runs one real proposal (a new live call with ``--live``, otherwise a replay of the latest saved
    live response) plus every simulated negative control in ``fixtures/simulated/``.

    :param args: Parsed arguments with ``event``, ``live`` and ``out``.
    :type args: argparse.Namespace
    :returns: ``0`` if all five checks pass, ``1`` if any fails, ``2`` if there is no saved live run to replay.
    :rtype: int
    """
    try:
        scenarios = demo.run_scenarios(args.event, live=args.live)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 2
    for run_dir in [scenarios["real"], *scenarios["simulated"]]:
        _summary(run_dir)
    rows = demo.grade(scenarios)
    path = demo.write_results(rows, scenarios, args.out)
    print()
    for n, row in enumerate(rows, start=1):
        print(f"check {n}: {'PASS' if row['passed'] else 'FAIL'}  {row['check']}")
    print(f"\n{sum(r['passed'] for r in rows)}/{len(rows)} checks passed; table written to {path}")
    return 0 if all(r["passed"] for r in rows) else 1


def main(argv=None):
    """Parse arguments and dispatch to a subcommand.

    :param argv: Arguments without the program name; defaults to ``sys.argv[1:]``.
    :type argv: list[str] or None
    :returns: Process exit code from the subcommand.
    :rtype: int
    """
    parser = argparse.ArgumentParser(prog="python -m assistant", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("incidents", help="list incidents grouped from the recorded events").set_defaults(func=cmd_incidents)

    run = sub.add_parser("run", help="diagnose an incident, propose a patch, check it, write a report")
    run.add_argument("--event", default="EV1", help="event ID that selects the incident (default: EV1)")
    run.add_argument("--response-file", type=Path,
                     help="use a saved or simulated model response instead of calling the API")
    run.add_argument("--model", help=f"model ID (default: {config.MODEL})")
    run.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], help=f"effort (default: {config.EFFORT})")
    run.set_defaults(func=cmd_run)

    replay = sub.add_parser("replay", help="regenerate a saved run's report without calling the model")
    replay.add_argument("run_dir", type=Path)
    replay.add_argument("--recheck", action="store_true", help="rerun the fixed check on the saved candidate and compare")
    replay.set_defaults(func=cmd_replay)

    demo_cmd = sub.add_parser("demo", help="run the minimum demonstration set and write RESULTS.md")
    demo_cmd.add_argument("--event", default="EV1")
    demo_cmd.add_argument("--live", action="store_true", help="make a new live call instead of replaying the saved one")
    demo_cmd.add_argument("--out", type=Path, default=config.ROOT / "RESULTS.md", help="results table path")
    demo_cmd.set_defaults(func=cmd_demo)

    args = parser.parse_args(argv)
    return args.func(args)
