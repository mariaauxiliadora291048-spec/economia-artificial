from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from economia_artificial.domain import utc_now


@dataclass(frozen=True, slots=True)
class Memory:
    agent_id: str
    kind: str
    content: str
    metadata: dict[str, Any]
    salience: float
    created_at: datetime


class MemoryStore(Protocol):
    def record(
        self,
        agent_id: str,
        kind: str,
        content: str,
        metadata: dict[str, Any],
        salience: float = 0.5,
    ) -> Memory: ...

    def relevant(self, agent_id: str, limit: int = 8) -> list[Memory]: ...


class InMemoryMemoryStore:
    def __init__(self) -> None:
        self._memories: list[Memory] = []

    def record(
        self,
        agent_id: str,
        kind: str,
        content: str,
        metadata: dict[str, Any],
        salience: float = 0.5,
    ) -> Memory:
        memory = Memory(agent_id, kind, content, metadata, salience, utc_now())
        self._memories.append(memory)
        return memory

    def relevant(self, agent_id: str, limit: int = 8) -> list[Memory]:
        agent_memories = [memory for memory in self._memories if memory.agent_id == agent_id]
        return sorted(
            agent_memories,
            key=lambda memory: (memory.salience, memory.created_at),
            reverse=True,
        )[:limit]


class JsonMemoryStore(InMemoryMemoryStore):
    """Small persistent reference store; replace with PostgreSQL in deployment."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path
        self._load()

    def record(
        self,
        agent_id: str,
        kind: str,
        content: str,
        metadata: dict[str, Any],
        salience: float = 0.5,
    ) -> Memory:
        memory = super().record(agent_id, kind, content, metadata, salience)
        self._persist()
        return memory

    def _load(self) -> None:
        if not self._path.exists():
            return
        raw_memories = json.loads(self._path.read_text(encoding="utf-8"))
        self._memories = [
            Memory(
                agent_id=raw["agent_id"],
                kind=raw["kind"],
                content=raw["content"],
                metadata=raw["metadata"],
                salience=raw["salience"],
                created_at=datetime.fromisoformat(raw["created_at"]),
            )
            for raw in raw_memories
        ]

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        serialized = []
        for memory in self._memories:
            raw = asdict(memory)
            raw["created_at"] = memory.created_at.isoformat()
            serialized.append(raw)
        temporary_path = self._path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(serialized, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary_path.replace(self._path)
