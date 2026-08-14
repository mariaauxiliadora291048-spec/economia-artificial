from __future__ import annotations

from typing import Protocol


class ConnectorDisabledError(RuntimeError):
    pass


class SocialConnector(Protocol):
    def read_feed(self) -> list[dict[str, str]]: ...

    def search(self, query: str) -> list[dict[str, str]]: ...

    def draft_post(self, text: str) -> str: ...

    def publish_post(self, draft_id: str) -> str: ...


class EmailConnector(Protocol):
    def search(self, query: str) -> list[dict[str, str]]: ...

    def draft(self, recipient: str, subject: str, body: str) -> str: ...

    def send(self, draft_id: str) -> str: ...


class PhoneConnector(Protocol):
    def receive(self) -> list[dict[str, str]]: ...

    def transcribe(self, message_id: str) -> str: ...

    def call(self, destination: str) -> str: ...


class ComputerConnector(Protocol):
    def observe_screen(self) -> bytes: ...

    def click(self, x: int, y: int) -> None: ...

    def type(self, text: str) -> None: ...


class PaymentConnector(Protocol):
    def create_transfer(self, amount: str, destination: str) -> str: ...


class DisabledConnector:
    """Explicit default for all connectors with public, human or financial effects."""

    def __getattr__(self, _: str) -> object:
        raise ConnectorDisabledError("Connector exists architecturally but is not enabled")
