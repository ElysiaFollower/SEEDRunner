"""Mount management for seed-runner."""

import getpass
import os
import posixpath
import socket
from typing import Any, Dict, Optional, Set

from seed_runner.remote import execute_ssh_command, run_ssh_command
from seed_runner.state import load_state, save_mount_metadata, save_state, state_lock
from seed_runner.utils import ensure_dir, escape_shell_arg, generate_id, get_timestamp


def _normalize_remote_dir(machine_user: str, remote_dir: Optional[str]) -> str:
    """Expand the remote work directory to an absolute path."""
    value = remote_dir or "~/seed-experiment"
    if value.startswith("~"):
        suffix = value[2:] if value.startswith("~/") else value[1:]
        if suffix:
            return f"/home/{machine_user}/{suffix}"
        return f"/home/{machine_user}"
    return value


class MountManager:
    """Manage remote sshfs mounts backed by a local workspace directory."""

    def __init__(
        self,
        local_user: Optional[str] = None,
        local_ssh_port: Optional[int] = None,
        local_host: Optional[str] = None,
        remote_to_local_key: Optional[str] = None,
    ):
        self.local_user = local_user
        self.local_ssh_port = local_ssh_port
        self.local_host = local_host
        self.remote_to_local_key = remote_to_local_key

    def _discover_local_host(self) -> str:
        """Detect the local IP address that the remote VM can reach."""
        env_local_host = os.getenv("SEED_RUNNER_LOCAL_HOST")
        if self.local_host:
            return self.local_host
        if env_local_host:
            self.local_host = env_local_host
            return env_local_host

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            candidate = sock.getsockname()[0]

        if candidate.startswith("127."):
            raise RuntimeError(
                "Could not determine a non-loopback local host. "
                "Set SEED_RUNNER_LOCAL_HOST explicitly."
            )
        self.local_host = candidate
        return candidate

    def _get_mount(self, mount_id: str) -> Dict[str, Any]:
        state = load_state()
        if mount_id not in state["mounts"]:
            raise KeyError(f"Mount '{mount_id}' not found")
        return state["mounts"][mount_id]

    def _remote_mount_source(self, machine_id: str, remote_path: str) -> Optional[str]:
        """Return the current remote mount source when the path is already mounted."""
        quoted_path = escape_shell_arg(remote_path)
        mounted = run_ssh_command(
            machine_id,
            f"mountpoint -q {quoted_path}",
            timeout=10,
            check=False,
        )
        if mounted.returncode != 0:
            return None

        source = run_ssh_command(
            machine_id,
            f"findmnt -n -o SOURCE --target {quoted_path}",
            timeout=10,
            check=False,
        )
        value = source.stdout.strip()
        return value or None

    def _save_mount(self, mount_info: Dict[str, Any]) -> None:
        with state_lock():
            state = load_state()
            state["mounts"][mount_info["mount_id"]] = mount_info
            save_state(state)

    def _tmux_sessions_using_path(self, machine_id: str, remote_path: str) -> Set[str]:
        """Discover tmux sessions whose panes currently live under the remote path."""
        result = run_ssh_command(
            machine_id,
            "tmux list-panes -a -F '#{session_name}\t#{pane_current_path}' 2>/dev/null || true",
            timeout=10,
            check=False,
        )
        sessions: Set[str] = set()
        for line in result.stdout.splitlines():
            if "\t" not in line:
                continue
            session_name, pane_path = line.split("\t", 1)
            pane_path = pane_path.strip()
            if pane_path == remote_path or pane_path.startswith(f"{remote_path}/"):
                sessions.add(session_name.strip())
        return sessions

    def _session_count(self, mount_info: Dict[str, Any]) -> int:
        return len(mount_info.get("session_ids", []))

    def _public_mount_info(self, mount_info: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "mount_id": mount_info["mount_id"],
            "machine": mount_info["machine"],
            "local_path": mount_info["local_path"],
            "remote_path": mount_info["remote_path"],
            "status": mount_info["status"],
            "mounted_at": mount_info["mounted_at"],
        }

    def create(
        self,
        machine_id: str,
        local_dir: str,
        remote_dir: Optional[str] = None,
        timeout: int = 30,
    ) -> Dict[str, Any]:
        """Create a new mount."""
        from seed_runner.config import get_machine_config

        machine_config = get_machine_config(machine_id)
        local_path = os.path.abspath(local_dir)
        local_workspace_root = os.path.dirname(local_path)
        remote_path = _normalize_remote_dir(machine_config.user, remote_dir)
        mount_id = generate_id("mnt")
        remote_sync_dir = f"/home/{machine_config.user}/.seed-runner/mounts/{mount_id}/sync"
        local_user = self.local_user or os.getenv("SEED_RUNNER_LOCAL_USER") or getpass.getuser()
        local_ssh_port = self.local_ssh_port or int(os.getenv("SEED_RUNNER_LOCAL_SSH_PORT", "22"))
        local_host = self._discover_local_host()
        expected_source = f"{local_user}@{local_host}:{local_workspace_root}"
        remote_key = (
            self.remote_to_local_key
            or os.getenv("SEED_RUNNER_REMOTE_TO_LOCAL_KEY")
            or f"~/.ssh/{os.path.basename(machine_config.key_path)}"
        )

        state = load_state()
        for existing in state["mounts"].values():
            if existing.get("status") != "mounted":
                continue
            if existing["local_path"] == local_path:
                raise ValueError(f"Local directory already mounted: {local_path}")
            if existing["machine"] == machine_id and existing["remote_path"] == remote_path:
                raise ValueError(f"Remote directory already mounted: {remote_path}")

        ensure_dir(local_path)
        ensure_dir(os.path.join(local_path, "logs"))
        ensure_dir(os.path.join(local_path, "artifacts"))

        reused_existing_mount = False
        existing_source = self._remote_mount_source(machine_id, remote_sync_dir)
        if existing_source:
            if existing_source == expected_source:
                reused_existing_mount = True
            else:
                raise RuntimeError(
                    "Remote mount point already in use: "
                    f"{remote_sync_dir} is mounted from {existing_source}"
                )
        else:
            execute_ssh_command(
                machine_id,
                (
                    f"mkdir -p {escape_shell_arg(remote_sync_dir)} "
                    f"{escape_shell_arg(remote_path)} "
                    f"{escape_shell_arg(posixpath.join(remote_sync_dir, 'artifacts'))}"
                ),
                timeout=timeout,
            )

            source = escape_shell_arg(expected_source)
            mount_point = escape_shell_arg(remote_sync_dir)
            mount_cmd = (
                f"sshfs -o reconnect,nonempty,StrictHostKeyChecking=no,IdentityFile={remote_key} "
                f"-p {local_ssh_port} {source} {mount_point}"
            )
            execute_ssh_command(machine_id, mount_cmd, timeout=timeout)

            verify = run_ssh_command(
                machine_id,
                f"mountpoint -q {escape_shell_arg(remote_sync_dir)}",
                timeout=timeout,
                check=False,
            )
            if verify.returncode != 0:
                raise RuntimeError(f"Remote mount did not become ready: {verify.stderr}")

        mounted_at = get_timestamp()
        mount_info = {
            "mount_id": mount_id,
            "machine": machine_id,
            "local_path": local_path,
            "local_workspace_root": local_workspace_root,
            "remote_path": remote_path,
            "remote_sync_dir": remote_sync_dir,
            "local_host": local_host,
            "local_user": local_user,
            "local_ssh_port": local_ssh_port,
            "remote_to_local_key": remote_key,
            "status": "mounted",
            "mounted_at": mounted_at,
            "session_ids": [],
        }

        self._save_mount(mount_info)
        save_mount_metadata(
            local_path,
            {
                "mount_id": mount_id,
                "machine": machine_id,
                "local_path": local_path,
                "local_workspace_root": local_workspace_root,
                "remote_path": remote_path,
                "remote_sync_dir": remote_sync_dir,
                "mounted_at": mounted_at,
                "sessions": [],
            },
        )
        return self._public_mount_info(mount_info)

    def status(self, mount_id: str) -> Dict[str, Any]:
        """Get mount status."""
        state = load_state()
        if mount_id not in state["mounts"]:
            raise KeyError(f"Mount '{mount_id}' not found")

        mount_info = state["mounts"][mount_id].copy()
        if mount_info["status"] != "unmounted":
            verify = run_ssh_command(
                mount_info["machine"],
                f"mountpoint -q {escape_shell_arg(mount_info['remote_sync_dir'])}",
                timeout=10,
                check=False,
            )
            mount_info["status"] = "mounted" if verify.returncode == 0 else "error"

        result = self._public_mount_info(mount_info)
        result["session_count"] = self._session_count(mount_info)
        return result

    def destroy(self, mount_id: str, cleanup: bool = False) -> Dict[str, Any]:
        """Destroy a mount and clean up any live tmux sessions using it."""
        state = load_state()
        if mount_id not in state["mounts"]:
            raise KeyError(f"Mount '{mount_id}' not found")

        mount_info = state["mounts"][mount_id]
        local_path = mount_info["local_path"]
        remote_path = mount_info["remote_path"]
        remote_sync_dir = mount_info["remote_sync_dir"]
        machine_id = mount_info["machine"]

        tmux_sessions_to_kill: Set[str] = set()
        for session_id in mount_info.get("session_ids", []):
            session = state["sessions"].get(session_id)
            if not session or session.get("status") == "destroyed":
                continue
            tmux_sessions_to_kill.add(session["tmux_session"])

        tmux_sessions_to_kill.update(self._tmux_sessions_using_path(machine_id, remote_path))
        tmux_sessions_to_kill.update(self._tmux_sessions_using_path(machine_id, remote_sync_dir))
        for tmux_session in sorted(tmux_sessions_to_kill):
            run_ssh_command(
                machine_id,
                f"tmux kill-session -t {escape_shell_arg(tmux_session)} || true",
                timeout=10,
                check=False,
            )

        run_ssh_command(
            machine_id,
            (
                f"fusermount -u {escape_shell_arg(remote_sync_dir)} || "
                f"umount {escape_shell_arg(remote_sync_dir)} || "
                f"umount -f {escape_shell_arg(remote_sync_dir)} || true"
            ),
            timeout=30,
            check=False,
        )

        if cleanup:
            run_ssh_command(
                machine_id,
                f"rm -rf {escape_shell_arg(remote_sync_dir)} {escape_shell_arg(remote_path)}",
                timeout=30,
                check=False,
            )

        verify = run_ssh_command(
            machine_id,
            f"mountpoint -q {escape_shell_arg(remote_sync_dir)}",
            timeout=10,
            check=False,
        )
        if verify.returncode == 0:
            raise RuntimeError(f"Remote mount is still active: {remote_sync_dir}")

        unmounted_at = get_timestamp()
        with state_lock():
            state = load_state()
            if mount_id not in state["mounts"]:
                raise KeyError(f"Mount '{mount_id}' not found")

            mount_info = state["mounts"][mount_id]
            mount_info["status"] = "unmounted"
            mount_info["unmounted_at"] = unmounted_at
            state["mounts"][mount_id] = mount_info

            for session_id in mount_info.get("session_ids", []):
                session = state["sessions"].get(session_id)
                if not session or session.get("status") == "destroyed":
                    continue
                session["status"] = "destroyed"
                session["destroyed_at"] = unmounted_at
                session["logs_preserved"] = True
                session["logs_location"] = os.path.join(local_path, "logs", session["session_name"])
                session["busy"] = False
                session.pop("active_command", None)
                state["sessions"][session_id] = session

            save_state(state)

        return {
            "mount_id": mount_id,
            "status": "unmounted",
            "unmounted_at": unmounted_at,
            "artifacts_preserved": True,
            "artifacts_location": local_path,
        }


_mount_manager: Optional[MountManager] = None


def get_mount_manager() -> MountManager:
    """Get or create the global MountManager instance."""
    global _mount_manager
    if _mount_manager is None:
        _mount_manager = MountManager()
    return _mount_manager
