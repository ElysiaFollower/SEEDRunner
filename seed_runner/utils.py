"""Utility functions for seed-runner."""

import json
import os
import shlex
import subprocess
from datetime import datetime
from typing import Any, Dict, Optional, Sequence, Union

Command = Union[str, Sequence[str]]


def _format_command(cmd: Command) -> str:
    """Format a command for logs and error messages."""
    if isinstance(cmd, str):
        return cmd
    return " ".join(shlex.quote(part) for part in cmd)


def run_command(
    cmd: Command,
    check: bool = True,
    timeout: Optional[int] = None,
) -> subprocess.CompletedProcess:
    """
    Run a shell command and return the result.

    Args:
        cmd: Command to run
        check: If True, raise exception on non-zero exit code
        timeout: Optional timeout in seconds

    Returns:
        CompletedProcess object
    """
    command_text = _format_command(cmd)
    result = subprocess.run(
        cmd,
        shell=isinstance(cmd, str),
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command failed with exit code {result.returncode}\n"
            f"Command: {command_text}\n"
            f"Stderr: {result.stderr}"
        )

    return result


def ensure_dir(path: str) -> None:
    """Create directory if it doesn't exist."""
    os.makedirs(path, exist_ok=True)


def json_response(data: Dict[str, Any]) -> str:
    """Format data as JSON response."""
    return json.dumps(data, indent=2, default=str)


def get_timestamp() -> str:
    """Get current timestamp in ISO format."""
    return datetime.utcnow().isoformat() + "Z"


def parse_timestamp(timestamp: str) -> datetime:
    """Parse an ISO timestamp produced by get_timestamp."""
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))


def generate_id(prefix: str) -> str:
    """Generate a unique ID with given prefix."""
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
    return f"{prefix}_{timestamp}"


def read_file(path: str) -> str:
    """Read file contents."""
    with open(path, 'r') as f:
        return f.read()


def write_file(path: str, content: str) -> None:
    """Write content to file."""
    ensure_dir(os.path.dirname(path))
    with open(path, 'w') as f:
        f.write(content)


def append_file(path: str, content: str) -> None:
    """Append content to file."""
    ensure_dir(os.path.dirname(path))
    with open(path, 'a') as f:
        f.write(content)


def escape_shell_arg(arg: str) -> str:
    """
    Escape a string for safe use in shell commands.

    Uses single quotes and handles embedded single quotes.
    """
    if not arg:
        return "''"
    # Replace single quotes with '\'' (end quote, escaped quote, start quote)
    escaped = arg.replace("'", "'\\''")
    return f"'{escaped}'"
