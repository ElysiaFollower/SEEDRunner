"""Opt-in integration test for the real VM-backed seed-runner workflow."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from seed_runner.config import ConfigManager


def _run_cli(env, *args):
    """Run the seed-runner CLI as a separate process and parse the JSON response."""
    result = subprocess.run(
        [sys.executable, "-m", "seed_runner.cli", *args],
        capture_output=True,
        text=True,
        env=env,
    )

    stream = result.stdout if result.returncode == 0 else result.stderr
    try:
        payload = json.loads(stream)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"Expected JSON output from seed-runner, got:\n{stream}"
        ) from exc

    if result.returncode != 0:
        raise AssertionError(
            f"seed-runner {' '.join(args)} failed:\n{json.dumps(payload, indent=2)}"
        )

    return payload


@pytest.mark.integration
def test_real_vm_agent_workflow(tmp_path):
    """Exercise the CLI the same way an agent would: mount, session, exec, read files, cleanup."""
    if os.getenv("SEED_RUNNER_RUN_REAL_VM_TESTS") != "1":
        pytest.skip("Set SEED_RUNNER_RUN_REAL_VM_TESTS=1 to run real VM integration tests")

    env_file = Path(".env.machines")
    if not env_file.exists():
        pytest.skip(".env.machines is required for real VM integration tests")

    machine_id = os.getenv("SEED_RUNNER_TEST_MACHINE", "vm-seed-01")
    config_manager = ConfigManager(str(env_file))
    if not config_manager.has_machine(machine_id):
        pytest.skip(f"Machine '{machine_id}' is not configured in .env.machines")

    if "SEED_RUNNER_LOCAL_HOST" not in os.environ:
        pytest.skip("SEED_RUNNER_LOCAL_HOST must be configured for real VM integration tests")

    state_dir = tmp_path / "state"
    local_dir = tmp_path / "artifacts"
    session_name = "exp-agent-e2e"
    labsetup_dir = tmp_path / "Labsetup"
    labsetup_dir.mkdir()
    (labsetup_dir / "input.txt").write_text("agent-e2e-ok\n")
    command = "pwd && cat Labsetup/input.txt > artifacts/e2e.txt && cat artifacts/e2e.txt"

    env = os.environ.copy()
    env["SEED_RUNNER_STATE_DIR"] = str(state_dir)

    mount_id = None
    session_id = None

    try:
        mount = _run_cli(
            env,
            "mount",
            "create",
            "--machine",
            machine_id,
            "--local-dir",
            str(local_dir),
        )
        mount_id = mount["mount_id"]
        assert mount["status"] == "mounted"

        session = _run_cli(
            env,
            "session",
            "create",
            "--machine",
            machine_id,
            "--mount-id",
            mount_id,
            "--name",
            session_name,
        )
        session_id = session["session_id"]
        assert session["status"] == "ready"

        exec_result = _run_cli(
            env,
            "session",
            "exec",
            "--session",
            session_id,
            "--cmd",
            command,
        )
        assert exec_result["exit_code"] == 0
        assert Path(exec_result["log_file_local"]).exists()

        session_status = _run_cli(
            env,
            "session",
            "status",
            "--session",
            session_id,
        )
        assert session_status["status"] == "active"
        assert session_status["command_count"] == 1

        synced_file = local_dir / "artifacts" / "e2e.txt"
        assert synced_file.read_text() == "agent-e2e-ok\n"

        metadata = json.loads((local_dir / "metadata.json").read_text())
        assert metadata["sessions"][0]["session_name"] == session_name
        assert metadata["sessions"][0]["commands"][0]["cmd"] == command
        assert metadata["sessions"][0]["commands"][0]["exit_code"] == 0
    finally:
        if session_id is not None:
            _run_cli(env, "session", "destroy", "--session", session_id)
        if mount_id is not None:
            _run_cli(env, "mount", "destroy", "--mount-id", mount_id)
