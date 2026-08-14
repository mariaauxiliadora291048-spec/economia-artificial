from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from economia_artificial.runtime import DecisionProvider
from economia_artificial.world import EconomyWorld


def utc_now() -> datetime:
    return datetime.now(UTC)


class AgentLifecycle(StrEnum):
    CREATED = "created"
    STARTING = "starting"
    ACTIVE = "active"
    PAUSED = "paused"
    WAITING = "waiting"
    ERROR = "error"
    SUSPENDED = "suspended"
    BANKRUPT = "bankrupt"
    TERMINATED = "terminated"


@dataclass(slots=True)
class AgentRuntimeState:
    agent_id: str
    provider_id: str
    lifecycle: AgentLifecycle = AgentLifecycle.CREATED
    next_wake_at: datetime | None = None
    priority: int = 0
    last_action: str | None = None
    last_error: str | None = None
    crash_count: int = 0
    cycles_completed: int = 0


class ProviderResolver(Protocol):
    def __call__(self, provider_id: str) -> DecisionProvider: ...


class JsonRuntimeStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> dict[str, AgentRuntimeState]:
        if not self._path.exists():
            return {}
        raw_states = json.loads(self._path.read_text(encoding="utf-8"))
        return {
            raw["agent_id"]: AgentRuntimeState(
                agent_id=raw["agent_id"],
                provider_id=raw["provider_id"],
                lifecycle=AgentLifecycle(raw["lifecycle"]),
                next_wake_at=datetime.fromisoformat(raw["next_wake_at"])
                if raw["next_wake_at"]
                else None,
                priority=int(raw["priority"]),
                last_action=raw["last_action"],
                last_error=raw["last_error"],
                crash_count=int(raw["crash_count"]),
                cycles_completed=int(raw["cycles_completed"]),
            )
            for raw in raw_states
        }

    def save(self, states: dict[str, AgentRuntimeState]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        serialized = []
        for state in states.values():
            raw = asdict(state)
            raw["lifecycle"] = state.lifecycle.value
            raw["next_wake_at"] = state.next_wake_at.isoformat() if state.next_wake_at else None
            serialized.append(raw)
        temporary_path = self._path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(serialized, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary_path.replace(self._path)


class AgentScheduler:
    """One lightweight worker loop schedules a durable population of agents."""

    def __init__(
        self,
        world: EconomyWorld,
        resolve_provider: ProviderResolver,
        store: JsonRuntimeStore,
        on_world_changed: Callable[[], None],
        cycle_interval_seconds: float = 30.0,
    ) -> None:
        self._world = world
        self._resolve_provider = resolve_provider
        self._store = store
        self._on_world_changed = on_world_changed
        self._cycle_interval_seconds = cycle_interval_seconds
        self._states = store.load()
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._world.on_agent_created(self._start_child)

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run, name="economy-agent-scheduler", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def states(self) -> list[AgentRuntimeState]:
        with self._lock:
            return sorted(self._states.values(), key=lambda state: (state.priority, state.agent_id))

    def register_agent(self, agent_id: str, provider_id: str) -> AgentRuntimeState:
        if agent_id not in self._world.agents:
            raise ValueError("Unknown agent")
        with self._lock:
            self._world.grant_provider(agent_id, provider_id)
            state = self._states.get(agent_id)
            if state is None:
                state = AgentRuntimeState(agent_id=agent_id, provider_id=provider_id)
                self._states[agent_id] = state
            else:
                state.provider_id = provider_id
            self._persist()
            return state

    def start_agent(self, agent_id: str) -> AgentRuntimeState:
        with self._lock:
            state = self._require_state(agent_id)
            state.lifecycle = AgentLifecycle.STARTING
            state.next_wake_at = utc_now()
            state.last_error = None
            self._persist()
            return state

    def pause_agent(self, agent_id: str) -> AgentRuntimeState:
        with self._lock:
            state = self._require_state(agent_id)
            state.lifecycle = AgentLifecycle.PAUSED
            state.next_wake_at = None
            self._persist()
            return state

    def resume_agent(self, agent_id: str) -> AgentRuntimeState:
        return self.start_agent(agent_id)

    def restart_agent(self, agent_id: str) -> AgentRuntimeState:
        with self._lock:
            state = self._require_state(agent_id)
            state.crash_count = 0
            state.last_error = None
            state.lifecycle = AgentLifecycle.STARTING
            state.next_wake_at = utc_now()
            self._persist()
            return state

    def _start_child(self, parent_agent_id: str, child_agent_id: str) -> None:
        with self._lock:
            parent_state = self._states.get(parent_agent_id)
            if parent_state is None:
                return
            self._world.grant_provider(child_agent_id, parent_state.provider_id)
            self._states[child_agent_id] = AgentRuntimeState(
                agent_id=child_agent_id,
                provider_id=parent_state.provider_id,
                lifecycle=AgentLifecycle.STARTING,
                next_wake_at=utc_now(),
            )
            self._persist()

    def _run(self) -> None:
        while not self._stop_event.wait(0.2):
            due_agent_ids = self._due_agents()
            for agent_id in due_agent_ids:
                self._run_agent_cycle(agent_id)

    def _due_agents(self) -> list[str]:
        now = utc_now()
        with self._lock:
            return [
                state.agent_id
                for state in self._states.values()
                if state.lifecycle
                in {AgentLifecycle.STARTING, AgentLifecycle.WAITING, AgentLifecycle.ERROR}
                and state.next_wake_at is not None
                and state.next_wake_at <= now
            ]

    def _run_agent_cycle(self, agent_id: str) -> None:
        with self._lock:
            state = self._require_state(agent_id)
            state.lifecycle = AgentLifecycle.ACTIVE
            self._persist()
        try:
            if not self._world.policy.provider_is_granted(agent_id, state.provider_id):
                raise RuntimeError("PROVIDER_NOT_GRANTED")
            provider = self._resolve_provider(state.provider_id)
            observations = self._world.run_autonomous_cycle(agent_id, provider)
            self._world.advance_market()
            with self._lock:
                state.lifecycle = AgentLifecycle.WAITING
                state.next_wake_at = utc_now() + timedelta(seconds=self._cycle_interval_seconds)
                state.cycles_completed += 1
                state.last_action = observations[-1]["tool"] if observations else "reflection"
                state.last_error = None
                self._persist()
        except Exception as exc:
            with self._lock:
                state.crash_count += 1
                state.last_error = str(exc)[:500]
                if state.crash_count >= 3:
                    state.lifecycle = AgentLifecycle.SUSPENDED
                    state.next_wake_at = None
                else:
                    state.lifecycle = AgentLifecycle.ERROR
                    delay = min(300, 2**state.crash_count)
                    state.next_wake_at = utc_now() + timedelta(seconds=delay)
                self._persist()

    def _require_state(self, agent_id: str) -> AgentRuntimeState:
        state = self._states.get(agent_id)
        if state is None:
            raise ValueError("Agent is not registered with the runtime")
        return state

    def _persist(self) -> None:
        self._store.save(self._states)
        self._on_world_changed()
