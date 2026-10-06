"""Verify that the frozen baseline, check script and answer key are byte-identical to the starter pack."""
import hashlib
import json

from . import config


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_frozen():
    manifest = json.loads(config.FROZEN_MANIFEST.read_text())["files"]
    mismatches = []
    for rel, expected in manifest.items():
        path = config.ROOT / rel
        actual = sha256(path) if path.exists() else None
        if actual != expected:
            mismatches.append({"path": rel, "expected": expected, "actual": actual})
    return {"ok": not mismatches, "checked": sorted(manifest), "mismatches": mismatches}
