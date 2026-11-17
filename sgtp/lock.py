"""Release source lock: every shipped file with its SHA-256 (see release_source_lock.json)."""

import hashlib
import json
from pathlib import Path

RELEASE_ROOT = Path(__file__).resolve().parent.parent
LOCK_FILE = "release_source_lock.json"
LOCK_VERSION = "sgtp_release_source_lock_v1"
# Directories that only ever hold run outputs or caches, never shipped sources.
IGNORED_PARTS = ("__pycache__", ".git", "runs")


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def shipped_files(root=RELEASE_ROOT):
    root = Path(root)
    result = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in IGNORED_PARTS for part in relative.parts) or path.suffix == ".pyc":
            continue
        name = str(relative).replace("\\", "/")
        if name != LOCK_FILE:
            result.append(name)
    return result


def verify(root=RELEASE_ROOT):
    """Compare the tree with the lock; report changed, missing and unlisted files."""
    root = Path(root)
    lock = json.loads((root / LOCK_FILE).read_text(encoding="utf-8"))
    if lock.get("lock_version") != LOCK_VERSION:
        raise ValueError("Unknown release lock version")
    expected = lock["files"]
    changed = [name for name, digest in sorted(expected.items())
               if (root / name).is_file() and sha256_file(root / name) != digest]
    missing = [name for name in sorted(expected) if not (root / name).is_file()]
    unlisted = [name for name in shipped_files(root) if name not in expected]
    return {"lock_version": LOCK_VERSION, "file_count": len(expected),
            "changed": changed, "missing": missing, "unlisted": unlisted,
            "passed": not (changed or missing or unlisted)}
