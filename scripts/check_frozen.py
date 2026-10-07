"""Fail if any frozen starter-pack file differs from its hash in ``fixtures/frozen.json``.

Stdlib only (``assistant.integrity`` has no third-party imports), so it runs as a pre-commit hook without the app's
dependencies.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant.integrity import verify_frozen


def main():
    """Verify the frozen files and report each mismatch.

    :returns: Process exit code: ``0`` if all hashes match, ``1`` otherwise.
    :rtype: int
    """
    result = verify_frozen()
    for mismatch in result["mismatches"]:
        print(f"FROZEN FILE CHANGED: {mismatch['path']} (expected {mismatch['expected'][:12]}…)")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
