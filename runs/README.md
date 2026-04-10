# Experiment Workspaces

Use `runs/` as the staging area for application-layer experiments that use the
installed `seed-runner` CLI.

Rules:

- Create one subdirectory per experiment, for example `runs/exp-web-01/`.
- Choose a **mount root** directory inside that experiment (this is `--local-dir`), for example `./workspace`. Under it, only `artifacts/` is reserved by the tool (command logs under `artifacts/logs/<session-name>/`, plus synced outputs). Everything else at the mount root is for you (for example `Labsetup/`, extra scripts). `metadata.json` is written at the mount root.
- Put materials that must appear on the VM under that mount root (for example copy or symlink `docs/` and `Labsetup/` into `./workspace/` when the lab expects them there).
- Run `seed-runner` from inside the experiment directory (paths in examples are relative to `runs/<experiment>/`).
- Keep durable outputs from the remote run under `./workspace/artifacts/` after sync; the final human-facing report still goes under `./report/`.
- Do not modify `seed_runner/`, `tests/`, or other tool source files while
  running an experiment unless the task is specifically to develop SEEDRunner.
- Treat the repository root [`SKILL.md`](../SKILL.md) as the main execution workflow.
  `runs/SKILL.md` is only an auxiliary guide for polishing the final Chinese report.

Typical flow:

```bash
mkdir -p runs/exp-web-01
cd runs/exp-web-01

seed-runner mount create --machine vm-seed-01 --local-dir ./workspace
seed-runner session create --machine vm-seed-01 --mount-id <mount-id> --name exp-web-01
seed-runner session exec --session <session-id> --cmd "<shell-command>"
seed-runner session destroy --session <session-id>
seed-runner mount destroy --mount-id <mount-id>
```

If the default remote mount point conflicts with another live experiment, retry
mount creation with a unique remote directory, for example:

```bash
seed-runner mount create \
  --machine vm-seed-01 \
  --local-dir ./workspace \
  --remote-dir /home/seed/seed-experiments/exp-web-01
```

Suggested per-experiment layout:

```text
runs/
└── exp-web-01/
    ├── workspace/                 # --local-dir (mount root)
    │   ├── metadata.json
    │   ├── artifacts/           # reserved (logs/, synced outputs)
    │   ├── Labsetup/            # example: synced inputs for the VM
    │   └── docs/                # optional: if you keep manuals inside the mount
    ├── notes/                   # optional: local-only notes
    └── report/                  # final Chinese report
```

If you name the mount root `./artifacts` instead of `./workspace`, command logs
become `./artifacts/artifacts/logs/...` (two `artifacts` segments). Prefer
`workspace` unless you intentionally accept that path shape.
