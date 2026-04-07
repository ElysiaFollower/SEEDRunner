"""
Tests for configuration management.
"""

import pytest
import os
from seed_runner.config import ConfigManager, MachineConfig


def test_machine_config_validation(tmp_path):
    """Test MachineConfig validation."""
    key_path = tmp_path / "id_test"
    key_path.write_text("test-key")

    # Valid config
    config = MachineConfig(
        machine_id="test",
        host="localhost",
        port=2222,
        user="seed",
        key_path=str(key_path)
    )
    assert config.validate()

    # Missing host
    with pytest.raises(ValueError, match="HOST not configured"):
        config = MachineConfig(
            machine_id="test",
            host="",
            port=2222,
            user="seed",
            key_path=str(key_path)
        )
        config.validate()

    # Missing user
    with pytest.raises(ValueError, match="USER not configured"):
        config = MachineConfig(
            machine_id="test",
            host="localhost",
            port=2222,
            user="",
            key_path=str(key_path)
        )
        config.validate()


def test_config_manager_load(env_file, monkeypatch):
    """Test ConfigManager loading configuration."""
    monkeypatch.delenv("SEED_RUNNER_LOCAL_HOST", raising=False)
    monkeypatch.delenv("SEED_RUNNER_LOCAL_SSH_PORT", raising=False)
    monkeypatch.delenv("SEED_RUNNER_LOCAL_USER", raising=False)
    monkeypatch.delenv("SEED_RUNNER_REMOTE_TO_LOCAL_KEY", raising=False)

    config_manager = ConfigManager(env_file)

    # Check that machine was loaded
    assert config_manager.has_machine("vm-seed-01")

    # Get machine config
    machine = config_manager.get_machine("vm-seed-01")
    assert machine.machine_id == "vm-seed-01"
    assert machine.host == "localhost"
    assert machine.port == 2222
    assert machine.user == "seed"
    assert os.environ["SEED_RUNNER_LOCAL_HOST"] == "10.0.0.5"
    assert os.environ["SEED_RUNNER_LOCAL_SSH_PORT"] == "2200"


def test_config_manager_preserves_existing_runtime_env(env_file, monkeypatch):
    """Existing process environment should take precedence over file defaults."""
    monkeypatch.setenv("SEED_RUNNER_LOCAL_HOST", "10.1.2.3")

    config_manager = ConfigManager(env_file)

    assert config_manager.has_machine("vm-seed-01")
    assert os.environ["SEED_RUNNER_LOCAL_HOST"] == "10.1.2.3"


def test_config_manager_missing_machine(env_file):
    """Test ConfigManager with missing machine."""
    config_manager = ConfigManager(env_file)

    with pytest.raises(KeyError, match="not found"):
        config_manager.get_machine("nonexistent")


def test_config_manager_list_machines(env_file):
    """Test listing all machines."""
    config_manager = ConfigManager(env_file)
    machines = config_manager.list_machines()

    assert "vm-seed-01" in machines
    assert len(machines) >= 1
