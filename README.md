# SEEDRunner

Autonomous SEED experiment runner powered by AI agents.

## Overview

SEEDRunner is a system that enables AI agents to autonomously complete SEED (Security Experimentation and Evaluation) lab experiments and generate formal reports.

**Key Features**:
- Autonomous experiment execution without human intervention
- Real-time remote command execution via SSH + tmux
- Complete execution tracing and audit logs
- Automatic experiment report generation
- Modular architecture for extensibility

## Quick Start

### Prerequisites

- Python 3.8+
- SSH access to target VM with public key authentication
- `tmux` and `sshfs` installed on target VM

### Installation

```bash
# Clone the repository
git clone <repo-url>
cd SEEDRunner

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
python3 -m pip install -e ".[dev]"
```

### Configuration

```bash
# Copy the example configuration
cp .env.machines.example .env.machines

# Edit .env.machines with your machine details and local SSH info
# SEED_RUNNER_LOCAL_HOST=<LOCAL_IP_REACHABLE_FROM_VM>
# SEED_RUNNER_LOCAL_SSH_PORT=<LOCAL_SSH_PORT>
# MACHINE_vm-seed-01_HOST=<IP>
# MACHINE_vm-seed-01_PORT=<SSH_PORT>
# MACHINE_vm-seed-01_USER=<USER>
# MACHINE_vm-seed-01_KEY=<PATH_TO_KEY>
```

`seed-runner` now mounts the local shared directory on the remote VM via `sshfs`.
That means the target VM must be able to SSH back to your local machine using
`SEED_RUNNER_LOCAL_HOST` and `SEED_RUNNER_LOCAL_SSH_PORT`. If the return path
needs a different local username or key path, set `SEED_RUNNER_LOCAL_USER` and
`SEED_RUNNER_REMOTE_TO_LOCAL_KEY` in `.env.machines` as well.

### Basic Usage

For application-layer experiments, create a dedicated workspace under `runs/`
and invoke `seed-runner` from there instead of using the repository root as the
working directory:

```bash
mkdir -p runs/exp-web-01
cd runs/exp-web-01
```

```bash
# Create a mount
seed-runner mount create \
  --machine vm-seed-01 \
  --local-dir ./artifacts

# Create a session
seed-runner session create \
  --machine vm-seed-01 \
  --mount-id mnt_20260407_001 \
  --name exp-web-01

# Execute a command
seed-runner session exec \
  --session sess_20260407_001 \
  --cmd "make"

# Check session status
seed-runner session status --session sess_20260407_001

# Destroy session
seed-runner session destroy --session sess_20260407_001

# Destroy mount
seed-runner mount destroy --mount-id mnt_20260407_001
```

This keeps experiment outputs under `runs/<experiment>/artifacts/` and avoids
mixing lab results with SEEDRunner source files. See [`runs/README.md`](runs/README.md)
for the expected workspace layout.

## Documentation

- [API Reference](docs/reference/SEED_RUNNER_API.md) — Complete API specification
- [Requirements](REQUIREMENTS.md) — Project requirements and constraints
- [Architecture](docs/architecture/) — System design and architecture decisions

## Project Structure

```
SEEDRunner/
├── seed_runner/           # Main package
│   ├── __init__.py
│   ├── cli.py            # CLI entry point
│   ├── config.py         # Configuration management
│   ├── mount.py          # Mount management
│   ├── session.py        # Session management
│   └── utils.py          # Utilities
├── tests/                # Test suite
├── docs/                 # Documentation
├── runs/                 # Per-experiment workspaces for application-layer use
├── plans/                # Project plans
├── evals/                # Evaluation samples
├── pyproject.toml        # Project metadata
├── requirements.txt      # Dependencies
├── .env.machines.example # Configuration template
└── README.md            # This file
```

## Development

### Running Tests

```bash
python3 -m pytest tests/
```

To run the real VM-backed integration test that exercises the full CLI workflow
across separate processes:

```bash
SEED_RUNNER_RUN_REAL_VM_TESTS=1 python3 -m pytest tests/test_real_vm_integration.py -q
```

### Code Style

```bash
black seed_runner/
flake8 seed_runner/
mypy seed_runner/
```

## License

MIT

## Contact

For questions or issues, please open an issue on GitHub.
