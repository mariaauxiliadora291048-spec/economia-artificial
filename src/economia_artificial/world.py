from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from economia_artificial.domain import Agent, AgentStatus, Customer, Product, ProductStatus, money
from economia_artificial.gateway import ActionGateway
from economia_artificial.governance import Capability, EnvironmentMode, PolicyEngine
from economia_artificial.ledger import Ledger
from economia_artificial.market import SimulatedMarket
from economia_artificial.memory import InMemoryMemoryStore, MemoryStore
from economia_artificial.research import ResearchClient, WikipediaResearchClient
from economia_artificial.runtime import AutonomousAgentRuntime, DecisionProvider


@dataclass(frozen=True, slots=True)
class SimulationConfig:
    seed: int
    mode: EnvironmentMode = EnvironmentMode.SANDBOX
    cognitive_cost: Decimal = Decimal("0.03")


@dataclass(slots=True)
class AgentResources:
    compute_units: Decimal = Decimal("10.00")
    token_budget: int = 5_000_000
    tool_budget: int = 5_000
    storage_budget_mb: int = 1_024
    network_budget: int = 10_000

    def can_allocate(self, requested: AgentResources) -> bool:
        return (
            self.compute_units >= requested.compute_units
            and self.token_budget >= requested.token_budget
            and self.tool_budget >= requested.tool_budget
            and self.storage_budget_mb >= requested.storage_budget_mb
            and self.network_budget >= requested.network_budget
        )

    def allocate(self, requested: AgentResources) -> None:
        if not self.can_allocate(requested):
            raise ValueError("Insufficient non-monetary resources for child agent")
        self.compute_units -= requested.compute_units
        self.token_budget -= requested.token_budget
        self.tool_budget -= requested.tool_budget
        self.storage_budget_mb -= requested.storage_budget_mb
        self.network_budget -= requested.network_budget

    def consume_tool(self, tool_name: str) -> bool:
        if self.tool_budget <= 0:
            return False
        if tool_name == "web.search" and self.network_budget <= 0:
            return False
        self.tool_budget -= 1
        if tool_name == "web.search":
            self.network_budget -= 1
        return True

    def consume_cognitive_turn(self, estimated_tokens: int = 1_000) -> bool:
        if self.token_budget < estimated_tokens:
            return False
        self.token_budget -= estimated_tokens
        return True


class EconomyWorld:
    """Governed world façade. Only the gateway can accept agent world actions."""

    def __init__(
        self,
        config: SimulationConfig,
        research: ResearchClient | None = None,
        memory_store: MemoryStore | None = None,
    ) -> None:
        self.config = config
        self.ledger = Ledger()
        self.agents: dict[str, Agent] = {}
        self.resources: dict[str, AgentResources] = {}
        self._agent_created_listeners: list[Callable[[str, str], None]] = []
        self.market = SimulatedMarket(self.ledger, config.seed)
        self.policy = PolicyEngine(config.mode)
        self.research = research or WikipediaResearchClient()
        self.memory = memory_store or InMemoryMemoryStore()
        self.gateway = ActionGateway(
            self.ledger,
            self.market,
            self.agents,
            self.policy,
            self.research,
            self._spawn_child,
            self._consume_tool_budget,
            self._consume_cognitive_budget,
        )
        self.cycle = 0

    @classmethod
    def create(
        cls,
        seed: int,
        mode: EnvironmentMode = EnvironmentMode.SANDBOX,
        research: ResearchClient | None = None,
        memory_store: MemoryStore | None = None,
    ) -> EconomyWorld:
        return cls(SimulationConfig(seed=seed, mode=mode), research, memory_store)

    def create_agent(
        self,
        name: str,
        initial_cash: Decimal | str | int | float = "100.00",
        *,
        objective: str = "maximize_net_worth",
        model_id: str = "deterministic-reference",
        resources: AgentResources | None = None,
    ) -> Agent:
        if any(existing.name == name for existing in self.agents.values()):
            raise ValueError(f"Agent name {name} already exists")
        normalized_cash = money(initial_cash)
        if normalized_cash < 0:
            raise ValueError("Initial cash cannot be negative")
        if not objective.strip():
            raise ValueError("An agent objective is required")
        if not model_id.strip():
            raise ValueError("An agent model is required")
        agent = Agent(
            id=f"agent-{len(self.agents) + 1}",
            name=name,
            objective=objective.strip(),
            model_id=model_id.strip(),
        )
        self.agents[agent.id] = agent
        self.resources[agent.id] = resources or AgentResources()
        self.policy.grant(agent.id, Capability.MARKET_READ)
        self.policy.grant(agent.id, Capability.MARKET_WRITE)
        if normalized_cash > 0:
            self.ledger.transfer(
                debit_account="economy:initial_capital",
                credit_account=self.market.agent_account(agent.id),
                amount=normalized_cash,
                transaction_type="INITIAL_CAPITAL",
                description=f"Initial capital for {agent.id}",
            )
        return agent

    def on_agent_created(self, listener: Callable[[str, str], None]) -> None:
        self._agent_created_listeners.append(listener)

    def _spawn_child(self, parent_agent_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
        parent = self.agents.get(parent_agent_id)
        if parent is None:
            raise ValueError("Unknown parent agent")
        initial_capital = money(arguments["initial_capital"])
        creation_cost = Decimal("1.00")
        parent_account = self.market.agent_account(parent_agent_id)
        if not self.ledger.can_afford(parent_account, initial_capital + creation_cost):
            raise ValueError("Insufficient capital to fund creation and its cost")
        requested_resources = AgentResources(
            compute_units=Decimal(str(arguments["compute_units"])),
            token_budget=int(arguments["token_budget"]),
            tool_budget=int(arguments["tool_budget"]),
            storage_budget_mb=int(arguments["storage_budget_mb"]),
            network_budget=int(arguments["network_budget"]),
        )
        requested_capabilities = {Capability(value) for value in arguments["capabilities"]}
        parent_capabilities = self.policy.capabilities_for(parent_agent_id)
        if not requested_capabilities.issubset(parent_capabilities):
            raise ValueError("A child can receive only capabilities its parent holds")
        parent_resources = self.resources[parent_agent_id]
        parent_resources.allocate(requested_resources)
        child = Agent(
            id=f"agent-{len(self.agents) + 1}",
            name=arguments["name"],
            model_id=parent.model_id,
            objective=arguments["mission"],
        )
        self.agents[child.id] = child
        self.resources[child.id] = requested_resources
        self.ledger.transfer(
            debit_account=parent_account,
            credit_account=self.market.agent_account(child.id),
            amount=initial_capital,
            transaction_type="AGENT_CAPITAL_ALLOCATION",
            description=f"Capital allocated by {parent_agent_id} to {child.id}",
            reference_id=child.id,
        )
        for capability in requested_capabilities:
            self.policy.grant(child.id, capability)
        self.gateway._emit(
            "agent.created",
            parent_agent_id,
            "agent",
            child.id,
            {
                "mission": child.objective,
                "initial_capital": str(initial_capital),
                "capabilities": sorted(capability.value for capability in requested_capabilities),
            },
        )
        self.memory.record(
            parent_agent_id,
            "economic_outcome",
            f"Created child agent {child.name} to pursue: {child.objective}",
            {"child_agent_id": child.id},
            salience=0.9,
        )
        for listener in self._agent_created_listeners:
            listener(parent_agent_id, child.id)
        return {"agent_id": child.id, "name": child.name, "mission": child.objective}

    def grant(self, agent_id: str, capability: Capability) -> None:
        if agent_id not in self.agents:
            raise ValueError("Unknown agent")
        self.policy.grant(agent_id, capability)

    def grant_provider(self, agent_id: str, provider_id: str) -> None:
        if agent_id not in self.agents:
            raise ValueError("Unknown agent")
        self.policy.grant_provider(agent_id, provider_id)

    def _consume_tool_budget(self, agent_id: str, tool_name: str) -> bool:
        resources = self.resources.get(agent_id)
        return resources.consume_tool(tool_name) if resources else False

    def _consume_cognitive_budget(self, agent_id: str) -> bool:
        resources = self.resources.get(agent_id)
        return resources.consume_cognitive_turn() if resources else False

    def perceive(self, agent_id: str) -> dict[str, Any]:
        agent = self.agents.get(agent_id)
        if agent is None:
            raise ValueError("Unknown agent")
        products = [
            {
                "id": product.id,
                "name": product.name,
                "category": product.category,
                "price": str(product.price),
                "status": product.status.value,
                "units_sold": product.units_sold,
            }
            for product in self.market.products.values()
            if product.owner_agent_id == agent_id
        ]
        memories = [
            {
                "kind": memory.kind,
                "content": memory.content,
                "metadata": memory.metadata,
            }
            for memory in self.memory.relevant(agent_id)
        ]
        return {
            "world": {"mode": self.config.mode.value, "cycle": self.cycle},
            "agent": {
                "id": agent.id,
                "name": agent.name,
                "objective": agent.objective,
                "model_id": agent.model_id,
                "reputation": str(agent.reputation),
                "capabilities": sorted(
                    capability.value for capability in self.policy.capabilities_for(agent_id)
                ),
                "providers": sorted(self.policy.providers_for(agent_id)),
            },
            "financial": {
                "cash": str(self.balance_of(agent_id)),
                "net_worth": str(self.net_worth(agent_id)),
            },
            "resources": {
                "compute_units": str(self.resources[agent_id].compute_units),
                "token_budget": self.resources[agent_id].token_budget,
                "tool_budget": self.resources[agent_id].tool_budget,
                "storage_budget_mb": self.resources[agent_id].storage_budget_mb,
                "network_budget": self.resources[agent_id].network_budget,
            },
            "products": products,
            "market": self.market.overview(),
            "memories": memories,
        }

    def run_autonomous_cycle(
        self,
        agent_id: str,
        provider: DecisionProvider,
        max_actions: int = 6,
    ) -> list[dict[str, Any]]:
        runtime = AutonomousAgentRuntime(
            self.gateway,
            self.memory,
            cognitive_cost=self.config.cognitive_cost,
            max_actions_per_cycle=max_actions,
        )
        return runtime.run_cycle(agent_id, lambda: self.perceive(agent_id), provider)

    def add_customer(
        self,
        customer_id: str,
        budget: Decimal | str | int | float,
        needs: Mapping[str, Decimal | str | int | float],
    ) -> Customer:
        if not needs:
            raise ValueError("A customer needs at least one market preference")
        customer = Customer(
            id=customer_id,
            budget=money(budget),
            needs={category: Decimal(str(weight)) for category, weight in needs.items()},
        )
        self.market.add_customer(customer)
        if customer.budget > 0:
            self.ledger.transfer(
                debit_account="economy:initial_capital",
                credit_account=self.market.customer_account(customer.id),
                amount=customer.budget,
                transaction_type="CUSTOMER_BUDGET",
                description=f"Initial budget for customer {customer.id}",
            )
        return customer

    def balance_of(self, agent_id: str) -> Decimal:
        if agent_id not in self.agents:
            raise ValueError("Unknown agent")
        return self.ledger.balance(self.market.agent_account(agent_id))

    def net_worth(self, agent_id: str) -> Decimal:
        # No receivables, assets, liabilities or obligations exist in MVP-1.
        return self.balance_of(agent_id)

    def advance_market(self) -> list[dict[str, object]]:
        self.cycle += 1
        sales = self.market.advance(self.agents)
        for sale in sales:
            self.gateway._emit("product.sale", None, "product", str(sale["product_id"]), sale)
            product = self.market.products[str(sale["product_id"])]
            self.memory.record(
                product.owner_agent_id,
                "economic_outcome",
                f"Product {product.name} generated a sale of {sale['amount']}.",
                sale,
                salience=0.9,
            )
        self.ledger.assert_integrity()
        return sales

    def snapshot(self) -> dict[str, Any]:
        return {
            "config": {
                "seed": self.config.seed,
                "mode": self.config.mode.value,
                "cognitive_cost": str(self.config.cognitive_cost),
            },
            "cycle": self.cycle,
            "agents": [
                {
                    "id": agent.id,
                    "name": agent.name,
                    "model_id": agent.model_id,
                    "objective": agent.objective,
                    "status": agent.status.value,
                    "reputation": str(agent.reputation),
                }
                for agent in self.agents.values()
            ],
            "resources": {
                agent_id: {
                    "compute_units": str(resources.compute_units),
                    "token_budget": resources.token_budget,
                    "tool_budget": resources.tool_budget,
                    "storage_budget_mb": resources.storage_budget_mb,
                    "network_budget": resources.network_budget,
                }
                for agent_id, resources in self.resources.items()
            },
            "customers": [
                {
                    "id": customer.id,
                    "budget": str(customer.budget),
                    "needs": {category: str(value) for category, value in customer.needs.items()},
                    "price_sensitivity": str(customer.price_sensitivity),
                    "reputation_sensitivity": str(customer.reputation_sensitivity),
                }
                for customer in self.market.customers.values()
            ],
            "products": [
                {
                    "id": product.id,
                    "owner_agent_id": product.owner_agent_id,
                    "name": product.name,
                    "description": product.description,
                    "category": product.category,
                    "price": str(product.price) if product.price is not None else None,
                    "status": product.status.value,
                    "units_sold": product.units_sold,
                    "created_at": product.created_at.isoformat(),
                    "updated_at": product.updated_at.isoformat(),
                }
                for product in self.market.products.values()
            ],
            "capability_grants": self.policy.snapshot(),
            "provider_grants": self.policy.provider_snapshot(),
            "transactions": self.ledger.snapshot(),
        }

    @classmethod
    def from_snapshot(
        cls,
        snapshot: dict[str, Any],
        research: ResearchClient | None = None,
        memory_store: MemoryStore | None = None,
    ) -> EconomyWorld:
        config_data = snapshot["config"]
        config = SimulationConfig(
            seed=int(config_data["seed"]),
            mode=EnvironmentMode(config_data["mode"]),
            cognitive_cost=Decimal(str(config_data["cognitive_cost"])),
        )
        world = cls(config, research, memory_store)
        world.agents.clear()
        for raw in snapshot["agents"]:
            agent = Agent(
                id=raw["id"],
                name=raw["name"],
                model_id=raw["model_id"],
                objective=raw["objective"],
                status=AgentStatus(raw["status"]),
                reputation=Decimal(str(raw["reputation"])),
            )
            world.agents[agent.id] = agent
        world.resources.clear()
        for agent_id, raw in snapshot["resources"].items():
            world.resources[agent_id] = AgentResources(
                compute_units=Decimal(str(raw["compute_units"])),
                token_budget=int(raw["token_budget"]),
                tool_budget=int(raw["tool_budget"]),
                storage_budget_mb=int(raw["storage_budget_mb"]),
                network_budget=int(raw["network_budget"]),
            )
        world.market.customers.clear()
        for raw in snapshot["customers"]:
            world.market.customers[raw["id"]] = Customer(
                id=raw["id"],
                budget=money(str(raw["budget"])),
                needs={category: Decimal(value) for category, value in raw["needs"].items()},
                price_sensitivity=Decimal(str(raw["price_sensitivity"])),
                reputation_sensitivity=Decimal(str(raw["reputation_sensitivity"])),
            )
        world.market.products.clear()
        for raw in snapshot["products"]:
            world.market.products[raw["id"]] = Product(
                id=raw["id"],
                owner_agent_id=raw["owner_agent_id"],
                name=raw["name"],
                description=raw["description"],
                category=raw["category"],
                price=money(str(raw["price"])) if raw["price"] is not None else None,
                status=ProductStatus(raw["status"]),
                units_sold=int(raw["units_sold"]),
                created_at=datetime.fromisoformat(raw["created_at"]),
                updated_at=datetime.fromisoformat(raw["updated_at"]),
            )
        world.market._next_product_number = len(world.market.products) + 1
        world.policy.restore(snapshot["capability_grants"])
        world.policy.restore_provider_grants(snapshot.get("provider_grants", {}))
        world.ledger.restore(snapshot["transactions"])
        world.cycle = int(snapshot["cycle"])
        return world
