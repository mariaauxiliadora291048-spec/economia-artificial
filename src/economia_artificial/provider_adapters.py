from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from economia_artificial.provider_catalog import ProviderDescriptor


@dataclass(frozen=True, slots=True)
class ModelMetadata:
    model_id: str
    display_name: str
    context_length: int | None = None
    input_modalities: tuple[str, ...] = ("text",)
    output_modalities: tuple[str, ...] = ("text",)
    tool_calling: bool | None = None
    vision: bool | None = None
    reasoning: bool | None = None
    availability: str = "unknown"

    def public_view(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ConnectionResult:
    status: str
    message: str
    models: tuple[ModelMetadata, ...] = ()

    def public_view(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "message": self.message,
            "models": [model.public_view() for model in self.models],
        }


class ProviderAdapter(Protocol):
    def inspect(
        self,
        descriptor: ProviderDescriptor,
        base_url: str | None,
        api_key: str | None,
    ) -> ConnectionResult: ...


class OpenAICompatibleAdapter:
    """Health and model discovery for OpenAI-shaped management endpoints only."""

    def inspect(
        self,
        descriptor: ProviderDescriptor,
        base_url: str | None,
        api_key: str | None,
    ) -> ConnectionResult:
        if not base_url:
            return ConnectionResult("NOT_CONFIGURED", "A base URL is required for this provider")
        if descriptor.authentication_type == "bearer" and not api_key:
            return ConnectionResult(
                "NOT_CONFIGURED", "Configure an API key or environment variable"
            )
        if not descriptor.models_endpoint:
            return ConnectionResult(
                "UNAVAILABLE", "This provider has no configured model discovery endpoint"
            )
        headers = {"Accept": "application/json", "User-Agent": "EconomiaArtificial/0.4"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if descriptor.id == "github_models":
            headers["X-GitHub-Api-Version"] = "2026-03-10"
        url = _endpoint_url(base_url, descriptor.models_endpoint)
        try:
            request = Request(url, headers=headers)
            with urlopen(request, timeout=5) as response:  # noqa: S310
                payload = json.loads(response.read())
        except HTTPError as error:
            return _http_error(error)
        except (URLError, OSError, TimeoutError) as error:
            return ConnectionResult(
                "NETWORK_ERROR", str(error.reason if hasattr(error, "reason") else error)
            )
        except json.JSONDecodeError:
            return ConnectionResult("UNAVAILABLE", "The provider returned invalid JSON")
        return ConnectionResult(
            "CONNECTED", "Model discovery succeeded", tuple(_parse_models(payload))
        )


class MetadataOnlyAdapter:
    def inspect(
        self,
        descriptor: ProviderDescriptor,
        base_url: str | None,
        api_key: str | None,
    ) -> ConnectionResult:
        del base_url, api_key
        return ConnectionResult(
            "UNAVAILABLE",
            f"{descriptor.display_name} is metadata only; a native adapter is required",
        )


def adapter_for(descriptor: ProviderDescriptor) -> ProviderAdapter:
    if descriptor.adapter in {"openai_compatible", "github_models"}:
        return OpenAICompatibleAdapter()
    return MetadataOnlyAdapter()


def _endpoint_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _http_error(error: HTTPError) -> ConnectionResult:
    if error.code in {401, 403}:
        return ConnectionResult("AUTHENTICATION_ERROR", f"HTTP {error.code}")
    if error.code == 429:
        return ConnectionResult("RATE_LIMITED", "HTTP 429")
    if error.code == 404:
        return ConnectionResult("INVALID_MODEL", "Model endpoint returned HTTP 404")
    if error.code in {502, 503, 504}:
        return ConnectionResult("UNAVAILABLE", f"HTTP {error.code}")
    return ConnectionResult("NETWORK_ERROR", f"HTTP {error.code}")


def _parse_models(payload: Any) -> list[ModelMetadata]:
    rows = payload.get("data", []) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    models: list[ModelMetadata] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        model_id = str(row.get("id") or row.get("name") or "")
        if not model_id:
            continue
        limits = row.get("limits") if isinstance(row.get("limits"), dict) else {}
        capabilities = set(row.get("capabilities", []))
        models.append(
            ModelMetadata(
                model_id=model_id,
                display_name=str(row.get("name") or row.get("display_name") or model_id),
                context_length=_integer(
                    row.get("context_length") or limits.get("max_input_tokens")
                ),
                input_modalities=tuple(row.get("supported_input_modalities") or ["text"]),
                output_modalities=tuple(row.get("supported_output_modalities") or ["text"]),
                tool_calling="tool-calling" in capabilities if capabilities else None,
                vision="image" in (row.get("supported_input_modalities") or []),
                reasoning="reasoning" in capabilities if capabilities else None,
                availability=str(row.get("availability") or "available"),
            )
        )
    return models


def _integer(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
