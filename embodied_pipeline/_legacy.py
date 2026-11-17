"""Load existing engines without copying, moving or changing their files."""

from importlib import import_module
from pathlib import Path
import sys
from threading import RLock

from ._release import ENGINE_ROOT, RELEASE_ROOT

# Release-only adapter: engines are vendored under vendor/legacy_engines/ and only the
# three planning engines plus the release backend switch are shipped.
ROOT = RELEASE_ROOT
ENGINE_DIRS = (
    "object_alignment_v1", "skill_matching_v1", "llm_audit_v1", "release_adapters",
)
_LOCK = RLock()


def bootstrap():
    """Make the legacy flat imports available; do not import engines eagerly."""
    with _LOCK:
        for directory in reversed(ENGINE_DIRS):
            path = str(ENGINE_ROOT / directory)
            if path not in sys.path:
                sys.path.insert(0, path)


def load(name):
    """Reject module-name collisions instead of silently using unrelated code."""
    with _LOCK:
        bootstrap()
        expected = [ENGINE_ROOT / directory / (name + ".py")
                    for directory in ENGINE_DIRS]
        expected = [path.resolve() for path in expected if path.is_file()]
        if len(expected) != 1:
            raise ImportError(f"Expected one legacy engine module for {name!r}")
        cached = sys.modules.get(name)
        if cached is not None and Path(getattr(cached, "__file__", "")).resolve() != expected[0]:
            raise ImportError(f"Legacy module name collision: {name}")
        module = import_module(name)
        if Path(module.__file__).resolve() != expected[0]:
            raise ImportError(f"Legacy module loaded from an unexpected path: {name}")
        return module


def resolve(exports, name):
    if name not in exports:
        raise AttributeError(name)
    module, attribute = exports[name]
    return getattr(load(module), attribute)

