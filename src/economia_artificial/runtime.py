from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from economia_artificial.gateway import ActionGateway
from economia_artificial.memory import MemoryStore


@dataclass(frozen=True, slots=True)
class ProposedAction:
    name: str
    arguments: dict[str, Any]


class DecisionProvider(Protocol):
    """Port for a future local/sandboxed model provider."""

    def decide(
        self, state: dict[str, Any], observation: dict[str, Any] | None
    ) -> ProposedAction | None: ...


class ScriptedDecisionProvider:
    """Deterministic provider used for reference runs and tests, never an LLM."""

    def __init__(self, actions: Sequence[ProposedAction]) -> None:
        self._actions = iter(actions)

    def decide(
        self, state: dict[str, Any], observation: dict[str, Any] | None
    ) -> ProposedAction | None:
        del state, observation
        return next(self._actions, None)


class AgentRuntime:
    def __init__(self, gateway: ActionGateway, max_actions_per_cycle: int = 8) -> None:
        self._gateway = gateway
        self._max_actions_per_cycle = max_actions_per_cycle

    def run_cycle(
        self, agent_id: str, state: dict[str, Any], provider: DecisionProvider
    ) -> list[dict[str, Any]]:
        observations: list[dict[str, Any]] = []
        last_observation: dict[str, Any] | None = None
        for _ in range(self._max_actions_per_cycle):
            action = provider.decide(state, last_observation)
            if action is None:
                break
            outcome = self._gateway.execute(agent_id, action.name, action.arguments)
            last_observation = {
                "tool": action.name,
                "ok": outcome.ok,
                "result": outcome.result,
                "error_code": outcome.error_code,
            }
            observations.append(last_observation)
        return observations


class AutonomousAgentRuntime:
    """Perception → deliberation → action → observation → memory loop.

    The provider determines the next initiative. The runtime supplies only the
    cognitive budget, governance boundary, durable observation and stop limits.
    """

    def __init__(
        self,
        gateway: ActionGateway,
        memory: MemoryStore,
        cognitive_cost: Decimal,
        max_actions_per_cycle: int = 6,
    ) -> None:
        self._gateway = gateway
        self._memory = memory
        self._cognitive_cost = cognitive_cost
        self._max_actions_per_cycle = max_actions_per_cycle

    def run_cycle(
        self,
        agent_id: str,
        perceive: Callable[[], dict[str, Any]],
        provider: DecisionProvider,
    ) -> list[dict[str, Any]]:
        observations: list[dict[str, Any]] = []
        observation: dict[str, Any] | None = None
        for _ in range(self._max_actions_per_cycle):
            if not self._gateway.charge_cognition(agent_id, self._cognitive_cost):
                self._memory.record(
                    agent_id,
                    "operational_limit",
                    "Cycle paused because the cognitive budget is exhausted.",
                    {},
                    salience=0.9,
                )
                break
            action = provider.decide(perceive(), observation)
            if action is None:
                break
            if action.name == "agent.finish":
                self._record_reflection(agent_id, action)
                break
            outcome = self._gateway.execute(agent_id, action.name, action.arguments)
            observation = {
                "tool": action.name,
                "ok": outcome.ok,
                "result": outcome.result,
                "error_code": outcome.error_code,
            }
            observations.append(observation)
            self._memory.record(
                agent_id,
                "episode",
                f"Tried {action.name}; success={outcome.ok}.",
                {"arguments": action.arguments, "observation": observation},
                salience=0.8 if not outcome.ok else 0.6,
            )
        return observations

    def _record_reflection(self, agent_id: str, action: ProposedAction) -> None:
        reflection = str(action.arguments.get("reflection", "Cycle completed."))
        hypothesis = str(action.arguments.get("next_hypothesis", ""))
        self._memory.record(
            agent_id,
            "strategy",
            reflection,
            {"next_hypothesis": hypothesis},
            salience=0.9,
        )
