from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ModelRequirements:
    tools: bool = True
    vision: bool = False
    reasoning: bool = False
    embeddings: bool = False


class ModelRouter:
    """Chooses only pre-granted, enabled provider configurations.

    It is intentionally deterministic: a fallback is considered only when the
    caller explicitly includes it in the ordered provider configuration list.
    """

    def select(
        self,
        provider_configurations: list[dict[str, Any]],
        granted_provider_ids: frozenset[str],
        requirements: ModelRequirements | None = None,
    ) -> str:
        requirements = requirements or ModelRequirements()
        for configuration in provider_configurations:
            if not configuration["enabled"] or configuration["id"] not in granted_provider_ids:
                continue
            capability = configuration["capability"]
            if requirements.tools and not capability["supports_tools"]:
                continue
            if requirements.vision and not capability["supports_vision"]:
                continue
            if requirements.reasoning and not capability["supports_reasoning"]:
                continue
            if requirements.embeddings and not capability["supports_embeddings"]:
                continue
            if configuration["model"]:
                return str(configuration["id"])
        raise RuntimeError("No granted provider configuration satisfies the model requirements")
