# Agents

This document describes the AI agents and their capabilities in the SEEDRunner system.

## Overview

SEEDRunner is designed to work with Claude AI agents (via Claude API or Codex framework). The system provides a minimal, well-defined interface that allows agents to autonomously complete SEED experiments.

## Agent Capabilities

### What Agents Can Do

1. **Understand Experiments** — Parse experiment documentation and extract goals, methods, and acceptance criteria
2. **Plan Execution** — Design a sequence of steps to complete the experiment
3. **Execute Commands** — Run commands on remote VMs via `seed-runner` CLI
4. **Monitor Progress** — Check execution logs and determine if acceptance criteria are met
5. **Iterate on Failure** — Adjust strategy and retry when commands fail
6. **Generate Reports** — Synthesize execution logs into formal experiment reports

### What Agents Cannot Do (By Design)

- Configure SSH, network, or authentication (pre-configured by humans)
- Modify the `seed-runner` tool itself
- Access systems outside the designated experiment VM
- Make decisions that require human judgment (e.g., security implications)

## Integration Points

### 1. CLI Tool

Agents interact with `seed-runner` via command-line interface:

```bash
cd runs/exp-web-01
seed-runner mount create --machine vm-seed-01 --local-dir ./workspace
seed-runner session create --machine vm-seed-01 --mount-id mnt_xxx --name exp-web-01
seed-runner session exec --session sess_xxx --cmd "make"
seed-runner session status --session sess_xxx
```

`--local-dir` is the **full sshfs mount root** (here `./workspace`). The tool reserves only the subdirectory `artifacts/` under that root: command logs live under `artifacts/logs/<session-name>/`, and synced experiment outputs also land under `artifacts/`. Everything else at the mount root (for example `Labsetup/`, scripts, or docs) is for the agent to manage. `metadata.json` is written at the mount root by `seed-runner`.

If you name the mount root `./artifacts` instead, logs become `./artifacts/artifacts/logs/...` (two `artifacts` segments in the path). Using a name like `workspace` avoids that.

### 2. File System

Agents should work inside a dedicated experiment directory under `runs/` and
read execution logs and artifacts from the local mount point in that workspace:

```
./runs/exp-web-01/workspace/          # --local-dir (mount root)
├── metadata.json
├── artifacts/                        # reserved: tool + sync outputs
│   ├── logs/
│   │   └── exp-web-01/
│   │       ├── cmd_001.log
│   │       ├── cmd_002.log
│   │       └── ...
│   ├── code/
│   ├── results/
│   └── ...
└── (everything else: Labsetup, scripts, docs, … — agent-managed)
```

### 3. Skill/Tool Wrapper (Future)

The `seed-runner` CLI can be wrapped as a Claude Code Skill or Tool for seamless integration:

```python
# Pseudo-code for future Skill
@skill("seed-runner")
def run_seed_experiment(machine_id: str, experiment_name: str, commands: List[str]) -> ExperimentResult:
    """Run a SEED experiment autonomously."""
    # Implementation wraps seed-runner CLI
    pass
```

## Agent Workflow

### Typical Experiment Execution Flow

```
1. Agent receives experiment task
   ├─ Input: experiment manual, labsetup, target VM info
   └─ Output: execution plan

2. Agent initializes environment
   ├─ Enter or create workspace: runs/<experiment-name>/
   ├─ Create mount: seed-runner mount create ...
   ├─ Create session: seed-runner session create ...
   └─ Verify connectivity

3. Agent executes experiment steps
   ├─ Loop:
   │  ├─ Execute command: seed-runner session exec ...
   │  ├─ Read logs: cat ./workspace/artifacts/logs/.../cmd_XXX.log
   │  ├─ Check acceptance criteria
   │  └─ If not met, adjust and retry
   └─ Until acceptance criteria met or max retries reached

4. Agent generates report
   ├─ Collect all logs and artifacts
   ├─ Synthesize into formal report without waiting for human supervision
   └─ Save to ./report/

5. Agent cleans up
   ├─ Destroy session: seed-runner session destroy ...
   ├─ Destroy mount: seed-runner mount destroy ...
   └─ Return success/failure status
```

## Design Principles for Agent Integration

### 1. Minimal Interface

The `seed-runner` API is intentionally minimal (4 mount commands + 4 session commands). This reduces cognitive load on agents and makes the system easier to understand and debug.

### 2. Transparent Execution

All execution is logged and traceable. Agents can read logs to understand what happened and why something failed.

### 3. No Hidden State

The system maintains no hidden state. All information is either in the CLI return values or in the log files.

### 4. Fail-Safe Defaults

- Commands that fail don't destroy the session (allows debugging)
- Mounts are separate from sessions (allows reuse)
- Logs are always preserved (allows audit)
- Experiments run inside `runs/<experiment>/` so tool code and experiment outputs stay separated

### 5. Agent Autonomy

Agents are expected to:
- Make decisions about retry strategies
- Interpret acceptance criteria
- Adjust execution plans based on failures
- Generate reports without human input
- Carry the workflow to completion instead of stopping at partial progress updates

## Example: Agent Executing a Web Security Experiment

```python
# Pseudo-code showing how an agent might use seed-runner

def run_web_security_experiment(manual_path, labsetup_path, target_vm):
    os.makedirs("runs/exp-web-01", exist_ok=True)
    os.chdir("runs/exp-web-01")

    # 1. Parse experiment manual
    experiment = parse_manual(manual_path)
    acceptance_criteria = extract_acceptance_criteria(experiment)
    
    # 2. Initialize environment
    mount = run_cmd("seed-runner mount create --machine {} --local-dir ./workspace".format(target_vm))
    session = run_cmd("seed-runner session create --machine {} --mount-id {} --name exp-web-01".format(
        target_vm, mount['mount_id']))
    
    # 3. Execute experiment steps
    for step in experiment.steps:
        result = run_cmd("seed-runner session exec --session {} --cmd '{}'".format(
            session['session_id'], step.command))
        
        # Read logs to check progress
        logs = read_file(result['log_file_local'])
        
        # Check if acceptance criteria are met
        if check_acceptance_criteria(logs, acceptance_criteria):
            break
        
        # If not met, try next step or retry
    
    # 4. Generate report
    all_logs = collect_logs("./workspace/artifacts/logs/exp-web-01/")
    report = generate_report(experiment, all_logs)
    save_report("./report/exp-web-01.zh.md", report)
    
    # 5. Clean up
    run_cmd("seed-runner session destroy --session {}".format(session['session_id']))
    run_cmd("seed-runner mount destroy --mount-id {}".format(mount['mount_id']))
    
    return report
```

## Future Enhancements

### Skill Wrapper

Wrap `seed-runner` as a Claude Code Skill for direct integration:

```bash
/seed-runner mount create --machine vm-seed-01 --local-dir ./workspace
```

### Tool Integration

Expose `seed-runner` as a Claude API Tool for programmatic access.

### Agent Supervision

Add an external supervisor agent that monitors experiment progress and ensures completion.

### Multi-Experiment Orchestration

Support running multiple experiments in sequence or parallel.

## Troubleshooting

### Agent Stuck in Loop

If an agent keeps retrying the same command:
- Check the log file to understand the failure reason
- Manually inspect the remote VM state
- Adjust the agent's retry logic or acceptance criteria

### Incomplete Logs

If logs are missing or incomplete:
- Check disk space on local and remote systems
- Verify sshfs mount is still active
- Check file permissions

### Session Timeout

If a session times out:
- Increase the `--timeout` parameter
- Break the experiment into smaller steps
- Check for long-running background processes

### Partial Completion Without Report

If an agent stops after running commands but before writing the report:
- Treat the task as incomplete
- Resume from the saved logs and artifacts
- Write the report before cleanup, or explicitly explain why the report is blocked

## References

- [API Reference](docs/reference/SEED_RUNNER_API.md)
- [Requirements](REQUIREMENTS.md)
- [Architecture](docs/architecture/)
