"""Typed asynchronous access to the local Docker Engine API over its Unix socket."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import quote

import httpx


class DockerEngineError(RuntimeError):
    def __init__(self, operation: str, *, status_code: int | None = None):
        super().__init__(f"Docker Engine operation failed: {operation}")
        self.operation = operation
        self.status_code = status_code


class DockerNotFound(DockerEngineError):
    """The Engine explicitly returned HTTP 404 for an object lookup."""


class DockerConflict(DockerEngineError):
    """The Engine explicitly rejected an operation due to object state."""


@dataclass(frozen=True)
class ImageIdentity:
    reference: str
    image_id: str
    labels: dict[str, str]


@dataclass(frozen=True)
class DockerResource:
    resource_type: Literal["container", "volume"]
    resource_id: str
    labels: dict[str, str]
    running: bool | None = None
    health: str | None = None
    exit_code: int | None = None
    status: str | None = None


class DockerEngine:
    """Small Docker API surface; callers never construct URLs or shell commands."""

    def __init__(
        self,
        socket_path: str = "/var/run/docker.sock",
        *,
        timeout_seconds: float = 10,
        client: httpx.AsyncClient | None = None,
    ):
        if client is None:
            transport = httpx.AsyncHTTPTransport(uds=socket_path)
            client = httpx.AsyncClient(
                transport=transport,
                base_url="http://docker",
                timeout=httpx.Timeout(timeout_seconds),
                trust_env=False,
            )
        self._client = client
        self._api_prefix: str | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def initialize(self) -> str:
        response = await self._raw("GET", "/version", versioned=False)
        payload = self._json(response, "version")
        api_version = str(payload.get("ApiVersion", ""))
        components = api_version.split(".")
        if len(components) != 2 or not all(item.isdigit() for item in components):
            raise DockerEngineError("version")
        self._api_prefix = f"/v{api_version}"
        return api_version

    async def _raw(
        self,
        method: str,
        path: str,
        *,
        versioned: bool = True,
        expected: tuple[int, ...] = (200,),
        **kwargs: Any,
    ) -> httpx.Response:
        if versioned:
            if self._api_prefix is None:
                raise DockerEngineError("engine_not_initialized")
            path = self._api_prefix + path
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise DockerEngineError(f"{method} {path}") from exc
        if response.status_code == 404:
            raise DockerNotFound(f"{method} {path}", status_code=404)
        if response.status_code == 409:
            raise DockerConflict(f"{method} {path}", status_code=409)
        if response.status_code not in expected:
            raise DockerEngineError(f"{method} {path}", status_code=response.status_code)
        return response

    @staticmethod
    def _json(response: httpx.Response, operation: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise DockerEngineError(operation, status_code=response.status_code) from exc
        if not isinstance(payload, dict):
            raise DockerEngineError(operation, status_code=response.status_code)
        return payload

    async def inspect_image(self, reference: str) -> ImageIdentity:
        response = await self._raw("GET", f"/images/{quote(reference, safe='')}/json")
        payload = self._json(response, "inspect_image")
        image_id = str(payload.get("Id", ""))
        if not image_id.startswith("sha256:"):
            raise DockerEngineError("inspect_image")
        labels = payload.get("Config", {}).get("Labels") or {}
        if not isinstance(labels, dict):
            raise DockerEngineError("inspect_image")
        return ImageIdentity(reference=reference, image_id=image_id, labels=dict(labels))

    async def create_volume(self, *, name: str, labels: dict[str, str]) -> str:
        response = await self._raw(
            "POST", "/volumes/create", expected=(201,), json={"Name": name, "Labels": labels}
        )
        payload = self._json(response, "create_volume")
        created = str(payload.get("Name", ""))
        if created != name:
            raise DockerEngineError("create_volume")
        return created

    async def inspect_volume(self, name: str) -> DockerResource:
        response = await self._raw("GET", f"/volumes/{quote(name, safe='')}")
        payload = self._json(response, "inspect_volume")
        labels = payload.get("Labels") or {}
        return DockerResource("volume", str(payload.get("Name", name)), dict(labels))

    async def remove_volume(self, name: str) -> None:
        await self._raw(
            "DELETE", f"/volumes/{quote(name, safe='')}", expected=(204,), params={"force": "1"}
        )

    async def create_container(
        self, *, name: str, image_id: str, labels: dict[str, str], spec: dict[str, Any]
    ) -> str:
        payload = {**spec, "Image": image_id, "Labels": labels}
        response = await self._raw(
            "POST",
            "/containers/create",
            expected=(201,),
            params={"name": name},
            json=payload,
        )
        result = self._json(response, "create_container")
        container_id = str(result.get("Id", ""))
        if not container_id:
            raise DockerEngineError("create_container")
        return container_id

    async def start_container(self, container_id: str) -> None:
        await self._raw(
            "POST", f"/containers/{quote(container_id, safe='')}/start", expected=(204, 304)
        )

    async def inspect_container(self, container_id: str) -> DockerResource:
        response = await self._raw("GET", f"/containers/{quote(container_id, safe='')}/json")
        payload = self._json(response, "inspect_container")
        state = payload.get("State") or {}
        health_value = state.get("Health", {}).get("Status")
        return DockerResource(
            "container",
            str(payload.get("Id", container_id)),
            dict(payload.get("Config", {}).get("Labels") or {}),
            running=bool(state.get("Running")),
            health=str(health_value) if health_value is not None else None,
            exit_code=int(state.get("ExitCode", 0)) if not state.get("Running") else None,
            status=str(state.get("Status")) if state.get("Status") is not None else None,
        )

    async def wait_container(self, container_id: str, *, timeout_seconds: float) -> int:
        try:
            async with asyncio.timeout(timeout_seconds):
                response = await self._raw(
                    "POST",
                    f"/containers/{quote(container_id, safe='')}/wait",
                    params={"condition": "not-running"},
                )
        except TimeoutError as exc:
            raise DockerEngineError("wait_container") from exc
        payload = self._json(response, "wait_container")
        try:
            return int(payload["StatusCode"])
        except (KeyError, TypeError, ValueError) as exc:
            raise DockerEngineError("wait_container") from exc

    async def remove_container(self, container_id: str) -> None:
        await self._raw(
            "DELETE",
            f"/containers/{quote(container_id, safe='')}",
            expected=(204,),
            params={"force": "1", "v": "0"},
        )

    async def wait_healthy(
        self, container_id: str, *, timeout_seconds: float, poll_seconds: float = 0.2
    ) -> DockerResource:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while True:
            resource = await self.inspect_container(container_id)
            if resource.health == "healthy":
                return resource
            if not resource.running or resource.health == "unhealthy":
                raise DockerEngineError("container_health")
            if asyncio.get_running_loop().time() >= deadline:
                raise DockerEngineError("container_health_timeout")
            await asyncio.sleep(poll_seconds)

    async def list_resources(self, *, labels: dict[str, str]) -> list[DockerResource]:
        filters = json.dumps({"label": [f"{key}={value}" for key, value in labels.items()]})
        containers_response = await self._raw(
            "GET", "/containers/json", params={"all": "1", "filters": filters}
        )
        volumes_response = await self._raw("GET", "/volumes", params={"filters": filters})
        try:
            container_rows = containers_response.json()
            volume_rows = volumes_response.json().get("Volumes") or []
        except (json.JSONDecodeError, AttributeError) as exc:
            raise DockerEngineError("list_resources") from exc
        if not isinstance(container_rows, list) or not isinstance(volume_rows, list):
            raise DockerEngineError("list_resources")
        resources = [
            DockerResource(
                "container",
                str(row.get("Id", "")),
                dict(row.get("Labels") or {}),
                running=str(row.get("State", "")) == "running",
                status=str(row.get("State", "")) or None,
            )
            for row in container_rows
        ]
        resources.extend(
            DockerResource("volume", str(row.get("Name", "")), dict(row.get("Labels") or {}))
            for row in volume_rows
        )
        return resources
