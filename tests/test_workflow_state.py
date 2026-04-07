"""Stateful workflow tests for mount and session management."""

import json
import os

import seed_runner.config as config_module
from seed_runner.config import MachineConfig
from seed_runner.mount import MountManager
from seed_runner.session import SessionManager


class FakeCompletedProcess:
    """Small helper mirroring subprocess.CompletedProcess fields used in tests."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _fake_machine_config(key_path: str) -> MachineConfig:
    return MachineConfig(
        machine_id="vm-seed-01",
        host="localhost",
        port=2222,
        user="seed",
        key_path=key_path,
    )


def test_mount_state_persists_across_manager_instances(temp_dir, monkeypatch):
    """Mount records should survive across separate CLI-process-like manager instances."""
    key_path = os.path.join(temp_dir, "id_test")
    with open(key_path, "w") as f:
        f.write("test-key")

    monkeypatch.setenv("SEED_RUNNER_STATE_DIR", os.path.join(temp_dir, "state"))
    monkeypatch.setenv("SEED_RUNNER_LOCAL_USER", "ely")
    monkeypatch.setenv("SEED_RUNNER_LOCAL_HOST", "10.0.0.5")
    monkeypatch.setattr(
        "seed_runner.config.get_machine_config",
        lambda machine_id: _fake_machine_config(key_path),
    )
    monkeypatch.setattr("seed_runner.mount.execute_ssh_command", lambda *args, **kwargs: "")
    monkeypatch.setattr(
        "seed_runner.mount.run_ssh_command",
        lambda *args, **kwargs: FakeCompletedProcess(returncode=0),
    )

    mount_manager = MountManager()
    local_dir = os.path.join(temp_dir, "artifacts")
    created = mount_manager.create("vm-seed-01", local_dir)

    reloaded_manager = MountManager()
    status = reloaded_manager.status(created["mount_id"])

    assert status["mount_id"] == created["mount_id"]
    assert status["status"] == "mounted"
    assert status["session_count"] == 0

    metadata_path = os.path.join(local_dir, "metadata.json")
    with open(metadata_path, "r") as f:
        metadata = json.load(f)
    assert metadata["mount_id"] == created["mount_id"]
    assert metadata["sessions"] == []


def test_mount_manager_reads_runtime_settings_from_env_file(env_file, temp_dir, monkeypatch):
    """MountManager should pick up SEED_RUNNER_* values loaded during create()."""
    monkeypatch.chdir(temp_dir)
    monkeypatch.setenv("SEED_RUNNER_STATE_DIR", os.path.join(temp_dir, "state"))
    monkeypatch.delenv("SEED_RUNNER_LOCAL_HOST", raising=False)
    monkeypatch.delenv("SEED_RUNNER_LOCAL_SSH_PORT", raising=False)
    monkeypatch.delenv("SEED_RUNNER_LOCAL_USER", raising=False)
    monkeypatch.delenv("SEED_RUNNER_REMOTE_TO_LOCAL_KEY", raising=False)
    monkeypatch.setattr(config_module, "_config_manager", None)
    monkeypatch.setattr("seed_runner.mount.execute_ssh_command", lambda *args, **kwargs: "")
    monkeypatch.setattr(
        "seed_runner.mount.run_ssh_command",
        lambda *args, **kwargs: FakeCompletedProcess(returncode=0),
    )

    mount_manager = MountManager()
    local_dir = os.path.join(temp_dir, "artifacts")
    created = mount_manager.create("vm-seed-01", local_dir)

    with open(os.path.join(temp_dir, "state", "state.json"), "r") as f:
        state = json.load(f)

    mount_info = state["mounts"][created["mount_id"]]
    assert mount_info["local_host"] == "10.0.0.5"
    assert mount_info["local_ssh_port"] == 2200
    assert mount_info["local_user"] == "ely"
    assert mount_info["remote_to_local_key"] == "~/.ssh/id_ed25519"


def test_session_lifecycle_updates_state_and_metadata(temp_dir, monkeypatch):
    """Session create/exec/destroy should survive across manager instances and update metadata."""
    key_path = os.path.join(temp_dir, "id_test")
    with open(key_path, "w") as f:
        f.write("test-key")

    monkeypatch.setenv("SEED_RUNNER_STATE_DIR", os.path.join(temp_dir, "state"))
    monkeypatch.setenv("SEED_RUNNER_LOCAL_USER", "ely")
    monkeypatch.setenv("SEED_RUNNER_LOCAL_HOST", "10.0.0.5")
    monkeypatch.setattr(
        "seed_runner.config.get_machine_config",
        lambda machine_id: _fake_machine_config(key_path),
    )
    monkeypatch.setattr("seed_runner.mount.execute_ssh_command", lambda *args, **kwargs: "")
    monkeypatch.setattr(
        "seed_runner.mount.run_ssh_command",
        lambda *args, **kwargs: FakeCompletedProcess(returncode=0),
    )

    mount_manager = MountManager()
    local_dir = os.path.join(temp_dir, "artifacts")
    mount = mount_manager.create("vm-seed-01", local_dir)

    def fake_session_execute(machine_id, cmd, timeout=30):
        if "tmux new-window" in cmd:
            log_file = os.path.join(local_dir, "logs", "exp-web-01", "cmd_001.log")
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            with open(log_file, "w") as f:
                f.write("[2026-04-07T10:30:15Z] $ echo hello\n")
                f.write("hello\n")
                f.write("[2026-04-07T10:30:16Z] $ exit_code: 0\n")
        return ""

    monkeypatch.setattr("seed_runner.session.execute_ssh_command", fake_session_execute)
    monkeypatch.setattr(
        "seed_runner.session.run_ssh_command",
        lambda *args, **kwargs: FakeCompletedProcess(returncode=0),
    )

    session_manager = SessionManager()
    created_session = session_manager.create("vm-seed-01", mount["mount_id"], "exp-web-01")

    status_manager = SessionManager()
    status = status_manager.status(created_session["session_id"])
    assert status["status"] == "active"
    assert status["local_mount_point"] == local_dir

    exec_result = SessionManager().exec(created_session["session_id"], "echo hello")
    assert exec_result["exit_code"] == 0
    assert exec_result["log_filename"] == "cmd_001.log"

    destroy_result = SessionManager().destroy(created_session["session_id"])
    assert destroy_result["status"] == "destroyed"
    assert destroy_result["logs_location"] == os.path.join(local_dir, "logs", "exp-web-01")

    final_status = SessionManager().status(created_session["session_id"])
    assert final_status["status"] == "destroyed"
    assert final_status["command_count"] == 1

    with open(os.path.join(local_dir, "metadata.json"), "r") as f:
        metadata = json.load(f)

    assert len(metadata["sessions"]) == 1
    assert metadata["sessions"][0]["session_name"] == "exp-web-01"
    assert metadata["sessions"][0]["commands"][0]["cmd"] == "echo hello"
    assert metadata["sessions"][0]["commands"][0]["exit_code"] == 0
