# Experiment Workspaces

Use `runs/` as the staging area for application-layer experiments that use the
installed `seed-runner` CLI.

Rules:

- Create one subdirectory per experiment, for example `runs/exp-web-01/`.
- Put the experiment inputs there as well, typically `docs/*.tex` and `Labsetup/docker-compose.yml`.
- Run `seed-runner` from inside that experiment directory.
- Keep experiment outputs inside that directory, usually under `./artifacts/`.
- Save the final local report under `./report/`.
- Do not modify `seed_runner/`, `tests/`, or other tool source files while
  running an experiment unless the task is specifically to develop SEEDRunner.
- Treat the repository root [`SKILL.md`](../SKILL.md) as the main execution workflow.
  `runs/SKILL.md` is only an auxiliary guide for polishing the final Chinese report.

Typical flow:

```bash
mkdir -p runs/exp-web-01
cd runs/exp-web-01

seed-runner mount create --machine vm-seed-01 --local-dir ./artifacts
seed-runner session create --machine vm-seed-01 --mount-id <mount-id> --name exp-web-01
seed-runner session exec --session <session-id> --cmd "<shell-command>"
seed-runner session destroy --session <session-id>
seed-runner mount destroy --mount-id <mount-id>
```

Suggested per-experiment layout:

```text
runs/
└── exp-web-01/
    ├── notes/
    ├── artifacts/
    ├── docs/
    ├── Labsetup/
    └── report/
```
