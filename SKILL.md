---
name: seed-runner
description: Use when a task should be executed inside a preconfigured remote VM through the seed-runner CLI, especially for SEED experiments that require running shell commands, inspecting logs and artifacts from the local shared directory, iterating on failures, and cleaning up remote sessions without using raw ssh, tmux, or sshfs directly.
metadata:
  short-description: Run remote VM tasks through seed-runner
---

# Seed Runner

Use this skill when the work must happen on a remote VM and the environment has already been prepared for `seed-runner`.

Do not use this skill to modify or debug the `seed-runner` implementation itself. This skill is for application-layer execution on top of the tool.

## Read First

- Read `docs/reference/SEED_RUNNER_API.md` for exact command forms and JSON fields.
- Read `AGENTS.md` for system boundaries and agent expectations.

## Preconditions

- Assume `.env.machines` is already correct.
- Assume SSH, remote sshfs, tmux, and keys are preconfigured by humans.
- Do not reconfigure SSH, keys, ports, tmux, or sshfs unless the user explicitly asks you to debug the platform.
- Prefer `seed-runner` CLI over raw `ssh`, `tmux`, or `sshfs`.

## Standard Workflow

1. Create a local shared directory, usually `./artifacts` for the task.
2. Create a mount:

```bash
seed-runner mount create --machine <machine-id> --local-dir ./artifacts
```

3. Create a session with a descriptive name:

```bash
seed-runner session create --machine <machine-id> --mount-id <mount-id> --name <session-name>
```

4. Execute commands through the session:

```bash
seed-runner session exec --session <session-id> --cmd "<shell-command>"
```

5. After each command:
- inspect the returned `exit_code`
- read `log_file_local`
- inspect files under `<local-dir>/artifacts/` when the command is expected to create outputs

6. Use `seed-runner session status --session <session-id>` when you need current status, command count, or the last command context.
7. When the task is complete, destroy the session and then destroy the mount.

## Command Discipline

- Send complete shell commands, not partial fragments.
- Prefer non-interactive commands.
- Use relative paths inside the remote work directory unless there is a clear reason not to.
- If a task is long-running, set a larger `--timeout` instead of assuming the default is enough.
- If you need durable evidence, write it under `artifacts/` so it lands in the local shared directory.

## Failure Handling

- If `mount create` fails, treat it as an environment blocker. Surface the exact JSON error and stop. Do not silently fall back to raw SSH.
- If `session create` fails, report the exact error and stop.
- If `seed-runner session exec` returns a non-zero `exit_code`, this is usually a task-level failure, not a platform failure. Read the log, diagnose, and retry strategically.
- If a command times out, inspect the partial log first. Then either increase `--timeout`, split the work into smaller commands, or report the blocker.
- If a session is gone or a mount is unmounted, recreate what you need instead of assuming hidden state still exists.

## Completion Rules

A task using this skill is complete only when:
- the requested remote action has been carried out
- the result is supported by logs or artifacts
- session and mount cleanup has been performed, unless the user asked to keep them for inspection

In the final report, include:
- `mount_id`
- `session_id`
- the key command or commands run
- relevant `log_file_local` paths
- important artifact paths
- final success or failure status

## Practical Pattern

For a typical experiment loop:

1. create mount
2. create session
3. run one command
4. inspect `log_file_local`
5. inspect generated artifacts
6. decide whether to continue, retry, or conclude
7. clean up

Keep the loop tight. Do not queue many blind commands before reading the evidence from the previous one.

## Notes

- `seed-runner` already handles the remote working directory and log placement. Use the returned paths instead of guessing.
- Logs are the primary source of truth for command behavior.
- Artifacts in the local shared directory are the primary source of truth for produced files.
