import json

import httpx
import pytest

from app.browser_tasks.docker_runtime import (
    DockerEngine,
    DockerEngineError,
    DockerNotFound,
)


@pytest.mark.asyncio
async def test_engine_uses_negotiated_api_and_structured_image_identity():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/version":
            return httpx.Response(200, json={"ApiVersion": "1.52"})
        if request.url.path.startswith("/v1.52/images/"):
            return httpx.Response(
                200,
                json={"Id": "sha256:" + "a" * 64, "Config": {"Labels": {"version": "1"}}},
            )
        raise AssertionError(request.url)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://docker")
    engine = DockerEngine(client=client)
    try:
        assert await engine.initialize() == "1.52"
        image = await engine.inspect_image("reader/browser:test")
        assert image.image_id == "sha256:" + "a" * 64
        assert image.labels == {"version": "1"}
        assert requests[-1].url.path.startswith("/v1.52/images/")
    finally:
        await engine.aclose()


@pytest.mark.asyncio
async def test_only_http_404_is_treated_as_explicit_not_found():
    status = 404

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/version":
            return httpx.Response(200, json={"ApiVersion": "1.52"})
        return httpx.Response(status, json={"message": "failure"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://docker")
    engine = DockerEngine(client=client)
    try:
        await engine.initialize()
        with pytest.raises(DockerNotFound):
            await engine.inspect_container("missing")
        status = 500
        with pytest.raises(DockerEngineError) as caught:
            await engine.inspect_container("unknown")
        assert not isinstance(caught.value, DockerNotFound)
    finally:
        await engine.aclose()


@pytest.mark.asyncio
async def test_list_resources_passes_exact_label_filters_and_rejects_bad_json():
    seen_filters: list[dict] = []
    bad_volumes = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal bad_volumes
        if request.url.path == "/version":
            return httpx.Response(200, json={"ApiVersion": "1.52"})
        seen_filters.append(json.loads(request.url.params["filters"]))
        if request.url.path.endswith("/containers/json"):
            return httpx.Response(
                200,
                json=[{"Id": "container", "Labels": {"managed": "true"}, "State": "running"}],
            )
        if bad_volumes:
            return httpx.Response(200, content=b"not-json")
        return httpx.Response(200, json={"Volumes": [{"Name": "volume", "Labels": {}}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://docker")
    engine = DockerEngine(client=client)
    try:
        await engine.initialize()
        resources = await engine.list_resources(labels={"managed": "true", "deployment": "one"})
        assert [resource.resource_type for resource in resources] == ["container", "volume"]
        assert all(
            set(item["label"]) == {"managed=true", "deployment=one"} for item in seen_filters
        )
        bad_volumes = True
        with pytest.raises(DockerEngineError):
            await engine.list_resources(labels={"managed": "true"})
    finally:
        await engine.aclose()
