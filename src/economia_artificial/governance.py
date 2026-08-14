from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class EnvironmentMode(StrEnum):
    SIMULATION = "simulation"
    SANDBOX = "sandbox"
    PAPER = "paper"
    REAL = "real"


class Capability(StrEnum):
    MARKET_READ = "market.read"
    MARKET_WRITE = "market.write"
    WEB_RESEARCH = "web.research"
    AGENT_CREATE = "agent.create"
    EXTERNAL_PUBLISH = "external.publish"
    HUMAN_COMMUNICATION = "human.communication"
    FINANCIAL_TRANSACTION = "financial.transaction"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    code: str | None = None


class PolicyEngine:
    """Capability and mode boundary around all world-facing agent actions."""

    _EXTERNAL_CAPABILITIES = {
        Capability.WEB_RESEARCH,
        Capability.EXTERNAL_PUBLISH,
        Capability.HUMAN_COMMUNICATION,
        Capability.FINANCIAL_TRANSACTION,
    }
    _SENSITIVE_TERMS = frozenset({"api_key", "authorization", "password", "senha", "secret"})

    def __init__(self, mode: EnvironmentMode) -> None:
        self.mode = mode
        self._grants: defaultdict[str, set[Capability]] = defaultdict(set)
        self._provider_grants: defaultdict[str, set[str]] = defaultdict(set)

    def grant(self, agent_id: str, capability: Capability) -> None:
        self._grants[agent_id].add(capability)

    def capabilities_for(self, agent_id: str) -> frozenset[Capability]:
        return frozenset(self._grants[agent_id])

    def grant_provider(self, agent_id: str, provider_id: str) -> None:
        self._provider_grants[agent_id].add(provider_id)

    def provider_is_granted(self, agent_id: str, provider_id: str) -> bool:
        return provider_id in self._provider_grants[agent_id]

    def providers_for(self, agent_id: str) -> frozenset[str]:
        return frozenset(self._provider_grants[agent_id])

    def provider_snapshot(self) -> dict[str, list[str]]:
        return {
            agent_id: sorted(provider_ids)
            for agent_id, provider_ids in self._provider_grants.items()
        }

    def restore_provider_grants(self, serialized_grants: dict[str, list[str]]) -> None:
        self._provider_grants.clear()
        for agent_id, provider_ids in serialized_grants.items():
            self._provider_grants[agent_id] = set(provider_ids)

    def snapshot(self) -> dict[str, list[str]]:
        return {
            agent_id: sorted(capability.value for capability in capabilities)
            for agent_id, capabilities in self._grants.items()
        }

    def restore(self, serialized_grants: dict[str, list[str]]) -> None:
        self._grants.clear()
        for agent_id, values in serialized_grants.items():
            self._grants[agent_id] = {Capability(value) for value in values}

    def evaluate(
        self,
        agent_id: str,
        capability: Capability,
        risk: RiskLevel,
        arguments: dict[str, Any],
    ) -> PolicyDecision:
        if capability not in self._grants[agent_id]:
            return PolicyDecision(False, "CAPABILITY_DENIED")
        if self.mode is EnvironmentMode.SIMULATION and capability in self._EXTERNAL_CAPABILITIES:
            return PolicyDecision(False, "EXTERNAL_ACTION_DISABLED_IN_SIMULATION")
        if risk is RiskLevel.HIGH:
            return PolicyDecision(False, "HUMAN_APPROVAL_REQUIRED")
        if capability is Capability.WEB_RESEARCH and self._contains_sensitive_text(arguments):
            return PolicyDecision(False, "SENSITIVE_DATA_BLOCKED")
        return PolicyDecision(True)

    def _contains_sensitive_text(self, arguments: dict[str, Any]) -> bool:
        serialized = " ".join(str(value).casefold() for value in arguments.values())
        return any(term in serialized for term in self._SENSITIVE_TERMS)
