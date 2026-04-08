"""
Configuration management for seed-runner.

Loads machine configurations from .env.machines file and provides
a unified interface for accessing SSH connection details.
"""

import os
from pathlib import Path
from typing import Dict, Optional
from dataclasses import dataclass


@dataclass
class MachineConfig:
    """SSH connection configuration for a target machine."""
    machine_id: str
    host: str
    port: int
    user: str
    key_path: str

    def validate(self) -> bool:
        """Validate that all required fields are present and valid."""
        if not self.host:
            raise ValueError(f"Machine {self.machine_id}: HOST not configured")
        if not self.user:
            raise ValueError(f"Machine {self.machine_id}: USER not configured")
        if not self.key_path:
            raise ValueError(f"Machine {self.machine_id}: KEY not configured")

        # Expand ~ in key path
        expanded_key_path = os.path.expanduser(self.key_path)
        if not os.path.exists(expanded_key_path):
            raise ValueError(f"Machine {self.machine_id}: KEY file not found: {self.key_path}")
        return True


class ConfigManager:
    """Manages machine configurations from .env.machines file."""

    def __init__(self, env_file: str = ".env.machines"):
        """
        Initialize ConfigManager.

        Args:
            env_file: Path to .env.machines file
        """
        self.env_file = resolve_env_file(env_file)
        self.machines: Dict[str, MachineConfig] = {}
        self._load_config()

    def _load_config(self) -> None:
        """Load configuration from .env.machines file."""
        if not os.path.exists(self.env_file):
            raise FileNotFoundError(
                f"Configuration file not found: {self.env_file}\n"
                f"Please copy .env.machines.example to .env.machines and fill in your values."
            )

        file_values = self._parse_env_file()
        for key, value in file_values.items():
            if key.startswith("MACHINE_"):
                continue
            os.environ.setdefault(key, value)

        values = file_values.copy()
        values.update(os.environ)

        # Parse environment variables
        machine_ids = set()
        for key in values:
            if key.startswith("MACHINE_"):
                parts = key.split("_")
                if len(parts) >= 3:
                    machine_id = "_".join(parts[1:-1])
                    machine_ids.add(machine_id)

        # Load each machine's configuration
        for machine_id in machine_ids:
            host = values.get(f"MACHINE_{machine_id}_HOST")
            port_str = values.get(f"MACHINE_{machine_id}_PORT", "22")
            user = values.get(f"MACHINE_{machine_id}_USER")
            key_path = values.get(f"MACHINE_{machine_id}_KEY")

            try:
                port = int(port_str)
            except ValueError:
                raise ValueError(f"Machine {machine_id}: PORT must be an integer, got {port_str}")

            config = MachineConfig(
                machine_id=machine_id,
                host=host or "",
                port=port,
                user=user or "",
                key_path=key_path or ""
            )

            try:
                config.validate()
                self.machines[machine_id] = config
            except ValueError as e:
                raise ValueError(str(e))

    def _parse_env_file(self) -> Dict[str, str]:
        """Parse a simple KEY=VALUE env file without external dependencies."""
        values: Dict[str, str] = {}
        with open(self.env_file, "r") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                    value = value[1:-1]
                values[key] = os.path.expandvars(value)
        return values

    def get_machine(self, machine_id: str) -> MachineConfig:
        """
        Get configuration for a specific machine.

        Args:
            machine_id: Machine identifier

        Returns:
            MachineConfig object

        Raises:
            KeyError: If machine_id not found
        """
        if machine_id not in self.machines:
            available = ", ".join(self.machines.keys())
            raise KeyError(
                f"Machine '{machine_id}' not found in configuration.\n"
                f"Available machines: {available}"
            )
        return self.machines[machine_id]

    def list_machines(self) -> Dict[str, MachineConfig]:
        """Get all configured machines."""
        return self.machines.copy()

    def has_machine(self, machine_id: str) -> bool:
        """Check if a machine is configured."""
        return machine_id in self.machines


def _get_project_root() -> Path:
    """Return the repository root when running from the editable project."""
    return Path(__file__).resolve().parent.parent


def resolve_env_file(env_file: str = ".env.machines") -> str:
    """Resolve the default config file from workspace or project root."""
    requested = Path(os.path.expanduser(env_file))
    if requested.is_absolute():
        return str(requested)

    cwd = Path.cwd().resolve()
    for parent in (cwd, *cwd.parents):
        candidate = parent / requested
        if candidate.exists():
            return str(candidate)

    project_candidate = _get_project_root() / requested
    return str(project_candidate)


# Global config manager instance
_config_manager: Optional[ConfigManager] = None


def get_config_manager(env_file: str = ".env.machines") -> ConfigManager:
    """Get or create the global ConfigManager instance."""
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager(env_file)
    return _config_manager


def get_machine_config(machine_id: str) -> MachineConfig:
    """Convenience function to get machine config."""
    return get_config_manager().get_machine(machine_id)
