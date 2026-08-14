import json
from decimal import Decimal

from economia_artificial.domain import ExecutionStatus, ProductStatus
from economia_artificial.governance import Capability, EnvironmentMode
from economia_artificial.memory import JsonMemoryStore
from economia_artificial.research import ResearchItem, ResearchReport
from economia_artificial.runtime import AgentRuntime, ProposedAction, ScriptedDecisionProvider
from economia_artificial.scheduler import AgentLifecycle, AgentScheduler, JsonRuntimeStore
from economia_artificial.world import AgentResources, EconomyWorld


class FakeResearchClient:
    def search(self, query: str) -> ResearchReport:
        return ResearchReport(
            query=query,
            source="https://source.example.test",
            items=[
                ResearchItem(
                    title="Pesquisa real simulada",
                    snippet="Uma fonte retornou evidência para a hipótese.",
                    source_url="https://source.example.test/evidence",
                )
            ],
        )


def create_product(world: EconomyWorld, agent_id: str, price: str = "5.00") -> str:
    created = world.gateway.execute(
        agent_id,
        "product.create",
        {
            "name": "Template de produtividade",
            "description": "Um template digital para organizar projetos e tarefas.",
            "category": "productivity",
        },
    )
    assert created.ok
    assert created.result is not None
    product_id = str(created.result["product_id"])
    assert world.gateway.execute(
        agent_id, "product.price", {"product_id": product_id, "price": price}
    ).ok
    assert world.gateway.execute(agent_id, "product.publish", {"product_id": product_id}).ok
    return product_id


def test_every_monetary_change_is_a_balanced_append_only_transaction() -> None:
    world = EconomyWorld.create(seed=7)
    agent = world.create_agent("A17", initial_cash="100.00")
    world.add_customer("C1", budget="50.00", needs={"productivity": "1.0"})
    product_id = create_product(world, agent.id)

    initial_transaction_count = len(world.ledger.transactions)
    world.advance_market()

    assert len(world.ledger.transactions) > initial_transaction_count
    assert all(
        transaction.debit.amount == transaction.credit.amount
        for transaction in world.ledger.transactions
    )
    world.ledger.assert_integrity()
    assert world.market.products[product_id].units_sold >= 0
    assert world.balance_of(agent.id) >= Decimal("99.83")


def test_actions_are_audited_and_rejected_before_state_changes() -> None:
    world = EconomyWorld.create(seed=1)
    agent = world.create_agent("A17")
    before = world.balance_of(agent.id)

    rejected = world.gateway.execute(agent.id, "external.http", {"url": "https://example.com"})

    assert not rejected.ok
    assert rejected.error_code == "UNKNOWN_TOOL"
    assert world.balance_of(agent.id) == before
    assert len(world.gateway.tool_calls) == 1
    assert world.gateway.tool_calls[0].execution_status is ExecutionStatus.REJECTED
    assert world.gateway.events[-1].event_type == "tool.rejected"


def test_product_lifecycle_only_changes_through_registered_tools() -> None:
    world = EconomyWorld.create(seed=9)
    owner = world.create_agent("owner")
    other = world.create_agent("other")
    product_id = create_product(world, owner.id)

    attempt = world.gateway.execute(
        other.id, "product.price", {"product_id": product_id, "price": "1.00"}
    )

    assert not attempt.ok
    assert attempt.error_code == "EXECUTION_FAILED"
    assert world.market.products[product_id].price == Decimal("5.00")
    assert world.market.products[product_id].status is ProductStatus.PUBLISHED


def test_seeded_market_is_reproducible() -> None:
    def simulate() -> tuple[list[dict[str, object]], Decimal]:
        world = EconomyWorld.create(seed=123)
        agent = world.create_agent("A17")
        world.add_customer("C1", budget="50.00", needs={"productivity": "1.0"})
        create_product(world, agent.id, price="3.00")
        return world.advance_market(), world.balance_of(agent.id)

    assert simulate() == simulate()


def test_runtime_observes_each_tool_result_before_the_next_decision() -> None:
    world = EconomyWorld.create(seed=1)
    agent = world.create_agent("A17")
    provider = ScriptedDecisionProvider(
        [
            ProposedAction("market.search", {"query": "productivity", "category": "productivity"}),
            ProposedAction("market.search", {"query": "", "category": None}),
        ]
    )

    observations = AgentRuntime(world.gateway).run_cycle(agent.id, {"cycle": 1}, provider)

    assert [observation["ok"] for observation in observations] == [True, True]
    assert len(world.gateway.tool_calls) == 2


def test_web_research_requires_a_granted_capability() -> None:
    world = EconomyWorld.create(seed=1, research=FakeResearchClient())
    agent = world.create_agent("A17")

    denied = world.gateway.execute(agent.id, "web.search", {"query": "economia digital"})
    world.grant(agent.id, Capability.WEB_RESEARCH)
    allowed = world.gateway.execute(agent.id, "web.search", {"query": "economia digital"})

    assert denied.error_code == "CAPABILITY_DENIED"
    assert allowed.ok
    assert allowed.result is not None
    assert allowed.result["source"] == "https://source.example.test"


def test_simulation_mode_blocks_research_even_with_a_grant() -> None:
    world = EconomyWorld.create(
        seed=1,
        mode=EnvironmentMode.SIMULATION,
        research=FakeResearchClient(),
    )
    agent = world.create_agent("A17")
    world.grant(agent.id, Capability.WEB_RESEARCH)

    outcome = world.gateway.execute(agent.id, "web.search", {"query": "economia digital"})

    assert outcome.error_code == "EXTERNAL_ACTION_DISABLED_IN_SIMULATION"


def test_autonomous_runtime_records_observations_and_reflection() -> None:
    world = EconomyWorld.create(seed=1, research=FakeResearchClient())
    agent = world.create_agent("A17")
    world.grant(agent.id, Capability.WEB_RESEARCH)
    provider = ScriptedDecisionProvider(
        [
            ProposedAction("web.search", {"query": "economia digital"}),
            ProposedAction(
                "agent.finish",
                {"reflection": "A evidência é promissora.", "next_hypothesis": "Testar produto."},
            ),
        ]
    )

    observations = world.run_autonomous_cycle(agent.id, provider)
    memories = world.memory.relevant(agent.id)

    assert observations[0]["tool"] == "web.search"
    assert {memory.kind for memory in memories} == {"episode", "strategy"}
    assert world.balance_of(agent.id) == Decimal("99.92")


def test_json_memory_survives_a_new_store_instance(tmp_path) -> None:
    path = tmp_path / "agent-memory.json"
    first_store = JsonMemoryStore(path)
    first_store.record("agent-1", "strategy", "Testar uma hipótese.", {}, salience=0.8)

    restored_store = JsonMemoryStore(path)

    assert restored_store.relevant("agent-1")[0].content == "Testar uma hipótese."


def test_agent_creation_is_an_economic_governed_action() -> None:
    world = EconomyWorld.create(seed=1, research=FakeResearchClient())
    parent = world.create_agent("A01", initial_cash="100.00")
    world.grant(parent.id, Capability.AGENT_CREATE)

    outcome = world.gateway.execute(
        parent.id,
        "agent.create",
        {
            "name": "A02",
            "mission": "Pesquisar oportunidades de automação para pequenas empresas.",
            "initial_capital": "10.00",
            "compute_units": "1.00",
            "token_budget": 100_000,
            "tool_budget": 100,
            "storage_budget_mb": 100,
            "network_budget": 100,
            "capabilities": ["market.read", "market.write"],
        },
    )

    assert outcome.ok
    assert outcome.result is not None
    child_id = str(outcome.result["agent_id"])
    assert world.agents[child_id].objective.startswith("Pesquisar oportunidades")
    assert world.balance_of(parent.id) == Decimal("89.00")
    assert world.balance_of(child_id) == Decimal("10.00")
    assert world.resources[parent.id].compute_units == Decimal("9.00")
    assert world.resources[child_id].token_budget == 100_000


def test_world_snapshot_restores_identity_economy_and_capabilities(tmp_path) -> None:
    world = EconomyWorld.create(seed=1, research=FakeResearchClient())
    agent = world.create_agent("A01", initial_cash="50.00")
    world.grant(agent.id, Capability.WEB_RESEARCH)
    snapshot_path = tmp_path / "world.json"
    snapshot_path.write_text(json.dumps(world.snapshot()), encoding="utf-8")

    restored = EconomyWorld.from_snapshot(json.loads(snapshot_path.read_text(encoding="utf-8")))

    assert restored.agents[agent.id].name == "A01"
    assert restored.balance_of(agent.id) == Decimal("50.00")
    assert Capability.WEB_RESEARCH in restored.policy.capabilities_for(agent.id)


def test_scheduler_persists_lifecycle_after_an_autonomous_cycle(tmp_path) -> None:
    world = EconomyWorld.create(seed=1, research=FakeResearchClient())
    agent = world.create_agent("A01")
    scheduler = AgentScheduler(
        world,
        lambda _: ScriptedDecisionProvider([ProposedAction("agent.finish", {})]),
        JsonRuntimeStore(tmp_path / "runtime.json"),
        lambda: None,
        cycle_interval_seconds=60,
    )
    scheduler.register_agent(agent.id, "test-provider")
    scheduler.start_agent(agent.id)
    scheduler._run_agent_cycle(agent.id)

    restored = JsonRuntimeStore(tmp_path / "runtime.json").load()[agent.id]

    assert restored.lifecycle is AgentLifecycle.WAITING
    assert restored.cycles_completed == 1


def test_agent_creation_preserves_ui_objective_model_and_budgets() -> None:
    world = EconomyWorld.create(seed=1)
    resources = AgentResources(token_budget=42_000, tool_budget=80, network_budget=12)

    agent = world.create_agent(
        "A01",
        initial_cash="125.00",
        objective="Aumentar patrimônio por meio de pesquisa e experimentos de mercado.",
        model_id="local-model",
        resources=resources,
    )

    perception = world.perceive(agent.id)

    assert perception["agent"]["objective"].startswith("Aumentar patrimônio")
    assert perception["agent"]["model_id"] == "local-model"
    assert perception["resources"]["token_budget"] == 42_000
    assert perception["resources"]["tool_budget"] == 80
    assert perception["resources"]["network_budget"] == 12
