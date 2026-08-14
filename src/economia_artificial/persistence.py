from __future__ import annotations

import json
from pathlib import Path

from economia_artificial.memory import MemoryStore
from economia_artificial.research import ResearchClient
from economia_artificial.world import EconomyWorld


class JsonWorldStore:
    """Durable local state for the development server.

    The store is deliberately JSON and local for this milestone. Its payload
    mirrors the PostgreSQL schema so a repository adapter can replace it later.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(
        self,
        research: ResearchClient | None = None,
        memory_store: MemoryStore | None = None,
    ) -> EconomyWorld | None:
        if not self._path.exists():
            return None
        snapshot = json.loads(self._path.read_text(encoding="utf-8"))
        return EconomyWorld.from_snapshot(snapshot, research, memory_store)

    def save(self, world: EconomyWorld) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self._path.with_suffix(".tmp")
        payload = json.dumps(world.snapshot(), ensure_ascii=False, indent=2)
        temporary_path.write_text(payload, encoding="utf-8")
        temporary_path.replace(self._path)
