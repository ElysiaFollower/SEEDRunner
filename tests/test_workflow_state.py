"""Stateful workflow tests for mount and session management."""

import json
import os
import subprocess
import sys
import threading
import time

import pytest
import seed_runner.config as config_module
from seed_runner.config import MachineConfig
from seed_runner.mount import MountManager
from seed_runner.session import SessionManager
from seed_runner.state import load_state, save_state


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


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(__file__))


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
    assert mount_info["local_workspace_root"] == temp_dir
    assert mount_info["remote_sync_dir"].endswith("/sync")


def test_mount_create_reuses_existing_remote_mount_for_same_source(temp_dir, monkeypatch):
    """Mount creation should recover when the remote path is already mounted from the same local dir."""
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

    remote_commands = []

    def fake_execute(machine_id, cmd, timeout=30):
        remote_commands.append(cmd)
        return ""

    def fake_run(machine_id, cmd, timeout=30, check=False):
        if "mountpoint -q" in cmd:
            return FakeCompletedProcess(returncode=0)
        if "findmnt -n -o SOURCE" in cmd:
            return FakeCompletedProcess(
                returncode=0,
                stdout=f"ely@10.0.0.5:{temp_dir}\n",
            )
        return FakeCompletedProcess(returncode=0)

    monkeypatch.setattr("seed_runner.mount.execute_ssh_command", fake_execute)
    monkeypatch.setattr("seed_runner.mount.run_ssh_command", fake_run)

    created = MountManager().create("vm-seed-01", os.path.join(temp_dir, "artifacts"))

    assert created["status"] == "mounted"
    assert not any("sshfs " in cmd for cmd in remote_commands)


def test_mount_destroy_kills_tmux_sessions_discovered_by_remote_path(temp_dir, monkeypatch):
    """Mount destroy should clean up stale tmux sessions that still live under the mount path."""
    monkeypatch.setenv("SEED_RUNNER_STATE_DIR", os.path.join(temp_dir, "state"))
    local_dir = os.path.join(temp_dir, "artifacts")
    os.makedirs(local_dir, exist_ok=True)

    state = load_state()
    state["mounts"]["mnt_test"] = {
        "mount_id": "mnt_test",
        "machine": "vm-seed-01",
        "local_path": local_dir,
        "remote_sync_dir": "/home/seed/.seed-runner/mounts/mnt_test/sync",
        "remote_path": "/home/seed/seed-experiment",
        "status": "mounted",
        "mounted_at": "2026-04-08T11:00:00Z",
        "session_ids": [],
    }
    save_state(state)

    remote_commands = []

    def fake_run(machine_id, cmd, timeout=30, check=False):
        remote_commands.append(cmd)
        if "tmux list-panes" in cmd:
            return FakeCompletedProcess(
                returncode=0,
                stdout=(
                    "stale_session\t/home/seed/seed-experiment\n"
                    "sync_stale\t/home/seed/.seed-runner/mounts/mnt_test/sync\n"
                ),
            )
        if "mountpoint -q" in cmd:
            return FakeCompletedProcess(returncode=1)
        return FakeCompletedProcess(returncode=0)

    monkeypatch.setattr("seed_runner.mount.run_ssh_command", fake_run)

    result = MountManager().destroy("mnt_test")

    assert result["status"] == "unmounted"
    assert any("tmux kill-session -t 'stale_session'" in cmd for cmd in remote_commands)
    assert any("tmux kill-session -t 'sync_stale'" in cmd for cmd in remote_commands)


def test_mount_destroy_raises_when_remote_mount_is_still_active(temp_dir, monkeypatch):
    """Mount destroy must not report success if the remote path is still mounted."""
    monkeypatch.setenv("SEED_RUNNER_STATE_DIR", os.path.join(temp_dir, "state"))
    local_dir = os.path.join(temp_dir, "artifacts")
    os.makedirs(local_dir, exist_ok=True)

    state = load_state()
    state["mounts"]["mnt_test"] = {
        "mount_id": "mnt_test",
        "machine": "vm-seed-01",
        "local_path": local_dir,
        "remote_sync_dir": "/home/seed/.seed-runner/mounts/mnt_test/sync",
        "remote_path": "/home/seed/seed-experiment",
        "status": "mounted",
        "mounted_at": "2026-04-08T11:00:00Z",
        "session_ids": [],
    }
    save_state(state)

    def fake_run(machine_id, cmd, timeout=30, check=False):
        if "tmux list-panes" in cmd:
            return FakeCompletedProcess(returncode=0, stdout="")
        if "mountpoint -q" in cmd:
            return FakeCompletedProcess(returncode=0)
        return FakeCompletedProcess(returncode=0)

    monkeypatch.setattr("seed_runner.mount.run_ssh_command", fake_run)

    with pytest.raises(RuntimeError, match="still active"):
        MountManager().destroy("mnt_test")

    state = load_state()
    assert state["mounts"]["mnt_test"]["status"] == "mounted"


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
    assert created_session["remote_work_dir"] == "/home/seed/seed-experiment"

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


def test_state_lock_preserves_concurrent_writes_across_processes(temp_dir):
    """Concurrent state writers should not lose each other's updates."""
    state_dir = os.path.join(temp_dir, "state")
    env = os.environ.copy()
    env["SEED_RUNNER_STATE_DIR"] = state_dir

    worker = """
import os
import time
from seed_runner.state import load_state, save_state, state_lock

session_id = os.environ["TEST_SESSION_ID"]
with state_lock():
    state = load_state()
    time.sleep(0.2)
    state["sessions"][session_id] = {"session_id": session_id}
    save_state(state)
"""

    processes = []
    for session_id in ("sess_a", "sess_b"):
        process_env = env.copy()
        process_env["TEST_SESSION_ID"] = session_id
        processes.append(
            subprocess.Popen(
                [sys.executable, "-c", worker],
                cwd=_repo_root(),
                env=process_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        )

    for process in processes:
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, stdout + stderr

    with open(os.path.join(state_dir, "state.json"), "r") as f:
        state = json.load(f)

    assert set(state["sessions"]) == {"sess_a", "sess_b"}


def test_session_exec_rejects_concurrent_commands_on_same_session(temp_dir, monkeypatch):
    """A session should reject overlapping exec requests instead of reusing the same log slot."""
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
    monkeypatch.setattr(
        "seed_runner.session.run_ssh_command",
        lambda *args, **kwargs: FakeCompletedProcess(returncode=0),
    )
    monkeypatch.setattr("seed_runner.session.execute_ssh_command", lambda *args, **kwargs: "")

    local_dir = os.path.join(temp_dir, "artifacts")
    mount = MountManager().create("vm-seed-01", local_dir)
    session = SessionManager().create("vm-seed-01", mount["mount_id"], "exp-web-01")

    command_started = threading.Event()

    def fake_exec(machine_id, cmd, timeout=30):
        if "tmux new-window" not in cmd:
            return ""

        command_started.set()

        def write_log():
            time.sleep(0.25)
            log_file = os.path.join(local_dir, "logs", "exp-web-01", "cmd_001.log")
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            with open(log_file, "w") as f:
                f.write("[2026-04-08T10:30:15Z] $ echo first\n")
                f.write("first\n")
                f.write("[2026-04-08T10:30:16Z] $ exit_code: 0\n")

        threading.Thread(target=write_log, daemon=True).start()
        return ""

    monkeypatch.setattr("seed_runner.session.execute_ssh_command", fake_exec)

    results = {}

    def run_first_command():
        results["first"] = SessionManager().exec(session["session_id"], "echo first")

    worker = threading.Thread(target=run_first_command)
    worker.start()

    assert command_started.wait(timeout=1)
    with pytest.raises(RuntimeError, match="busy"):
        SessionManager().exec(session["session_id"], "echo second")

    worker.join(timeout=3)
    assert not worker.is_alive()

    first_result = results["first"]
    assert first_result["exit_code"] == 0
    assert first_result["log_filename"] == "cmd_001.log"

    status = SessionManager().status(session["session_id"])
    assert status["status"] == "active"
    assert status["busy"] is False
    assert status["command_count"] == 1

    with open(os.path.join(local_dir, "metadata.json"), "r") as f:
        metadata = json.load(f)

    commands = metadata["sessions"][0]["commands"]
    assert len(commands) == 1
    assert commands[0]["index"] == 1
    assert commands[0]["cmd"] == "echo first"
