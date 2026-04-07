"""
CLI entry point for seed-runner.

Provides command-line interface for mount and session management.
"""

import sys
import json
import os
import argparse
from typing import Any, Dict

from seed_runner.mount import get_mount_manager
from seed_runner.session import get_session_manager
from seed_runner.utils import json_response


def handle_error(error: Exception, error_code: int) -> None:
    """Handle and print error message."""
    error_info = {
        "error": str(error),
        "error_code": error_code,
    }
    print(json_response(error_info), file=sys.stderr)
    sys.exit(1)


def cmd_mount_create(args: argparse.Namespace) -> None:
    """Handle 'mount create' command."""
    try:
        mount_manager = get_mount_manager()
        result = mount_manager.create(
            machine_id=args.machine,
            local_dir=args.local_dir,
            remote_dir=args.remote_dir,
            timeout=args.timeout,
        )
        print(json_response(result))
    except Exception as e:
        handle_error(e, 2001)


def cmd_mount_status(args: argparse.Namespace) -> None:
    """Handle 'mount status' command."""
    try:
        mount_manager = get_mount_manager()
        result = mount_manager.status(args.mount_id)
        print(json_response(result))
    except KeyError as e:
        handle_error(e, 2004)
    except Exception as e:
        handle_error(e, 2001)


def cmd_mount_destroy(args: argparse.Namespace) -> None:
    """Handle 'mount destroy' command."""
    try:
        mount_manager = get_mount_manager()
        result = mount_manager.destroy(
            mount_id=args.mount_id,
            cleanup=args.cleanup,
        )
        print(json_response(result))
    except KeyError as e:
        handle_error(e, 2004)
    except Exception as e:
        handle_error(e, 2009)


def cmd_session_create(args: argparse.Namespace) -> None:
    """Handle 'session create' command."""
    try:
        session_manager = get_session_manager()
        result = session_manager.create(
            machine_id=args.machine,
            mount_id=args.mount_id,
            session_name=args.name,
            timeout=args.timeout,
        )
        print(json_response(result))
    except KeyError as e:
        handle_error(e, 2004)
    except Exception as e:
        handle_error(e, 2003)


def cmd_session_exec(args: argparse.Namespace) -> None:
    """Handle 'session exec' command."""
    try:
        session_manager = get_session_manager()

        # Execute command
        result = session_manager.exec(
            session_id=args.session,
            cmd=args.cmd,
            timeout=args.timeout,
        )
        print(json_response(result))
    except KeyError as e:
        handle_error(e, 2005)
    except Exception as e:
        handle_error(e, 2007)


def cmd_session_status(args: argparse.Namespace) -> None:
    """Handle 'session status' command."""
    try:
        session_manager = get_session_manager()
        result = session_manager.status(args.session)
        print(json_response(result))
    except KeyError as e:
        handle_error(e, 2005)
    except Exception as e:
        handle_error(e, 2005)


def cmd_session_destroy(args: argparse.Namespace) -> None:
    """Handle 'session destroy' command."""
    try:
        session_manager = get_session_manager()
        result = session_manager.destroy(args.session)
        print(json_response(result))
    except KeyError as e:
        handle_error(e, 2005)
    except Exception as e:
        handle_error(e, 2005)


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="seed-runner: Autonomous SEED experiment runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Mount commands
    mount_parser = subparsers.add_parser("mount", help="Mount management")
    mount_subparsers = mount_parser.add_subparsers(dest="mount_command")

    # mount create
    mount_create = mount_subparsers.add_parser("create", help="Create a new mount")
    mount_create.add_argument("--machine", required=True, help="Target machine ID")
    mount_create.add_argument("--local-dir", required=True, help="Local mount point")
    mount_create.add_argument("--remote-dir", default=None, help="Remote experiment directory")
    mount_create.add_argument("--timeout", type=int, default=30, help="Mount timeout in seconds")
    mount_create.set_defaults(func=cmd_mount_create)

    # mount status
    mount_status = mount_subparsers.add_parser("status", help="Get mount status")
    mount_status.add_argument("--mount-id", required=True, help="Mount ID")
    mount_status.set_defaults(func=cmd_mount_status)

    # mount destroy
    mount_destroy = mount_subparsers.add_parser("destroy", help="Destroy a mount")
    mount_destroy.add_argument("--mount-id", required=True, help="Mount ID")
    mount_destroy.add_argument("--cleanup", action="store_true", help="Clean up remote directory")
    mount_destroy.set_defaults(func=cmd_mount_destroy)

    # Session commands
    session_parser = subparsers.add_parser("session", help="Session management")
    session_subparsers = session_parser.add_subparsers(dest="session_command")

    # session create
    session_create = session_subparsers.add_parser("create", help="Create a new session")
    session_create.add_argument("--machine", required=True, help="Target machine ID")
    session_create.add_argument("--mount-id", required=True, help="Mount ID")
    session_create.add_argument("--name", required=True, help="Session name")
    session_create.add_argument("--timeout", type=int, default=3600, help="Session timeout in seconds")
    session_create.set_defaults(func=cmd_session_create)

    # session exec
    session_exec = session_subparsers.add_parser("exec", help="Execute a command")
    session_exec.add_argument("--session", required=True, help="Session ID")
    session_exec.add_argument("--cmd", required=True, help="Command to execute")
    session_exec.add_argument("--timeout", type=int, default=300, help="Command timeout in seconds")
    session_exec.set_defaults(func=cmd_session_exec)

    # session status
    session_status = session_subparsers.add_parser("status", help="Get session status")
    session_status.add_argument("--session", required=True, help="Session ID")
    session_status.set_defaults(func=cmd_session_status)

    # session destroy
    session_destroy = session_subparsers.add_parser("destroy", help="Destroy a session")
    session_destroy.add_argument("--session", required=True, help="Session ID")
    session_destroy.set_defaults(func=cmd_session_destroy)

    # Parse arguments
    args = parser.parse_args()

    # Execute command
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
