import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel

from reader.core.errors import AppError, error_responses, install_error_handlers


def _app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)

    class Login(BaseModel):
        email: str
        password: str

    @app.get("/app-error")
    async def app_error():
        raise AppError(429, "board_rate_limited", "Slow down.", retry_after=30)

    @app.get("/plain-error")
    async def plain_error():
        raise AppError(404, "article_not_found", "No such article.")

    @app.post("/login")
    async def login(body: Login):
        return {"ok": True}

    @app.get("/boom")
    async def boom():
        raise RuntimeError("secret internals")

    return app


async def _call(method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=_app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


@pytest.mark.asyncio
async def test_app_error_has_code_message_and_retry_after() -> None:
    response = await _call("GET", "/app-error")

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "30"
    assert response.json() == {
        "error": {"code": "board_rate_limited", "message": "Slow down.", "retry_after": 30}
    }


@pytest.mark.asyncio
async def test_error_without_retry_omits_the_field() -> None:
    response = await _call("GET", "/plain-error")

    assert response.status_code == 404
    assert "Retry-After" not in response.headers
    assert response.json() == {
        "error": {"code": "article_not_found", "message": "No such article."}
    }


@pytest.mark.asyncio
async def test_validation_error_lists_fields_without_echoing_inputs() -> None:
    response = await _call("POST", "/login", json={"email": "me@example.com", "password": 123})

    body = response.json()
    assert response.status_code == 422
    assert body["error"]["code"] == "invalid_request"
    assert body["error"]["fields"] == [
        {"loc": ["body", "password"], "msg": "Input should be a valid string"}
    ]
    assert "123" not in response.text


@pytest.mark.asyncio
async def test_unknown_route_uses_the_same_shape() -> None:
    response = await _call("GET", "/nowhere")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_unexpected_exception_hides_internals() -> None:
    response = await _call("GET", "/boom")

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": "An unexpected error occurred."}
    }
    assert "secret" not in response.text


def test_error_responses_group_codes_by_status_and_access() -> None:
    signed_in = error_responses((404, "article_not_found"), (404, "source_not_found"))
    public = error_responses((503, "not_ready"), access="public")

    assert sorted(signed_in) == [401, 404, 429]
    assert "`not_authenticated`" in signed_in[401]["description"]
    assert "`article_not_found`, `source_not_found`" in signed_in[404]["description"]
    assert sorted(public) == [503]
