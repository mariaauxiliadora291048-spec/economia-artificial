from __future__ import annotations

from pathlib import Path

from economia_artificial.governance import Capability, EnvironmentMode
from economia_artificial.memory import JsonMemoryStore
from economia_artificial.openai_provider import OpenAIResponsesDecisionProvider
from economia_artificial.world import EconomyWorld


def main() -> None:
    memory_path = Path(".economia-artificial-data") / "agent-memory.json"
    world = EconomyWorld.create(
        seed=42,
        mode=EnvironmentMode.SANDBOX,
        memory_store=JsonMemoryStore(memory_path),
    )
    agent = world.create_agent("A17", initial_cash="100.00")
    world.add_customer("C1", budget="50.00", needs={"productivity": "1.0"})
    world.add_customer("C2", budget="40.00", needs={"automation": "0.8"})
    world.grant(agent.id, Capability.WEB_RESEARCH)

    provider = OpenAIResponsesDecisionProvider.from_environment()
    observations = world.run_autonomous_cycle(agent.id, provider)
    sales = world.advance_market()

    print({"observations": observations, "sales": sales, "state": world.perceive(agent.id)})


if __name__ == "__main__":
    main()
