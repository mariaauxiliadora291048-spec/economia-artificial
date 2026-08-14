from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SandboxManifest:
    agent_id: str
    filesystem_root: Path
    network_mode: str = "none"
    process_limit: int = 1
    memory_limit_mb: int = 512


class SandboxManager:
    """Sandbox allocation contract; an OS/VM backend can replace this local scaffold.

    The local folder is *not* presented as security isolation. Production use
    requires a VM/container backend that enforces the manifest.
    """

    def __init__(self, base_directory: Path) -> None:
        self._base_directory = base_directory

    def provision(self, agent_id: str, network_mode: str = "none") -> SandboxManifest:
        if network_mode not in {"none", "read_only"}:
            raise ValueError("Only none and read_only network modes are supported")
        filesystem_root = self._base_directory / agent_id
        filesystem_root.mkdir(parents=True, exist_ok=True)
        return SandboxManifest(agent_id, filesystem_root, network_mode=network_mode)
