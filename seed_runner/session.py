"""Session management for seed-runner."""

import os
import posixpath
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from seed_runner.remote import execute_ssh_command, run_ssh_command
from seed_runner.state import load_mount_metadata, load_state, save_mount_metadata, save_state, state_lock
from seed_runner.utils import ensure_dir, escape_shell_arg, generate_id, get_timestamp, parse_timestamp, read_file


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _read_exit_code(log_file: str) -> Optional[int]:
    """Read the trailing exit code marker from a completed command log."""
    if not os.path.exists(log_file):
        return None

    log_content = read_file(log_file)
    if "exit_code:" not in log_content:
        return None

    for line in reversed(log_content.strip().splitlines()):
        if "exit_code:" not in line:
            continue
        try:
            return int(line.split("exit_code:")[-1].strip())
        except ValueError:
            continue
    return None


class SessionManager:
    """Manage tmux-backed remote execution sessions."""

    def _sync_inputs_command(self, remote_sync_dir: str, remote_work_dir: str) -> str:
        quoted_sync_dir = escape_shell_arg(remote_sync_dir)
        quoted_work_dir = escape_shell_arg(remote_work_dir)
        return "\n".join(
            [
                f"mkdir -p {quoted_work_dir}",
                "if command -v rsync >/dev/null 2>&1; then",
                f"  rsync -a --delete --exclude 'artifacts/' {quoted_sync_dir}/ {quoted_work_dir}/",
                "else",
                (
                    f"  find {quoted_work_dir} -mindepth 1 -maxdepth 1 "
                    "! -name artifacts ! -name logs -exec rm -rf {} +"
                ),
                (
                    f"  cd {quoted_sync_dir} && tar --exclude='./artifacts' -cf - . | "
                    f"(cd {quoted_work_dir} && tar -xf -)"
                ),
                "fi",
                f"mkdir -p {escape_shell_arg(posixpath.join(remote_work_dir, 'artifacts'))}",
                f"mkdir -p {escape_shell_arg(posixpath.join(remote_work_dir, 'logs'))}",
            ]
        )

    def _sync_outputs_command(self, remote_work_dir: str, remote_sync_dir: str) -> str:
        sync_artifacts_dir = posixpath.join(remote_sync_dir, "artifacts")
        work_artifacts_dir = posixpath.join(remote_work_dir, "artifacts")
        work_logs_dir = posixpath.join(remote_work_dir, "logs")
        return "\n".join(
            [
                f"mkdir -p {escape_shell_arg(sync_artifacts_dir)}",
                "if command -v rsync >/dev/null 2>&1; then",
                (
                    f"  rsync -a {escape_shell_arg(work_logs_dir)}/ "
                    f"{escape_shell_arg(posixpath.join(sync_artifacts_dir, 'logs'))}/"
                ),
                (
                    f"  rsync -a {escape_shell_arg(work_artifacts_dir)}/ "
                    f"{escape_shell_arg(posixpath.join(sync_artifacts_dir, 'artifacts'))}/"
                ),
                "else",
                (
                    f"  mkdir -p {escape_shell_arg(posixpath.join(sync_artifacts_dir, 'logs'))} "
                    f"{escape_shell_arg(posixpath.join(sync_artifacts_dir, 'artifacts'))}"
                ),
                (
                    f"  cp -a {escape_shell_arg(work_logs_dir)}/. "
                    f"{escape_shell_arg(posixpath.join(sync_artifacts_dir, 'logs'))}/ 2>/dev/null || true"
                ),
                (
                    f"  cp -a {escape_shell_arg(work_artifacts_dir)}/. "
                    f"{escape_shell_arg(posixpath.join(sync_artifacts_dir, 'artifacts'))}/ 2>/dev/null || true"
                ),
                "fi",
            ]
        )

    def _get_state(self) -> Dict[str, Any]:
        return load_state()

    def _get_session(self, session_id: str) -> Dict[str, Any]:
        state = self._get_state()
        if session_id not in state["sessions"]:
            raise KeyError(f"Session '{session_id}' not found")
        return state["sessions"][session_id]

    def _save_session(self, session_info: Dict[str, Any]) -> None:
        with state_lock():
            state = self._get_state()
            state["sessions"][session_info["session_id"]] = session_info
            save_state(state)

    def _compute_status(
        self,
        session_info: Dict[str, Any],
        mount_info: Optional[Dict[str, Any]] = None,
    ) -> str:
        if session_info.get("status") == "destroyed":
            return "destroyed"

        created_at = parse_timestamp(session_info["created_at"])
        elapsed_seconds = int((_now_utc() - created_at).total_seconds())
        if elapsed_seconds > session_info["timeout_seconds"]:
            return "timeout"

        if session_info.get("busy"):
            return "busy"

        mount = mount_info
        if mount is None:
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

    def _busy_error(self, session_info: Dict[str, Any]) -> str:
        active_command = session_info.get("active_command") or {}
        label = active_command.get("log_filename") or active_command.get("cmd") or "another command"
        return f"Session '{session_info['session_id']}' is busy running {label}"

    def _update_mount_metadata_session(self, session_info: Dict[str, Any]) -> None:
        with state_lock():
            metadata = load_mount_metadata(session_info["local_mount_point"])
            sessions = metadata.setdefault("sessions", [])
            if any(item.get("session_id") == session_info["session_id"] for item in sessions):
                return
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
        with state_lock():
            metadata = load_mount_metadata(session_info["local_mount_point"])
            for item in metadata.get("sessions", []):
                if item["session_id"] != session_info["session_id"]:
                    continue
                commands = item.setdefault("commands", [])
                record = {
                    "index": command_index,
                    "cmd": cmd,
                    "log_file": log_filename,
                    "exit_code": exit_code,
                    "executed_at": executed_at,
                }
                for index, existing in enumerate(commands):
                    if existing.get("index") != command_index:
                        continue
                    commands[index] = record
                    save_mount_metadata(session_info["local_mount_point"], metadata)
                    return
                commands.append(record)
                save_mount_metadata(session_info["local_mount_point"], metadata)
                return

    def _clear_active_command(
        self,
        session_id: str,
        command_index: int,
        status: str = "active",
    ) -> Optional[Dict[str, Any]]:
        with state_lock():
            state = self._get_state()
            session_info = state["sessions"].get(session_id)
            if not session_info:
                return None

            active_command = session_info.get("active_command") or {}
            if (
                not session_info.get("busy")
                or active_command.get("command_index") != command_index
            ):
                return session_info.copy()

            session_info["busy"] = False
            session_info["status"] = status
            session_info.pop("active_command", None)
            state["sessions"][session_id] = session_info
            save_state(state)
            return session_info.copy()

    def _complete_active_command(
        self,
        session_id: str,
        exit_code: int,
        executed_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        active_command: Optional[Dict[str, Any]] = None
        with state_lock():
            state = self._get_state()
            session_info = state["sessions"].get(session_id)
            if not session_info:
                return None

            active_command = session_info.get("active_command")
            if not session_info.get("busy") or not active_command:
                return session_info.copy()

            executed_at = executed_at or get_timestamp()
            session_info["busy"] = False
            session_info["status"] = "active"
            session_info["last_command"] = active_command["cmd"]
            session_info["last_exit_code"] = exit_code
            session_info["last_executed_at"] = executed_at
            session_info.pop("active_command", None)
            state["sessions"][session_id] = session_info
            save_state(state)
            finalized_session = session_info.copy()

        self._append_command_metadata(
            session_info=finalized_session,
            command_index=active_command["command_index"],
            cmd=active_command["cmd"],
            log_filename=active_command["log_filename"],
            exit_code=exit_code,
            executed_at=executed_at,
        )
        return finalized_session

    def _refresh_active_command(self, session_id: str) -> Optional[Dict[str, Any]]:
        state = self._get_state()
        session_info = state["sessions"].get(session_id)
        if not session_info or not session_info.get("busy"):
            return session_info

        active_command = session_info.get("active_command") or {}
        log_file = active_command.get("local_log_file")
        if not log_file:
            return session_info

        exit_code = _read_exit_code(log_file)
        if exit_code is None:
            return session_info

        return self._complete_active_command(session_id, exit_code)

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
        remote_sync_dir = mount["remote_sync_dir"]

        ensure_dir(os.path.join(local_mount_point, "logs", session_name))

        execute_ssh_command(
            machine_id,
            (
                f"mkdir -p {escape_shell_arg(os.path.join(remote_work_dir, 'logs', session_name))} "
                f"{escape_shell_arg(os.path.join(remote_work_dir, 'artifacts'))} "
                f"{escape_shell_arg(posixpath.join(remote_sync_dir, 'artifacts', 'logs', session_name))} && "
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
            "busy": False,
        }

        conflict: Optional[Exception] = None
        with state_lock():
            state = self._get_state()
            mount = state["mounts"].get(mount_id)
            if not mount:
                conflict = KeyError(f"Mount '{mount_id}' not found")
            elif mount["status"] != "mounted":
                conflict = RuntimeError(f"Mount '{mount_id}' is not mounted")
            elif mount["machine"] != machine_id:
                conflict = RuntimeError("Session machine does not match mount machine")
            else:
                for existing in state["sessions"].values():
                    if existing["mount_id"] != mount_id:
                        continue
                    if existing["session_name"] != session_name:
                        continue
                    if existing.get("status") != "destroyed":
                        conflict = ValueError(f"Session name already in use: {session_name}")
                        break

            if conflict is None:
                state["sessions"][session_id] = session_info
                mount.setdefault("session_ids", []).append(session_id)
                state["mounts"][mount_id] = mount
                save_state(state)

        if conflict is not None:
            run_ssh_command(
                machine_id,
                f"tmux kill-session -t {escape_shell_arg(tmux_session)} || true",
                timeout=10,
                check=False,
            )
            raise conflict

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
        self._refresh_active_command(session_id)
        state = self._get_state()
        if session_id not in state["sessions"]:
            raise KeyError(f"Session '{session_id}' not found")

        session_info = state["sessions"][session_id]
        mount = state["mounts"].get(session_info["mount_id"])
        computed_status = self._compute_status(session_info, mount)
        if computed_status == "destroyed":
            raise RuntimeError(f"Session '{session_id}' has been destroyed")
        if computed_status == "timeout":
            raise RuntimeError(f"Session '{session_id}' has timed out")
        if computed_status == "busy":
            raise RuntimeError(self._busy_error(session_info))
        if computed_status == "error":
            raise RuntimeError(f"Session '{session_id}' is not active")

        if not mount or mount.get("status") != "mounted":
            raise RuntimeError(f"Mount '{session_info['mount_id']}' is not mounted")

        with state_lock():
            state = self._get_state()
            if session_id not in state["sessions"]:
                raise KeyError(f"Session '{session_id}' not found")

            session_info = state["sessions"][session_id]
            mount = state["mounts"].get(session_info["mount_id"])
            if session_info.get("status") == "destroyed":
                raise RuntimeError(f"Session '{session_id}' has been destroyed")

            created_at = parse_timestamp(session_info["created_at"])
            elapsed_seconds = int((_now_utc() - created_at).total_seconds())
            if elapsed_seconds > session_info["timeout_seconds"]:
                session_info["status"] = "timeout"
                state["sessions"][session_id] = session_info
                save_state(state)
                raise RuntimeError(f"Session '{session_id}' has timed out")

            if session_info.get("busy"):
                raise RuntimeError(self._busy_error(session_info))
            if not mount or mount.get("status") != "mounted":
                raise RuntimeError(f"Mount '{session_info['mount_id']}' is not mounted")

            session_name = session_info["session_name"]
            local_mount_point = session_info["local_mount_point"]
            remote_work_dir = session_info["remote_work_dir"]
            remote_sync_dir = mount["remote_sync_dir"]
            tmux_session = session_info["tmux_session"]

            command_index = session_info.get("command_count", 0) + 1
            log_filename = f"cmd_{command_index:03d}.log"
            local_log_dir = os.path.join(local_mount_point, "logs", session_name)
            local_log_file = os.path.join(local_log_dir, log_filename)
            remote_log_dir = posixpath.join(remote_sync_dir, "artifacts", "logs", session_name)
            remote_log_file = os.path.join(remote_log_dir, log_filename)

            session_info["command_count"] = command_index
            session_info["busy"] = True
            session_info["status"] = "busy"
            session_info["active_command"] = {
                "command_index": command_index,
                "cmd": cmd,
                "log_filename": log_filename,
                "local_log_file": local_log_file,
                "remote_log_file": remote_log_file,
                "started_at": get_timestamp(),
            }
            state["sessions"][session_id] = session_info
            save_state(state)

        ensure_dir(local_log_dir)

        script = "\n".join(
            [
                self._sync_inputs_command(remote_sync_dir, remote_work_dir),
                f"mkdir -p {escape_shell_arg(remote_log_dir)}",
                f"cd {escape_shell_arg(remote_work_dir)}",
                (
                    "printf '[%s] $ %s\\n' "
                    "\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\" "
                    f"{escape_shell_arg(cmd)} >> {escape_shell_arg(remote_log_file)}"
                ),
                f"({cmd}) >> {escape_shell_arg(remote_log_file)} 2>&1",
                "exit_code=$?",
                self._sync_outputs_command(remote_work_dir, remote_sync_dir),
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
        try:
            execute_ssh_command(session_info["machine"], remote_cmd, timeout=timeout)
        except Exception:
            self._clear_active_command(session_id, command_index)
            raise

        exit_code: Optional[int] = None
        while time.time() - start_time < timeout:
            exit_code = _read_exit_code(local_log_file)
            if exit_code is not None:
                break
            time.sleep(0.5)

        finalized_session: Optional[Dict[str, Any]] = None
        if exit_code is None:
            refreshed = self._refresh_active_command(session_id)
            if refreshed and not refreshed.get("busy"):
                finalized_session = refreshed
                exit_code = refreshed.get("last_exit_code")
            else:
                raise RuntimeError(
                    f"Command execution timeout after {timeout} seconds; "
                    "session remains busy until command exits"
                )
        else:
            finalized_session = self._complete_active_command(
                session_id,
                exit_code,
                get_timestamp(),
            )

        executed_at = (
            finalized_session.get("last_executed_at")
            if finalized_session
            else get_timestamp()
        )
        duration_ms = int((time.time() - start_time) * 1000)

        return {
            "session_id": session_id,
            "session_name": session_info["session_name"],
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
        self._refresh_active_command(session_id)
        state = self._get_state()
        if session_id not in state["sessions"]:
            raise KeyError(f"Session '{session_id}' not found")

        session_info = state["sessions"][session_id].copy()
        mount = state["mounts"].get(session_info["mount_id"])
        session_info["status"] = self._compute_status(session_info, mount)

        created_at = parse_timestamp(session_info["created_at"])
        elapsed_seconds = int((_now_utc() - created_at).total_seconds())

        result = session_info.copy()
        result["elapsed_seconds"] = elapsed_seconds
        return result

    def destroy(self, session_id: str) -> Dict[str, Any]:
        """Destroy a session while preserving logs."""
        self._refresh_active_command(session_id)
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
        destroyed_at = get_timestamp()
        with state_lock():
            state = self._get_state()
            if session_id not in state["sessions"]:
                raise KeyError(f"Session '{session_id}' not found")
            session_info = state["sessions"][session_id]
            session_info["status"] = "destroyed"
            session_info["destroyed_at"] = destroyed_at
            session_info["logs_preserved"] = True
            session_info["logs_location"] = logs_location
            session_info["busy"] = False
            session_info.pop("active_command", None)
            state["sessions"][session_id] = session_info
            save_state(state)

        return {
            "session_id": session_id,
            "session_name": session_info["session_name"],
            "status": "destroyed",
            "destroyed_at": destroyed_at,
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
