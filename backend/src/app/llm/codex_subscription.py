"""Translation-only Codex subscription adapter, adapted from Wandrun's reference.

Uses the subscription Responses endpoint and existing Codex file login. Only a
completed response is published; partial SSE output must never become a cached
translation. Login is imported explicitly; Reader owns refresh and persistence afterwards.
"""

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_function

from app.llm.codex_auth import subscription_headers

CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"


class CodexSubscriptionHTTPError(RuntimeError):
    def __init__(self, status_code: int, retry_after: str | None = None):
        # Never include upstream bodies: they can echo credentials or source text.
        super().__init__(f"Codex subscription HTTP {status_code}")
        self.status_code = status_code
        self.response = httpx.Response(
            status_code, headers={"retry-after": retry_after} if retry_after else {}
        )


class CodexSubscriptionStreamError(RuntimeError):
    """Incomplete, failed or malformed subscription stream."""


class _ResponseStream:
    def __init__(self):
        self.data: list[str] = []

    def feed(self, line: str) -> ChatResult | None:
        line = line.rstrip("\r\n")
        if line.startswith("data:"):
            self.data.append(line[5:].removeprefix(" "))
            if sum(map(len, self.data)) > 2_000_000:
                raise CodexSubscriptionStreamError("Codex event exceeds response limit")
        elif not line and self.data:
            data, self.data = "\n".join(self.data), []
            if data == "[DONE]":
                return None
            try:
                event = json.loads(data)
                kind = event["type"]
                if kind in {"error", "response.failed", "response.incomplete"}:
                    raise CodexSubscriptionStreamError("Codex response did not complete")
                if kind != "response.completed":
                    return None
                response = event["response"]
                if response.get("status") != "completed":
                    raise CodexSubscriptionStreamError("Codex response did not complete")
                texts = []
                for item in response["output"]:
                    if item["type"] != "message" or item.get("phase") == "commentary":
                        continue
                    for part in item["content"]:
                        if part["type"] == "refusal":
                            raise CodexSubscriptionHTTPError(400)
                        if part["type"] == "output_text":
                            texts.append(part["text"])
                content = "".join(texts)
                if not content.strip():
                    raise CodexSubscriptionStreamError("Codex returned no translation")
                return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])
            except (ValueError, KeyError, TypeError, AttributeError):
                raise CodexSubscriptionStreamError("Invalid Codex response event") from None
        return None


class CodexSubscriptionModel(BaseChatModel):
    model_name: str = "gpt-6-luna"
    auth_file: Path
    timeout: float = 60

    @property
    def _llm_type(self) -> str:
        return "codex-subscription"

    def with_structured_output(self, schema, *, method="json_schema", **kwargs):
        if method != "json_schema" or kwargs:
            raise ValueError("Codex translation requires native JSON Schema output")
        spec = convert_to_openai_function(schema, strict=True)
        return self.bind(
            response_format={
                "type": "json_schema",
                "name": spec["name"],
                "schema": spec["parameters"],
                "strict": True,
            }
        ) | PydanticOutputParser(pydantic_object=schema)

    def _build_request(self, messages, stop=None, **kwargs) -> dict[str, Any]:
        if stop or set(kwargs) - {"response_format"}:
            raise ValueError("Unsupported Codex translation options")
        instructions, inputs = [], []
        for message in messages:
            if not isinstance(message.content, str):
                raise ValueError("Codex translation accepts text only")
            if isinstance(message, SystemMessage):
                instructions.append(message.content)
            elif isinstance(message, HumanMessage):
                inputs.append({"role": "user", "content": message.content})
            else:
                raise ValueError("Codex translation accepts system and human messages only")
        body = {
            "model": self.model_name,
            "instructions": "\n".join(instructions) or "You are a precise translation service.",
            "input": inputs,
            "store": False,
            "stream": True,
            "reasoning": {"effort": "low"},
        }
        if response_format := kwargs.get("response_format"):
            body["text"] = {"format": response_format}
        return body

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        body = self._build_request(messages, stop, **kwargs)
        headers = subscription_headers(self.auth_file)
        with httpx.Client(timeout=self.timeout, follow_redirects=False) as client:
            for attempt in range(2):
                with client.stream(
                    "POST", CODEX_RESPONSES_URL, json=body, headers=headers
                ) as response:
                    if response.status_code == 401 and attempt == 0:
                        # Close the failed response before exchanging credentials.
                        rejected = headers["Authorization"].removeprefix("Bearer ")
                    else:
                        if response.status_code != 200:
                            raise CodexSubscriptionHTTPError(
                                response.status_code, response.headers.get("retry-after")
                            )
                        state = _ResponseStream()
                        for line in response.iter_lines():
                            if result := state.feed(line):
                                return result
                        if result := state.feed(""):
                            return result
                        raise CodexSubscriptionStreamError("Codex stream ended before completion")
                headers = subscription_headers(self.auth_file, rejected_access_token=rejected)
        raise CodexSubscriptionHTTPError(401)  # defensive; the second 401 raises above

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        body = self._build_request(messages, stop, **kwargs)
        async with asyncio.timeout(self.timeout):
            # File locking and OAuth exchange happen off the event loop. If this
            # caller is cancelled, an in-progress rotation still persists before
            # releasing its lock, so peers cannot reuse a consumed refresh token.
            headers = await asyncio.to_thread(subscription_headers, self.auth_file)
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
                for attempt in range(2):
                    async with client.stream(
                        "POST", CODEX_RESPONSES_URL, json=body, headers=headers
                    ) as response:
                        if response.status_code == 401 and attempt == 0:
                            rejected = headers["Authorization"].removeprefix("Bearer ")
                        else:
                            if response.status_code != 200:
                                raise CodexSubscriptionHTTPError(
                                    response.status_code, response.headers.get("retry-after")
                                )
                            state = _ResponseStream()
                            async for line in response.aiter_lines():
                                if result := state.feed(line):
                                    return result
                            if result := state.feed(""):
                                return result
                            raise CodexSubscriptionStreamError(
                                "Codex stream ended before completion"
                            )
                    headers = await asyncio.to_thread(
                        subscription_headers, self.auth_file, rejected_access_token=rejected
                    )
        raise CodexSubscriptionHTTPError(401)
