"""Persistent state helpers for mount and session metadata."""

from contextlib import contextmanager
import fcntl
import json
import os
from typing import Any, Dict, Iterator

from seed_runner.utils import ensure_dir, read_file, write_file


def _empty_state() -> Dict[str, Any]:
    """Return the default persisted state structure."""
    return {
        "mounts": {},
        "sessions": {},
    }


def get_state_dir() -> str:
    """Return the directory used to persist CLI state."""
    configured = os.getenv("SEED_RUNNER_STATE_DIR")
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    return os.path.expanduser("~/.seed-runner")


def get_state_file() -> str:
    """Return the JSON file used to persist CLI state."""
    return os.path.join(get_state_dir(), "state.json")


def get_state_lock_file() -> str:
    """Return the lock file guarding state.json mutations."""
    return os.path.join(get_state_dir(), "state.lock")


@contextmanager
def state_lock() -> Iterator[None]:
    """Acquire an exclusive cross-process lock for short state mutations."""
    lock_file = get_state_lock_file()
    ensure_dir(os.path.dirname(lock_file))
    fd = os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def load_state() -> Dict[str, Any]:
    """Load persisted state, returning an empty structure when absent."""
    state_file = get_state_file()
    if not os.path.exists(state_file):
        return _empty_state()
    state = json.loads(read_file(state_file))
    state.setdefault("mounts", {})
    state.setdefault("sessions", {})
    return state


def save_state(state: Dict[str, Any]) -> None:
    """Persist the full state atomically."""
    state_file = get_state_file()
    ensure_dir(os.path.dirname(state_file))
    temp_file = f"{state_file}.tmp"
    state.setdefault("mounts", {})
    state.setdefault("sessions", {})
    write_file(temp_file, json.dumps(state, indent=2, sort_keys=True))
    os.replace(temp_file, state_file)


def load_mount_metadata(local_path: str) -> Dict[str, Any]:
    """Load metadata.json for a mount if present."""
    metadata_path = os.path.join(local_path, "metadata.json")
    if not os.path.exists(metadata_path):
        return {}
    return json.loads(read_file(metadata_path))


def save_mount_metadata(local_path: str, metadata: Dict[str, Any]) -> None:
    """Persist metadata.json for a mount."""
    metadata_path = os.path.join(local_path, "metadata.json")
    write_file(metadata_path, json.dumps(metadata, indent=2, sort_keys=True))
