from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from economia_artificial.domain import LedgerEntry, Transaction, money, utc_now


class InsufficientFundsError(ValueError):
    pass


class Ledger:
    """Append-only double-entry ledger.

    A positive balance is an asset available to the account. Each posting moves
    exactly the same amount from one account to another; no public mutator can
    set a balance directly.
    """

    def __init__(self) -> None:
        self._balances: defaultdict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
        self._transactions: list[Transaction] = []

    def balance(self, account: str) -> Decimal:
        return money(self._balances[account])

    @property
    def transactions(self) -> tuple[Transaction, ...]:
        return tuple(self._transactions)

    def can_afford(self, account: str, amount: Decimal | str | int | float) -> bool:
        return self.balance(account) >= money(amount)

    def transfer(
        self,
        *,
        debit_account: str,
        credit_account: str,
        amount: Decimal | str | int | float,
        transaction_type: str,
        description: str,
        reference_id: str | None = None,
    ) -> Transaction:
        normalized_amount = money(amount)
        if normalized_amount <= 0:
            raise ValueError("A transaction amount must be positive")
        if debit_account == credit_account:
            raise ValueError("A transaction must use two distinct accounts")
        # The monetary authority may issue the fixed initial money supply. Its
        # negative balance is the matching liability, so this still preserves
        # double-entry accounting instead of making money appear in wallets.
        can_issue_money = debit_account == "economy:initial_capital"
        if not can_issue_money and not self.can_afford(debit_account, normalized_amount):
            raise InsufficientFundsError(f"{debit_account} cannot cover {normalized_amount}")

        transaction = Transaction(
            id=uuid4(),
            type=transaction_type,
            debit=LedgerEntry(debit_account, normalized_amount),
            credit=LedgerEntry(credit_account, normalized_amount),
            description=description,
            reference_id=reference_id,
            created_at=utc_now(),
        )
        self._balances[debit_account] -= normalized_amount
        self._balances[credit_account] += normalized_amount
        self._transactions.append(transaction)
        return transaction

    def assert_integrity(self) -> None:
        """Defensive audit suitable for test suites and reconciliation jobs."""
        derived: defaultdict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
        for transaction in self._transactions:
            if transaction.debit.amount != transaction.credit.amount:
                raise AssertionError(f"Unbalanced transaction {transaction.id}")
            derived[transaction.debit.account] -= transaction.debit.amount
            derived[transaction.credit.account] += transaction.credit.amount
        accounts: Iterable[str] = set(derived) | set(self._balances)
        for account in accounts:
            if money(derived[account]) != self.balance(account):
                raise AssertionError(f"Ledger drift in {account}")

    def snapshot(self) -> list[dict[str, str | None]]:
        return [
            {
                "id": str(transaction.id),
                "type": transaction.type,
                "debit_account": transaction.debit.account,
                "credit_account": transaction.credit.account,
                "amount": str(transaction.debit.amount),
                "description": transaction.description,
                "reference_id": transaction.reference_id,
                "created_at": transaction.created_at.isoformat(),
            }
            for transaction in self._transactions
        ]

    def restore(self, serialized_transactions: list[dict[str, str | None]]) -> None:
        self._balances.clear()
        self._transactions.clear()
        for raw in serialized_transactions:
            amount = money(str(raw["amount"]))
            transaction = Transaction(
                id=UUID(str(raw["id"])),
                type=str(raw["type"]),
                debit=LedgerEntry(str(raw["debit_account"]), amount),
                credit=LedgerEntry(str(raw["credit_account"]), amount),
                description=str(raw["description"]),
                reference_id=raw["reference_id"],
                created_at=datetime.fromisoformat(str(raw["created_at"])),
            )
            self._balances[transaction.debit.account] -= amount
            self._balances[transaction.credit.account] += amount
            self._transactions.append(transaction)
        self.assert_integrity()
