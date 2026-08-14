from __future__ import annotations

import argparse
import json
import os
import threading
from dataclasses import asdict
from decimal import Decimal
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from economia_artificial.governance import Capability, EnvironmentMode
from economia_artificial.memory import JsonMemoryStore
from economia_artificial.persistence import JsonWorldStore
from economia_artificial.providers import ProviderRegistry
from economia_artificial.scheduler import AgentScheduler, JsonRuntimeStore
from economia_artificial.world import AgentResources, EconomyWorld


class LocalControlPlane:
    def __init__(self, data_directory: Path) -> None:
        data_directory.mkdir(parents=True, exist_ok=True)
        self._world_store = JsonWorldStore(data_directory / "world.json")
        self.memory = JsonMemoryStore(data_directory / "memory.json")
        self.world = self._world_store.load(memory_store=self.memory) or EconomyWorld.create(
            seed=42,
            mode=EnvironmentMode.SANDBOX,
            memory_store=self.memory,
        )
        self.providers = ProviderRegistry(data_directory / "providers.json")
        self.scheduler = AgentScheduler(
            self.world,
            self.providers.resolve,
            JsonRuntimeStore(data_directory / "runtime.json"),
            self._save_world,
        )
        self._lock = threading.RLock()

    def start(self) -> None:
        self.scheduler.start()

    def stop(self) -> None:
        self.scheduler.stop()
        self._save_world()

    def _save_world(self) -> None:
        with self._lock:
            self._world_store.save(self.world)

    def status(self) -> dict[str, Any]:
        states = {state.agent_id: state for state in self.scheduler.states()}
        agents = []
        lifecycle_counts: dict[str, int] = {}
        for agent_id, agent in self.world.agents.items():
            state = states.get(agent_id)
            lifecycle = state.lifecycle.value if state else "created"
            lifecycle_counts[lifecycle] = lifecycle_counts.get(lifecycle, 0) + 1
            agents.append(
                {
                    "id": agent_id,
                    "name": agent.name,
                    "lifecycle": lifecycle,
                    "wealth": str(self.world.net_worth(agent_id)),
                    "last_action": state.last_action if state else None,
                    "last_error": state.last_error if state else None,
                    "next_wake_at": state.next_wake_at.isoformat()
                    if state and state.next_wake_at
                    else None,
                    "cycles_completed": state.cycles_completed if state else 0,
                }
            )
        total_wealth = sum(
            (self.world.net_worth(agent_id) for agent_id in self.world.agents), Decimal("0")
        )
        return {
            "mode": self.world.config.mode.value,
            "cycle": self.world.cycle,
            "system": _system_snapshot(),
            "economy": {
                "total_capital": str(total_wealth),
                "total_wealth": str(total_wealth),
                "transactions": len(self.world.ledger.transactions),
                "agents_created": len(self.world.agents),
            },
            "agent_counts": lifecycle_counts,
            "agents": agents,
            "events": [
                {
                    "type": event.event_type,
                    "agent_id": event.agent_id,
                    "entity_id": event.entity_id,
                    "payload": event.payload,
                    "created_at": event.created_at.isoformat(),
                }
                for event in self.world.gateway.events[-50:]
            ],
        }

    def create_agent(self, payload: dict[str, Any]) -> dict[str, Any]:
        provider_id = str(payload["provider_id"])
        self.providers.resolve(provider_id)
        model = self.providers.model_for(provider_id)
        requested_model = str(payload.get("model", model))
        if requested_model != model:
            raise ValueError("The selected model must match the configured provider model")
        resources = AgentResources(
            token_budget=_budget(payload, "token_budget", 5_000_000),
            tool_budget=_budget(payload, "tool_budget", 5_000),
            network_budget=_budget(payload, "network_budget", 10_000, allow_zero=True),
        )
        agent = self.world.create_agent(
            str(payload["name"]),
            payload.get("initial_cash", "100.00"),
            objective=str(payload.get("objective", "maximize_net_worth")),
            model_id=model,
            resources=resources,
        )
        for value in payload.get("capabilities", []):
            self.world.grant(agent.id, Capability(value))
        self.scheduler.register_agent(agent.id, provider_id)
        self._save_world()
        return {"id": agent.id, "name": agent.name}

    def set_capabilities(self, agent_id: str, payload: dict[str, Any]) -> None:
        values = {Capability(value) for value in payload["capabilities"]}
        self.world.policy.restore(
            {
                **self.world.policy.snapshot(),
                agent_id: sorted(capability.value for capability in values),
            }
        )
        self._save_world()

    def agent_detail(self, agent_id: str) -> dict[str, Any]:
        if agent_id not in self.world.agents:
            raise ValueError("Unknown agent")
        state = next(
            (state for state in self.scheduler.states() if state.agent_id == agent_id), None
        )
        return {
            "perception": self.world.perceive(agent_id),
            "runtime": asdict(state) if state else None,
            "memories": [asdict(memory) for memory in self.memory.relevant(agent_id, limit=30)],
            "activities": [
                {
                    "tool": call.tool_name,
                    "result": call.result,
                    "error_code": call.error_code,
                    "created_at": call.created_at.isoformat(),
                }
                for call in self.world.gateway.tool_calls
                if call.agent_id == agent_id
            ][-30:],
        }


class ControlPlaneHandler(BaseHTTPRequestHandler):
    control_plane: LocalControlPlane
    static_directory: Path

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/":
            return self._serve_file("index.html", "text/html; charset=utf-8")
        if path == "/api/status":
            return self._json(HTTPStatus.OK, self.control_plane.status())
        if path == "/api/providers":
            return self._json(HTTPStatus.OK, {"providers": self.control_plane.providers.list()})
        if path == "/api/provider-catalog":
            return self._json(HTTPStatus.OK, {"providers": self.control_plane.providers.catalog()})
        if path == "/api/local-models":
            return self._json(
                HTTPStatus.OK, {"models": self.control_plane.providers.scan_local_models()}
            )
        if path.startswith("/api/agents/"):
            return self._dispatch_agent_get(path)
        self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            payload = self._payload()
            if path == "/api/providers":
                provider = self.control_plane.providers.configure(**payload)
                return self._json(
                    HTTPStatus.CREATED, self.control_plane.providers.public_view(provider.id)
                )
            if path.startswith("/api/providers/"):
                return self._dispatch_provider_post(path, payload)
            if path == "/api/local-models/scan":
                return self._json(
                    HTTPStatus.OK,
                    {"models": self.control_plane.providers.scan_local_models()},
                )
            if path == "/api/agents":
                return self._json(HTTPStatus.CREATED, self.control_plane.create_agent(payload))
            if path == "/api/runtime/start":
                self.control_plane.start()
                return self._json(HTTPStatus.OK, {"status": "running"})
            if path == "/api/runtime/stop":
                self.control_plane.stop()
                return self._json(HTTPStatus.OK, {"status": "stopped"})
            if path.startswith("/api/agents/"):
                return self._dispatch_agent_post(path, payload)
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def _dispatch_agent_get(self, path: str) -> None:
        parts = path.split("/")
        if len(parts) == 4:
            self._json(HTTPStatus.OK, self.control_plane.agent_detail(parts[3]))
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def _dispatch_provider_post(self, path: str, payload: dict[str, Any]) -> None:
        parts = path.split("/")
        if len(parts) != 5:
            return self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
        provider_id, action = parts[3], parts[4]
        if action == "test":
            return self._json(
                HTTPStatus.OK, self.control_plane.providers.test_connection(provider_id)
            )
        if action == "refresh-models":
            return self._json(
                HTTPStatus.OK, self.control_plane.providers.refresh_models(provider_id)
            )
        if action == "models":
            return self._json(
                HTTPStatus.CREATED,
                self.control_plane.providers.add_manual_model(provider_id, payload),
            )
        self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def _dispatch_agent_post(self, path: str, payload: dict[str, Any]) -> None:
        parts = path.split("/")
        if len(parts) != 5:
            return self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
        agent_id, action = parts[3], parts[4]
        if action == "start":
            state = self.control_plane.scheduler.start_agent(agent_id)
        elif action == "pause":
            state = self.control_plane.scheduler.pause_agent(agent_id)
        elif action == "resume":
            state = self.control_plane.scheduler.resume_agent(agent_id)
        elif action == "restart":
            state = self.control_plane.scheduler.restart_agent(agent_id)
        elif action == "capabilities":
            self.control_plane.set_capabilities(agent_id, payload)
            return self._json(HTTPStatus.OK, {"status": "updated"})
        else:
            return self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
        self._json(HTTPStatus.OK, _runtime_state(state))

    def _payload(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length > 16_384:
            raise ValueError("Payload too large")
        raw = self.rfile.read(content_length) if content_length else b"{}"
        return json.loads(raw)

    def _serve_file(self, filename: str, content_type: str) -> None:
        content = (self.static_directory / filename).read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        content = json.dumps(payload, ensure_ascii=False, default=_json_default).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: object) -> None:
        return


def _runtime_state(state: Any) -> dict[str, Any]:
    raw = asdict(state)
    raw["lifecycle"] = state.lifecycle.value
    raw["next_wake_at"] = state.next_wake_at.isoformat() if state.next_wake_at else None
    return raw


def _budget(payload: dict[str, Any], key: str, default: int, *, allow_zero: bool = False) -> int:
    value = int(payload.get(key, default))
    if value < 0 or (value == 0 and not allow_zero):
        raise ValueError(f"{key} must be {'non-negative' if allow_zero else 'positive'}")
    return value


def _system_snapshot() -> dict[str, Any]:
    return {
        "cpu_cores": os.cpu_count() or 0,
        "ram": _memory_snapshot(),
        "network": "loopback-only; external write capabilities disabled",
    }


def _memory_snapshot() -> dict[str, int] | None:
    if os.name != "nt":
        return None
    import ctypes

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return {
        "total_mb": int(status.ullTotalPhys / 1_048_576),
        "available_mb": int(status.ullAvailPhys / 1_048_576),
        "used_percent": int(status.dwMemoryLoad),
    }


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    return str(value)


def main() -> None:
    parser = argparse.ArgumentParser(description="Economia Artificial local control plane")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8787, type=int)
    parser.add_argument("--data-dir", default=".economia-artificial-data")
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("The control plane is intentionally restricted to loopback hosts")
    static_directory = Path(__file__).parent / "static"
    control_plane = LocalControlPlane(Path(args.data_dir))
    ControlPlaneHandler.control_plane = control_plane
    ControlPlaneHandler.static_directory = static_directory
    control_plane.start()
    server = ThreadingHTTPServer((args.host, args.port), ControlPlaneHandler)
    print(f"Economia Artificial available at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        control_plane.stop()


if __name__ == "__main__":
    main()
