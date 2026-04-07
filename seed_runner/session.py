"""Session management for seed-runner."""

import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from seed_runner.remote import execute_ssh_command, run_ssh_command
from seed_runner.state import load_mount_metadata, load_state, save_mount_metadata, save_state
from seed_runner.utils import ensure_dir, escape_shell_arg, generate_id, get_timestamp, parse_timestamp, read_file


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class SessionManager:
    """Manage tmux-backed remote execution sessions."""

    def _get_state(self) -> Dict[str, Any]:
        return load_state()

    def _get_session(self, session_id: str) -> Dict[str, Any]:
        state = self._get_state()
        if session_id not in state["sessions"]:
            raise KeyError(f"Session '{session_id}' not found")
        return state["sessions"][session_id]

    def _save_session(self, session_info: Dict[str, Any]) -> None:
        state = self._get_state()
        state["sessions"][session_info["session_id"]] = session_info
        save_state(state)

    def _compute_status(self, session_info: Dict[str, Any]) -> str:
        if session_info.get("status") == "destroyed":
            return "destroyed"

        created_at = parse_timestamp(session_info["created_at"])
        elapsed_seconds = int((_now_utc() - created_at).total_seconds())
        if elapsed_seconds > session_info["timeout_seconds"]:
            return "timeout"

        state = self._get_state()
        mount = state["mounts"].get(session_info["mount_id"])
        if not mount or mount.get("status") == "unmounted":
            return "error"

        result = run_ssh_command(
            session_info["machine"],
            f"tmux has-session -t {escape_shell_arg(session_info['tmux_session'])}",
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            return "error"
        return "active"

    def _update_mount_metadata_session(self, session_info: Dict[str, Any]) -> None:
        metadata = load_mount_metadata(session_info["local_mount_point"])
        sessions = metadata.setdefault("sessions", [])
        sessions.append(
            {
                "session_id": session_info["session_id"],
                "session_name": session_info["session_name"],
                "created_at": session_info["created_at"],
                "commands": [],
            }
        )
        save_mount_metadata(session_info["local_mount_point"], metadata)

    def _append_command_metadata(
        self,
        session_info: Dict[str, Any],
        command_index: int,
        cmd: str,
        log_filename: str,
        exit_code: int,
        executed_at: str,
    ) -> None:
        metadata = load_mount_metadata(session_info["local_mount_point"])
        for item in metadata.get("sessions", []):
            if item["session_id"] != session_info["session_id"]:
                continue
            item.setdefault("commands", []).append(
                {
                    "index": command_index,
                    "cmd": cmd,
                    "log_file": log_filename,
                    "exit_code": exit_code,
                    "executed_at": executed_at,
                }
            )
            save_mount_metadata(session_info["local_mount_point"], metadata)
            return

    def create(
        self,
        machine_id: str,
        mount_id: str,
        session_name: str,
        timeout: int = 3600,
    ) -> Dict[str, Any]:
        """Create a new tmux session bound to an existing mount."""
        state = self._get_state()
        mount = state["mounts"].get(mount_id)
        if not mount:
            raise KeyError(f"Mount '{mount_id}' not found")
        if mount["status"] != "mounted":
            raise RuntimeError(f"Mount '{mount_id}' is not mounted")
        if mount["machine"] != machine_id:
            raise RuntimeError("Session machine does not match mount machine")

        for existing in state["sessions"].values():
            if existing["mount_id"] != mount_id:
                continue
            if existing["session_name"] != session_name:
                continue
            if existing.get("status") != "destroyed":
                raise ValueError(f"Session name already in use: {session_name}")

        session_id = generate_id("sess")
        tmux_session = f"seed_{session_id}"
        local_mount_point = mount["local_path"]
        remote_work_dir = mount["remote_path"]

        ensure_dir(os.path.join(local_mount_point, "logs", session_name))

        execute_ssh_command(
            machine_id,
            (
                f"mkdir -p {escape_shell_arg(os.path.join(remote_work_dir, 'logs', session_name))} "
                f"{escape_shell_arg(os.path.join(remote_work_dir, 'artifacts'))} && "
                f"tmux new-session -d -s {escape_shell_arg(tmux_session)} "
                f"-c {escape_shell_arg(remote_work_dir)}"
            ),
            timeout=timeout,
        )

        session_info = {
            "session_id": session_id,
            "session_name": session_name,
            "machine": machine_id,
            "mount_id": mount_id,
            "local_mount_point": local_mount_point,
            "remote_work_dir": remote_work_dir,
            "status": "ready",
            "tmux_session": tmux_session,
            "created_at": get_timestamp(),
            "command_count": 0,
            "timeout_seconds": timeout,
        }

        state["sessions"][session_id] = session_info
        mount.setdefault("session_ids", []).append(session_id)
        state["mounts"][mount_id] = mount
        save_state(state)
        self._update_mount_metadata_session(session_info)

        return {
            "session_id": session_id,
            "session_name": session_name,
            "machine": machine_id,
            "mount_id": mount_id,
            "local_mount_point": local_mount_point,
            "remote_work_dir": remote_work_dir,
            "status": "ready",
            "tmux_session": tmux_session,
            "created_at": session_info["created_at"],
        }

    def exec(
        self,
        session_id: str,
        cmd: str,
        timeout: int = 300,
    ) -> Dict[str, Any]:
        """Execute a command within a session."""
        state = self._get_state()
        if session_id not in state["sessions"]:
            raise KeyError(f"Session '{session_id}' not found")

        session_info = state["sessions"][session_id]
        computed_status = self._compute_status(session_info)
        if computed_status == "destroyed":
            raise RuntimeError(f"Session '{session_id}' has been destroyed")
        if computed_status == "timeout":
            session_info["status"] = "timeout"
            state["sessions"][session_id] = session_info
            save_state(state)
            raise RuntimeError(f"Session '{session_id}' has timed out")
        if computed_status == "error":
            raise RuntimeError(f"Session '{session_id}' is not active")

        mount = state["mounts"].get(session_info["mount_id"])
        if not mount or mount.get("status") != "mounted":
            raise RuntimeError(f"Mount '{session_info['mount_id']}' is not mounted")

        session_name = session_info["session_name"]
        local_mount_point = session_info["local_mount_point"]
        remote_work_dir = session_info["remote_work_dir"]
        tmux_session = session_info["tmux_session"]

        session_info["command_count"] += 1
        command_index = session_info["command_count"]
        log_filename = f"cmd_{command_index:03d}.log"

        local_log_dir = os.path.join(local_mount_point, "logs", session_name)
        ensure_dir(local_log_dir)
        local_log_file = os.path.join(local_log_dir, log_filename)

        remote_log_dir = os.path.join(remote_work_dir, "logs", session_name)
        remote_log_file = os.path.join(remote_log_dir, log_filename)

        script = "\n".join(
            [
                f"mkdir -p {escape_shell_arg(remote_log_dir)}",
                f"cd {escape_shell_arg(remote_work_dir)}",
                (
                    "printf '[%s] $ %s\\n' "
                    "\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\" "
                    f"{escape_shell_arg(cmd)} >> {escape_shell_arg(remote_log_file)}"
                ),
                f"({cmd}) >> {escape_shell_arg(remote_log_file)} 2>&1",
                "exit_code=$?",
                (
                    "printf '[%s] $ exit_code: %s\\n' "
                    "\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\" "
                    "\"$exit_code\" "
                    f">> {escape_shell_arg(remote_log_file)}"
                ),
                "exit \"$exit_code\"",
            ]
        )

        window_name = f"cmd_{command_index:03d}"
        remote_cmd = (
            f"tmux new-window -d -t {escape_shell_arg(tmux_session)} "
            f"-n {escape_shell_arg(window_name)} "
            f"{escape_shell_arg(f'bash -lc {escape_shell_arg(script)}')}"
        )

        start_time = time.time()
        execute_ssh_command(session_info["machine"], remote_cmd, timeout=timeout)

        exit_code: Optional[int] = None
        while time.time() - start_time < timeout:
            if os.path.exists(local_log_file):
                log_content = read_file(local_log_file)
                if "exit_code:" in log_content:
                    for line in reversed(log_content.strip().splitlines()):
                        if "exit_code:" not in line:
                            continue
                        try:
                            exit_code = int(line.split("exit_code:")[-1].strip())
                            break
                        except ValueError:
                            continue
                    if exit_code is not None:
                        break
            time.sleep(0.5)

        if exit_code is None:
            raise RuntimeError(f"Command execution timeout after {timeout} seconds")

        executed_at = get_timestamp()
        duration_ms = int((time.time() - start_time) * 1000)

        session_info["status"] = "active"
        session_info["last_command"] = cmd
        session_info["last_exit_code"] = exit_code
        session_info["last_executed_at"] = executed_at
        state["sessions"][session_id] = session_info
        save_state(state)
        self._append_command_metadata(
            session_info=session_info,
            command_index=command_index,
            cmd=cmd,
            log_filename=log_filename,
            exit_code=exit_code,
            executed_at=executed_at,
        )

        return {
            "session_id": session_id,
            "session_name": session_name,
            "command": cmd,
            "exit_code": exit_code,
            "log_file_local": local_log_file,
            "log_file_remote": remote_log_file,
            "log_filename": log_filename,
            "executed_at": executed_at,
            "duration_ms": duration_ms,
        }

    def status(self, session_id: str) -> Dict[str, Any]:
        """Get session status."""
        state = self._get_state()
        if session_id not in state["sessions"]:
            raise KeyError(f"Session '{session_id}' not found")

        session_info = state["sessions"][session_id]
        session_info["status"] = self._compute_status(session_info)
        state["sessions"][session_id] = session_info
        save_state(state)

        created_at = parse_timestamp(session_info["created_at"])
        elapsed_seconds = int((_now_utc() - created_at).total_seconds())

        result = session_info.copy()
        result["elapsed_seconds"] = elapsed_seconds
        return result

    def destroy(self, session_id: str) -> Dict[str, Any]:
        """Destroy a session while preserving logs."""
        state = self._get_state()
        if session_id not in state["sessions"]:
            raise KeyError(f"Session '{session_id}' not found")

        session_info = state["sessions"][session_id]
        if session_info.get("status") != "destroyed":
            run_ssh_command(
                session_info["machine"],
                f"tmux kill-session -t {escape_shell_arg(session_info['tmux_session'])} || true",
                timeout=10,
                check=False,
            )

        logs_location = os.path.join(
            session_info["local_mount_point"],
            "logs",
            session_info["session_name"],
        )
        session_info["status"] = "destroyed"
        session_info["destroyed_at"] = get_timestamp()
        session_info["logs_preserved"] = True
        session_info["logs_location"] = logs_location
        state["sessions"][session_id] = session_info
        save_state(state)

        return {
            "session_id": session_id,
            "session_name": session_info["session_name"],
            "status": "destroyed",
            "destroyed_at": session_info["destroyed_at"],
            "logs_preserved": True,
            "logs_location": logs_location,
        }


_session_manager: Optional[SessionManager] = None


def get_session_manager() -> SessionManager:
    """Get or create the global SessionManager instance."""
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
    return _session_manager
