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
- Read the chosen experiment materials under `runs/<experiment-name>/`, especially `docs/*.tex` and `Labsetup/docker-compose.yml`.
- Read `runs/SKILL.md` only when drafting or polishing the final Chinese report.

## Preconditions

- Assume `.env.machines` is already correct.
- Assume SSH, remote sshfs, tmux, and keys are preconfigured by humans.
- Do not reconfigure SSH, keys, ports, tmux, or sshfs unless the user explicitly asks you to debug the platform.
- Prefer `seed-runner` CLI over raw `ssh`, `tmux`, or `sshfs`.
- Unless the user says otherwise, create and use a dedicated workspace under `runs/<experiment-name>/` and do not run experiments from the repository root.
- Treat this skill as a complete end-to-end workflow: understand the lab, execute it, verify the result, write the report, and clean up in one pass.
- Do not assume a human is supervising between steps or will manually stitch together partial outputs for you.

## Standard Workflow

1. Choose the target experiment directory under `runs/`, for example `runs/ARP_Attack/` or `runs/Sniffing_Spoofing/`.
2. Read the experiment manual and lab setup files in that directory before issuing commands.
3. Create a local shared directory inside that workspace, usually `./artifacts` for the task.
4. Create a mount:

```bash
seed-runner mount create --machine <machine-id> --local-dir ./artifacts
```

5. Create a session with a descriptive name:

```bash
seed-runner session create --machine <machine-id> --mount-id <mount-id> --name <session-name>
```

6. Execute commands through the session:

```bash
seed-runner session exec --session <session-id> --cmd "<shell-command>"
```

7. After each command:
- inspect the returned `exit_code`
- read `log_file_local`
- inspect files under `<local-dir>/artifacts/` when the command is expected to create outputs

8. Use `seed-runner session status --session <session-id>` when you need current status, command count, or the last command context.
9. Save a complete Chinese report under `./report/`, grounded in logs and artifacts from the run.
10. When the task is complete, destroy the session and then destroy the mount.

## Command Discipline

- Send complete shell commands, not partial fragments.
- Prefer non-interactive commands.
- Use relative paths inside the remote work directory unless there is a clear reason not to.
- If a task is long-running, set a larger `--timeout` instead of assuming the default is enough.
- If you need durable evidence, write it under `artifacts/` so it lands in the local shared directory.
- Keep notes, reports, and intermediate files inside the current `runs/<experiment-name>/` workspace so the tool repository stays clean.
- A successful run is not just command execution. It must end with a usable report that a human can read without replaying the entire session.

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
- a Chinese report has been written under `./report/`
- session and mount cleanup has been performed, unless the user asked to keep them for inspection

In the final report, include:
- `mount_id`
- `session_id`
- the key command or commands run
- relevant `log_file_local` paths
- important artifact paths
- final success or failure status
- whether the acceptance criteria were met, partially met, or blocked

## Practical Pattern

For a typical experiment loop:

1. create mount
2. create session
3. run one command
4. inspect `log_file_local`
5. inspect generated artifacts
6. decide whether to continue, retry, or conclude
7. write the report under `./report/`
8. clean up

Keep the loop tight. Do not queue many blind commands before reading the evidence from the previous one.

## Notes

- `seed-runner` already handles the remote working directory and log placement. Use the returned paths instead of guessing.
- Logs are the primary source of truth for command behavior.
- Artifacts in the local shared directory are the primary source of truth for produced files.
- `runs/SKILL.md` is an auxiliary writing guide for report polish, not the primary execution workflow.
