from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from economia_artificial.model_router import ModelRequirements, ModelRouter
from economia_artificial.openai_provider import (
    OpenAIChatCompletionsDecisionProvider,
    OpenAIResponsesDecisionProvider,
)
from economia_artificial.provider_adapters import (
    ConnectionResult,
    ModelMetadata,
    adapter_for,
)
from economia_artificial.provider_catalog import ProviderDescriptor, provider_catalog
from economia_artificial.runtime import DecisionProvider
from economia_artificial.secret_store import LocalSecretStore


@dataclass(slots=True)
class ProviderCapability:
    context_window: int | None = None
    supports_embeddings: bool = False
    supports_reasoning: bool = False
    supports_vision: bool = False
    supports_tools: bool = True
    supports_streaming: bool = True


@dataclass(slots=True)
class ProviderConfig:
    id: str
    provider: str
    base_url: str | None
    model: str
    enabled: bool = True
    api_key_environment: str | None = None
    capability: ProviderCapability = field(default_factory=ProviderCapability)
    quota: dict[str, Any] = field(default_factory=dict)
    pricing: dict[str, Any] = field(default_factory=dict)
    models: list[ModelMetadata] = field(default_factory=list)
    health_status: str = "NOT_CONFIGURED"
    last_checked: str | None = None

    def public_view(
        self,
        descriptor: ProviderDescriptor,
        api_key: str | None,
        credential_source: str | None,
    ) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "display_name": descriptor.display_name,
            "environment_variable": descriptor.environment_variable,
            "base_url": self.base_url,
            "model": self.model,
            "enabled": self.enabled,
            "api_key_configured": bool(api_key),
            "api_key_masked": _mask_secret(api_key) if api_key else None,
            "credential_source": credential_source,
            "api_key_environment": self.api_key_environment,
            "capability": asdict(self.capability),
            "quota": self.quota,
            "pricing": self.pricing,
            "models": [model.public_view() for model in self.models],
            "model_count": len(self.models),
            "health_status": self.health_status,
            "last_checked": self.last_checked,
            "descriptor": descriptor.public_view(),
        }


class ProviderRegistry:
    """Durable provider metadata plus ephemeral API keys and reusable drivers."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._catalog = provider_catalog()
        self._providers: dict[str, ProviderConfig] = {}
        self._runtime_secrets: dict[str, str] = {}
        self._secret_store = LocalSecretStore(path.with_name(f"{path.stem}.secrets.json"))
        self.router = ModelRouter()
        self._load()

    def catalog(self) -> list[dict[str, Any]]:
        catalog = []
        for descriptor in self._catalog.values():
            configurations = [
                provider
                for provider in self._providers.values()
                if provider.provider == descriptor.id
            ]
            catalog.append(
                {
                    **descriptor.public_view(),
                    "configured": bool(configurations),
                    "enabled": any(provider.enabled for provider in configurations),
                    "configuration_count": len(configurations),
                }
            )
        return catalog

    def list(self) -> list[dict[str, Any]]:
        return [
            config.public_view(
                self._descriptor(config.provider),
                self._api_key(config),
                self._credential_source(config),
            )
            for config in self._providers.values()
        ]

    def public_view(self, provider_id: str) -> dict[str, Any]:
        config = self._require(provider_id)
        return config.public_view(
            self._descriptor(config.provider),
            self._api_key(config),
            self._credential_source(config),
        )

    def model_for(self, provider_id: str) -> str:
        config = self._require_enabled(provider_id)
        if not config.model:
            raise RuntimeError("Select or enter a model before assigning this provider")
        return config.model

    def route_for_agent(
        self,
        granted_provider_ids: frozenset[str],
        requirements: ModelRequirements | None = None,
    ) -> str:
        return self.router.select(self.list(), granted_provider_ids, requirements)

    def configure(
        self,
        *,
        provider: str,
        model: str = "",
        base_url: str | None = None,
        api_key: str | None = None,
        api_key_environment: str | None = None,
        enabled: bool = True,
        config_id: str | None = None,
        quota: dict[str, Any] | None = None,
        pricing: dict[str, Any] | None = None,
    ) -> ProviderConfig:
        descriptor = self._descriptor(provider)
        existing = self._providers.get(config_id) if config_id else None
        if config_id and existing is None:
            raise ValueError("Unknown provider configuration")
        if existing and existing.provider != provider:
            raise ValueError("A configured provider type cannot be changed")
        config = ProviderConfig(
            id=existing.id if existing else str(uuid4()),
            provider=provider,
            base_url=(base_url or descriptor.default_base_url or None).rstrip("/")
            if (base_url or descriptor.default_base_url)
            else None,
            model=model.strip(),
            enabled=_as_bool(enabled),
            api_key_environment=api_key_environment or descriptor.environment_variable,
            capability=_capability_from(descriptor, existing.capability if existing else None),
            quota=quota if quota is not None else (existing.quota if existing else {}),
            pricing=pricing if pricing is not None else (existing.pricing if existing else {}),
            models=existing.models if existing else [],
            health_status=existing.health_status if existing else "NOT_CONFIGURED",
            last_checked=existing.last_checked if existing else None,
        )
        if api_key:
            stored = self._secret_store.set(config.id, api_key)
            if self._secret_store.available and not stored:
                raise RuntimeError("Windows DPAPI could not protect the provider credential")
            self._runtime_secrets[config.id] = api_key
        self._providers[config.id] = config
        self._persist()
        return config

    def test_connection(self, provider_id: str) -> dict[str, Any]:
        config = self._require(provider_id)
        descriptor = self._descriptor(config.provider)
        result = adapter_for(descriptor).inspect(descriptor, config.base_url, self._api_key(config))
        self._record_result(config, result)
        return result.public_view()

    def refresh_models(self, provider_id: str) -> dict[str, Any]:
        return self.test_connection(provider_id)

    def add_manual_model(self, provider_id: str, model: dict[str, Any]) -> dict[str, Any]:
        config = self._require(provider_id)
        model_id = str(model.get("model_id") or "").strip()
        if not model_id:
            raise ValueError("model_id is required")
        metadata = ModelMetadata(
            model_id=model_id,
            display_name=str(model.get("display_name") or model_id),
            context_length=_optional_int(model.get("context_length")),
            input_modalities=tuple(model.get("input_modalities") or ["text"]),
            output_modalities=tuple(model.get("output_modalities") or ["text"]),
            tool_calling=_optional_bool(model.get("tool_calling")),
            vision=_optional_bool(model.get("vision")),
            reasoning=_optional_bool(model.get("reasoning")),
            availability=str(model.get("availability") or "manual"),
        )
        config.models = [item for item in config.models if item.model_id != metadata.model_id]
        config.models.append(metadata)
        if not config.model:
            config.model = metadata.model_id
        self._persist()
        return metadata.public_view()

    def resolve(self, provider_id: str) -> DecisionProvider:
        config = self._require_enabled(provider_id)
        descriptor = self._descriptor(config.provider)
        if descriptor.adapter not in {"openai_compatible", "github_models"}:
            raise RuntimeError(
                f"{descriptor.display_name} needs a native adapter before agents can use it"
            )
        if descriptor.adapter == "github_models":
            raise RuntimeError(
                "GitHub Models supports catalog discovery; its agent transport needs a "
                "dedicated adapter"
            )
        if not config.base_url:
            raise RuntimeError("Configure a base URL before starting an agent")
        api_key = self._api_key(config)
        if descriptor.authentication_type == "bearer" and not api_key:
            raise RuntimeError("Configure an API key in the process or an environment variable")
        if not config.model:
            raise RuntimeError("Select a model before starting an agent")
        from openai import OpenAI

        client = OpenAI(api_key=api_key or "local", base_url=config.base_url)
        if descriptor.api_style == "responses":
            return OpenAIResponsesDecisionProvider(client, config.model)
        if not descriptor.chat_endpoint:
            raise RuntimeError(f"{descriptor.display_name} has no agent-compatible chat endpoint")
        return OpenAIChatCompletionsDecisionProvider(client, config.model)

    @staticmethod
    def scan_local_models(timeout_seconds: float = 0.4) -> list[dict[str, Any]]:
        targets = [
            ("ollama_local", "Ollama", "http://127.0.0.1:11434/api/tags", "ollama"),
            ("lm_studio", "LM Studio", "http://127.0.0.1:1234/v1/models", "openai"),
            ("vllm", "vLLM", "http://127.0.0.1:8000/v1/models", "openai"),
            ("llama_cpp", "llama.cpp", "http://127.0.0.1:8080/v1/models", "openai"),
        ]
        found = []
        for provider_id, name, url, format_name in targets:
            try:
                request = Request(url, headers={"User-Agent": "EconomiaArtificial/0.4 local-scan"})
                with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
                    payload = json.loads(response.read())
                found.append(
                    {
                        "provider_id": provider_id,
                        "provider": name,
                        "url": url,
                        "status": "CONNECTED",
                        "models": _models_from_payload(payload, format_name),
                        "local_or_cloud": "local",
                        "pricing_model": "free",
                    }
                )
            except (URLError, OSError, TimeoutError, json.JSONDecodeError):
                continue
        return found

    def _record_result(self, config: ProviderConfig, result: ConnectionResult) -> None:
        config.health_status = result.status
        config.last_checked = datetime.now(UTC).isoformat()
        if result.models:
            config.models = list(result.models)
            if not config.model:
                config.model = result.models[0].model_id
        self._persist()

    def _api_key(self, config: ProviderConfig) -> str | None:
        return (
            self._runtime_secrets.get(config.id)
            or self._secret_store.get(config.id)
            or self._environment_key(config)
        )

    def _credential_source(self, config: ProviderConfig) -> str | None:
        if config.id in self._runtime_secrets:
            return "ui_and_windows_dpapi" if self._secret_store.has(config.id) else "ui_memory"
        if self._secret_store.has(config.id):
            return "windows_dpapi"
        return "environment" if self._environment_key(config) else None

    def _environment_key(self, config: ProviderConfig) -> str | None:
        variable = (
            config.api_key_environment or self._descriptor(config.provider).environment_variable
        )
        return os.environ.get(variable) if variable else None

    def _descriptor(self, provider: str) -> ProviderDescriptor:
        descriptor = self._catalog.get(provider)
        if descriptor is None:
            raise ValueError(f"Unsupported provider: {provider}")
        return descriptor

    def _require(self, provider_id: str) -> ProviderConfig:
        config = self._providers.get(provider_id)
        if config is None:
            raise RuntimeError("Provider configuration is unavailable")
        return config

    def _require_enabled(self, provider_id: str) -> ProviderConfig:
        config = self._require(provider_id)
        if not config.enabled:
            raise RuntimeError("Provider is unavailable or disabled")
        return config

    def _load(self) -> None:
        if not self._path.exists():
            return
        for raw in json.loads(self._path.read_text(encoding="utf-8")):
            if raw.get("provider") not in self._catalog:
                continue
            raw["capability"] = ProviderCapability(**raw.get("capability", {}))
            raw["models"] = [ModelMetadata(**model) for model in raw.get("models", [])]
            raw.setdefault("health_status", "NOT_CONFIGURED")
            raw.setdefault("last_checked", None)
            self._providers[raw["id"]] = ProviderConfig(**raw)

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = [asdict(provider) for provider in self._providers.values()]
        temporary_path = self._path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary_path.replace(self._path)


def _capability_from(
    descriptor: ProviderDescriptor, existing: ProviderCapability | None
) -> ProviderCapability:
    if existing:
        return existing
    return ProviderCapability(
        supports_embeddings=descriptor.embeddings_support,
        supports_reasoning=descriptor.reasoning_support,
        supports_vision=descriptor.vision_support,
        supports_tools=descriptor.tool_calling_support,
        supports_streaming=descriptor.streaming_support,
    )


def _models_from_payload(payload: dict[str, Any], format_name: str) -> list[str]:
    if format_name == "ollama":
        return [model.get("name", "unknown") for model in payload.get("models", [])]
    return [model.get("id", "unknown") for model in payload.get("data", [])]


def _mask_secret(value: str) -> str:
    return f"••••••••{value[-4:]}" if len(value) >= 4 else "••••••••"


def _as_bool(value: object) -> bool:
    return value if isinstance(value, bool) else str(value).lower() not in {"false", "0", "no"}


def _optional_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None
