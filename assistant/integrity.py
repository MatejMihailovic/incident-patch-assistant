"""Verify that the frozen baseline, check script and answer key are byte-identical to the starter pack."""

import hashlib
import json

from . import config


def sha256(path):
    """Hash a file's bytes.

    :param path: File to hash.
    :type path: pathlib.Path
    :returns: Hex-encoded SHA-256 digest.
    :rtype: str
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_frozen():
    """Compare every file listed in :data:`assistant.config.FROZEN_MANIFEST` with its recorded hash.

    :returns: ``{"ok": bool, "checked": [paths], "mismatches": [{"path", "expected", "actual"}]}``.
        ``actual`` is ``None`` for a missing file.
    :rtype: dict
    """
    manifest = json.loads(config.FROZEN_MANIFEST.read_text())["files"]
    mismatches = []
    for rel, expected in manifest.items():
        path = config.ROOT / rel
        actual = sha256(path) if path.exists() else None
        if actual != expected:
            mismatches.append({"path": rel, "expected": expected, "actual": actual})
    return {"ok": not mismatches, "checked": sorted(manifest), "mismatches": mismatches}
