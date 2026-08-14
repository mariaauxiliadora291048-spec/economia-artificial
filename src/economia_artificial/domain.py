from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

CENT = Decimal("0.01")


def money(value: Decimal | str | int | float) -> Decimal:
    """Normalize virtual currency; floating-point values are never retained."""
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_EVEN)


def utc_now() -> datetime:
    return datetime.now(UTC)


class AgentStatus(StrEnum):
    ACTIVE = "active"
    BANKRUPT = "bankrupt"
    SUSPENDED = "suspended"
    QUARANTINED = "quarantined"


class ProductStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    PAUSED = "paused"


class ValidationStatus(StrEnum):
    PENDING = "pending"
    ALLOWED = "allowed"
    DENIED = "denied"


class ExecutionStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
    FAILED = "failed"


@dataclass(slots=True)
class Agent:
    id: str
    name: str
    model_id: str = "deterministic-reference"
    objective: str = "maximize_net_worth"
    status: AgentStatus = AgentStatus.ACTIVE
    reputation: Decimal = Decimal("0.5000")


@dataclass(slots=True)
class Customer:
    id: str
    budget: Decimal
    needs: dict[str, Decimal]
    price_sensitivity: Decimal = Decimal("0.5000")
    reputation_sensitivity: Decimal = Decimal("0.3000")


@dataclass(slots=True)
class Product:
    id: str
    owner_agent_id: str
    name: str
    description: str
    category: str
    price: Decimal | None = None
    status: ProductStatus = ProductStatus.DRAFT
    units_sold: int = 0
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    account: str
    amount: Decimal


@dataclass(frozen=True, slots=True)
class Transaction:
    id: UUID
    type: str
    debit: LedgerEntry
    credit: LedgerEntry
    description: str
    reference_id: str | None
    created_at: datetime


@dataclass(slots=True)
class ToolCall:
    id: UUID
    agent_id: str
    tool_name: str
    arguments: dict[str, Any]
    cycle_id: str | None = None
    validation_status: ValidationStatus = ValidationStatus.PENDING
    execution_status: ExecutionStatus = ExecutionStatus.PENDING
    cost: Decimal = Decimal("0.00")
    result: dict[str, Any] | None = None
    error_code: str | None = None
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class Event:
    id: UUID
    event_type: str
    agent_id: str | None
    entity_type: str | None
    entity_id: str | None
    payload: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    ok: bool
    call_id: UUID
    result: dict[str, Any] | None = None
    error_code: str | None = None


def new_id() -> str:
    return str(uuid4())
