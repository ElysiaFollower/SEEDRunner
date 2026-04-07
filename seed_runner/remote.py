"""Remote SSH helpers shared by mount and session managers."""

import os
import subprocess
from typing import List

from seed_runner.config import get_machine_config


def get_ssh_args(machine_id: str, timeout: int = 30) -> List[str]:
    """Build SSH arguments for the configured machine."""
    machine_config = get_machine_config(machine_id)
    key_path = os.path.expanduser(machine_config.key_path)
    return [
        "ssh",
        "-i",
        key_path,
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        f"ConnectTimeout={timeout}",
        "-p",
        str(machine_config.port),
        f"{machine_config.user}@{machine_config.host}",
    ]


def run_ssh_command(
    machine_id: str,
    cmd: str,
    timeout: int = 30,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Run a command on the remote machine via SSH."""
    result = subprocess.run(
        get_ssh_args(machine_id, timeout=timeout) + [cmd],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"SSH command failed with exit code {result.returncode}\n"
            f"Command: {cmd}\n"
            f"Stderr: {result.stderr}"
        )
    return result


def execute_ssh_command(machine_id: str, cmd: str, timeout: int = 30) -> str:
    """Execute a remote command and return combined stdout/stderr."""
    result = run_ssh_command(machine_id, cmd, timeout=timeout, check=True)
    return result.stdout + result.stderr
