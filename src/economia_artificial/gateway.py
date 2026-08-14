from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any
from uuid import uuid4

from economia_artificial.domain import (
    Agent,
    AgentStatus,
    Event,
    ExecutionStatus,
    ToolCall,
    ToolOutcome,
    ValidationStatus,
    money,
    utc_now,
)
from economia_artificial.governance import Capability, PolicyEngine, RiskLevel
from economia_artificial.ledger import Ledger
from economia_artificial.market import MarketError, SimulatedMarket
from economia_artificial.research import ResearchClient, ResearchError

ToolExecutor = Callable[[str, dict[str, Any]], dict[str, Any]]
AgentSpawner = Callable[[str, dict[str, Any]], dict[str, Any]]
ToolBudgetConsumer = Callable[[str, str], bool]
CognitiveBudgetConsumer = Callable[[str], bool]


class ToolDefinition:
    def __init__(
        self,
        cost: str,
        capability: Capability,
        risk: RiskLevel,
        validate: Callable[[dict[str, Any]], None],
        execute: ToolExecutor,
    ) -> None:
        self.cost = money(cost)
        self.capability = capability
        self.risk = risk
        self.validate = validate
        self.execute = execute


class ActionGateway:
    """The sole public path by which an agent changes the simulated world."""

    def __init__(
        self,
        ledger: Ledger,
        market: SimulatedMarket,
        agents: dict[str, Agent],
        policy: PolicyEngine,
        research: ResearchClient,
        spawn_agent: AgentSpawner,
        consume_tool_budget: ToolBudgetConsumer,
        consume_cognitive_budget: CognitiveBudgetConsumer,
    ) -> None:
        self._ledger = ledger
        self._market = market
        self._agents = agents
        self._policy = policy
        self._research = research
        self._spawn_agent = spawn_agent
        self._consume_tool_budget = consume_tool_budget
        self._consume_cognitive_budget = consume_cognitive_budget
        self.tool_calls: list[ToolCall] = []
        self.events: list[Event] = []
        self._tools = self._build_registry()

    def execute(
        self,
        agent_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        cycle_id: str | None = None,
    ) -> ToolOutcome:
        call = ToolCall(
            id=uuid4(),
            agent_id=agent_id,
            tool_name=tool_name,
            arguments=dict(arguments),
            cycle_id=cycle_id,
        )
        self.tool_calls.append(call)

        agent = self._agents.get(agent_id)
        if agent is None or agent.status is not AgentStatus.ACTIVE:
            return self._reject(call, "POLICY_DENIED")
        tool = self._tools.get(tool_name)
        if tool is None:
            return self._reject(call, "UNKNOWN_TOOL")
        policy_decision = self._policy.evaluate(agent_id, tool.capability, tool.risk, arguments)
        if not policy_decision.allowed:
            return self._reject(call, policy_decision.code or "POLICY_DENIED")
        try:
            tool.validate(arguments)
        except (TypeError, ValueError, MarketError, ResearchError):
            return self._reject(call, "INVALID_ARGUMENTS")
        call.validation_status = ValidationStatus.ALLOWED

        agent_account = self._market.agent_account(agent_id)
        if not self._ledger.can_afford(agent_account, tool.cost):
            return self._reject(call, "INSUFFICIENT_RESOURCES")
        if not self._consume_tool_budget(agent_id, tool_name):
            return self._reject(call, "RESOURCE_BUDGET_EXCEEDED")
        try:
            result = tool.execute(agent_id, arguments)
        except (MarketError, ResearchError, ValueError):
            return self._failure(call, "EXECUTION_FAILED")

        self._ledger.transfer(
            debit_account=agent_account,
            credit_account="economy:treasury",
            amount=tool.cost,
            transaction_type="TOOL_COST",
            description=f"{tool_name} execution cost",
            reference_id=str(call.id),
        )
        call.cost = tool.cost
        call.result = result
        call.execution_status = ExecutionStatus.SUCCEEDED
        event_payload = {"tool_name": tool_name, "cost": str(tool.cost), "result": result}
        self._emit(
            "tool.executed",
            agent_id,
            "tool_call",
            str(call.id),
            event_payload,
        )
        return ToolOutcome(ok=True, call_id=call.id, result=result)

    def charge_cognition(self, agent_id: str, cost: Decimal | str = "0.03") -> bool:
        """Account for a model decision without granting it a world-action capability."""
        agent = self._agents.get(agent_id)
        normalized_cost = money(cost)
        if agent is None or agent.status is not AgentStatus.ACTIVE:
            return False
        agent_account = self._market.agent_account(agent_id)
        if not self._ledger.can_afford(agent_account, normalized_cost):
            return False
        if not self._consume_cognitive_budget(agent_id):
            return False
        self._ledger.transfer(
            debit_account=agent_account,
            credit_account="economy:treasury",
            amount=normalized_cost,
            transaction_type="MODEL_INFERENCE_COST",
            description="Cognitive model inference cost",
        )
        self._emit(
            "model.inference_charged",
            agent_id,
            "agent",
            agent_id,
            {"cost": str(normalized_cost)},
        )
        return True

    def _reject(self, call: ToolCall, error_code: str) -> ToolOutcome:
        call.validation_status = ValidationStatus.DENIED
        call.execution_status = ExecutionStatus.REJECTED
        call.error_code = error_code
        event_payload = {"tool_name": call.tool_name, "error_code": error_code}
        self._emit(
            "tool.rejected",
            call.agent_id,
            "tool_call",
            str(call.id),
            event_payload,
        )
        return ToolOutcome(ok=False, call_id=call.id, error_code=error_code)

    def _failure(self, call: ToolCall, error_code: str) -> ToolOutcome:
        call.execution_status = ExecutionStatus.FAILED
        call.error_code = error_code
        event_payload = {"tool_name": call.tool_name, "error_code": error_code}
        self._emit(
            "tool.failed",
            call.agent_id,
            "tool_call",
            str(call.id),
            event_payload,
        )
        return ToolOutcome(ok=False, call_id=call.id, error_code=error_code)

    def _emit(
        self,
        event_type: str,
        agent_id: str | None,
        entity_type: str | None,
        entity_id: str | None,
        payload: dict[str, Any],
    ) -> None:
        self.events.append(
            Event(uuid4(), event_type, agent_id, entity_type, entity_id, payload, utc_now())
        )

    def _build_registry(self) -> dict[str, ToolDefinition]:
        return {
            "market.search": ToolDefinition(
                "0.01", Capability.MARKET_READ, RiskLevel.LOW, self._validate_search, self._search
            ),
            "market.inspect": ToolDefinition(
                "0.01", Capability.MARKET_READ, RiskLevel.LOW, self._validate_inspect, self._inspect
            ),
            "product.create": ToolDefinition(
                cost="0.05",
                capability=Capability.MARKET_WRITE,
                risk=RiskLevel.MEDIUM,
                validate=self._validate_create,
                execute=self._create,
            ),
            "product.update": ToolDefinition(
                cost="0.03",
                capability=Capability.MARKET_WRITE,
                risk=RiskLevel.MEDIUM,
                validate=self._validate_update,
                execute=self._update,
            ),
            "product.publish": ToolDefinition(
                cost="0.10",
                capability=Capability.MARKET_WRITE,
                risk=RiskLevel.MEDIUM,
                validate=self._validate_product_id,
                execute=self._publish,
            ),
            "product.price": ToolDefinition(
                "0.02", Capability.MARKET_WRITE, RiskLevel.MEDIUM, self._validate_price, self._price
            ),
            "web.search": ToolDefinition(
                cost="0.02",
                capability=Capability.WEB_RESEARCH,
                risk=RiskLevel.LOW,
                validate=self._validate_web_search,
                execute=self._web_search,
            ),
            "agent.create": ToolDefinition(
                cost="1.00",
                capability=Capability.AGENT_CREATE,
                risk=RiskLevel.MEDIUM,
                validate=self._validate_agent_create,
                execute=self._create_agent,
            ),
        }

    @staticmethod
    def _required(arguments: dict[str, Any], key: str, expected_type: type[object]) -> object:
        value = arguments.get(key)
        if not isinstance(value, expected_type):
            raise ValueError(f"{key} is required")
        return value

    def _validate_search(self, arguments: dict[str, Any]) -> None:
        query = self._required(arguments, "query", str)
        category = arguments.get("category")
        if len(query) > 100 or (
            category is not None and category not in self._market.ALLOWED_CATEGORIES
        ):
            raise ValueError("Invalid market search")

    @staticmethod
    def _validate_inspect(arguments: dict[str, Any]) -> None:
        ActionGateway._required(arguments, "product_id", str)

    def _validate_create(self, arguments: dict[str, Any]) -> None:
        self._market._validate_product_text(
            self._required(arguments, "name", str),
            self._required(arguments, "description", str),
            self._required(arguments, "category", str),
        )

    def _validate_update(self, arguments: dict[str, Any]) -> None:
        self._required(arguments, "product_id", str)
        for key in ("name", "description", "category"):
            if (
                key in arguments
                and arguments[key] is not None
                and not isinstance(arguments[key], str)
            ):
                raise ValueError(f"{key} must be a string or null")
        updatable_fields = ("name", "description", "category")
        if not any(arguments.get(key) is not None for key in updatable_fields):
            raise ValueError("At least one product field must change")

    @staticmethod
    def _validate_product_id(arguments: dict[str, Any]) -> None:
        ActionGateway._required(arguments, "product_id", str)

    def _validate_price(self, arguments: dict[str, Any]) -> None:
        self._validate_product_id(arguments)
        value = arguments.get("price")
        try:
            price = money(value)
        except Exception as exc:
            raise ValueError("price must be monetary") from exc
        if not Decimal("0.01") <= price <= Decimal("1000.00"):
            raise ValueError("Price out of range")

    def _validate_web_search(self, arguments: dict[str, Any]) -> None:
        query = self._required(arguments, "query", str)
        if not 2 <= len(query.strip()) <= 200:
            raise ValueError("Invalid web research query")

    def _validate_agent_create(self, arguments: dict[str, Any]) -> None:
        name = self._required(arguments, "name", str)
        mission = self._required(arguments, "mission", str)
        capabilities = arguments.get("capabilities")
        if not 1 <= len(name.strip()) <= 100 or not 10 <= len(mission.strip()) <= 1_000:
            raise ValueError("Invalid child identity or mission")
        if not isinstance(capabilities, list) or not all(
            isinstance(item, str) for item in capabilities
        ):
            raise ValueError("Child capabilities must be a string list")
        try:
            initial_capital = money(arguments["initial_capital"])
            compute_units = Decimal(str(arguments["compute_units"]))
            integer_budgets = [
                int(arguments["token_budget"]),
                int(arguments["tool_budget"]),
                int(arguments["storage_budget_mb"]),
                int(arguments["network_budget"]),
            ]
            requested_capabilities = {Capability(value) for value in capabilities}
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Invalid child resource allocation") from exc
        if (
            initial_capital < Decimal("0.01")
            or compute_units <= 0
            or any(value < 0 for value in integer_budgets)
            or not requested_capabilities
        ):
            raise ValueError("Child allocation must contain positive resources and capabilities")

    def _search(self, _: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"results": self._market.search(arguments["query"], arguments.get("category"))}

    def _inspect(self, _: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._market.inspect(arguments["product_id"])

    def _create(self, agent_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
        name = arguments["name"]
        description = arguments["description"]
        category = arguments["category"]
        product = self._market.create_product(agent_id, name, description, category)
        return {"product_id": product.id, "status": product.status.value}

    def _update(self, agent_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
        product = self._market.update_product(
            agent_id,
            arguments["product_id"],
            arguments.get("name"),
            arguments.get("description"),
            arguments.get("category"),
        )
        return {"product_id": product.id, "status": product.status.value}

    def _publish(self, agent_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
        product = self._market.publish_product(agent_id, arguments["product_id"])
        return {"product_id": product.id, "status": product.status.value}

    def _price(self, agent_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
        product = self._market.set_price(agent_id, arguments["product_id"], arguments["price"])
        return {"product_id": product.id, "price": str(product.price)}

    def _web_search(self, _: str, arguments: dict[str, Any]) -> dict[str, Any]:
        report = self._research.search(arguments["query"])
        return {
            "query": report.query,
            "source": report.source,
            "results": [
                {"title": item.title, "snippet": item.snippet, "source_url": item.source_url}
                for item in report.items
            ],
        }

    def _create_agent(self, agent_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._spawn_agent(agent_id, arguments)
