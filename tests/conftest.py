"""
Pytest configuration and fixtures.
"""

import os
import pytest
import tempfile
import shutil


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    temp_path = tempfile.mkdtemp()
    yield temp_path
    # Cleanup
    if os.path.exists(temp_path):
        shutil.rmtree(temp_path)


@pytest.fixture
def env_file(temp_dir):
    """Create a test .env.machines file."""
    key_path = os.path.join(temp_dir, "id_test")
    with open(key_path, 'w') as f:
        f.write("test-key")

    env_path = os.path.join(temp_dir, ".env.machines")
    with open(env_path, 'w') as f:
        f.write(
            f"""SEED_RUNNER_LOCAL_HOST=10.0.0.5
SEED_RUNNER_LOCAL_SSH_PORT=2200
SEED_RUNNER_LOCAL_USER=ely
SEED_RUNNER_REMOTE_TO_LOCAL_KEY=~/.ssh/id_ed25519
MACHINE_vm-seed-01_HOST=localhost
MACHINE_vm-seed-01_PORT=2222
MACHINE_vm-seed-01_USER=seed
MACHINE_vm-seed-01_KEY={key_path}
"""
        )
    return env_path
