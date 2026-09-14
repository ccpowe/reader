"""Real SQL + LangChain + scanner loop; model, exploration transport and HTTP are controlled."""

import os
from copy import deepcopy
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from sqlalchemy import select, text
from test_candidate_recovery_postgres import _database

from app.core.settings import Settings
from app.domain.enums import SourceKind
from app.ingestion import web_crawl
from app.ingestion.models import SourceScanRequest
from app.ingestion.source_identity import canonical_web_identity
from app.ingestion.sources.web_blog import WebBlogSourceAdapter, WebBlogSourceConfig
from app.ingestion.web_rules import parse_web_rule
from app.services.subscriptions import subscribe_to_shared_source
from app.storage.models import Content, FeedSource, SourceSyncState, WebRuleJob
from app.web_rule_agent import cli_browser
from app.workers import candidates, sync
from app.workers.web_rules import run_web_rule_author_once

URL = "https://example.com/feed"


def _rule(selector: str) -> dict:
    return {
        "id": "e2e-author",
        "hosts": ["example.com"],
        "index_paths": ["/feed"],
        "listing": {
            "extraction": {
                "baseSelector": selector,
                "fields": [
                    {"name": "url", "type": "attribute", "selector": "a", "attribute": "href"},
                    {"name": "title", "type": "text", "selector": "h2"},
                ],
            }
        },
    }


class FeedbackModel(BaseChatModel):
    """Emits a wrong selector, reads real rejection, then submits a real receipt."""

    rule: dict
    calls: int = 0
    saw_rejection: bool = False
    early_stop: bool = False

    @property
    def _llm_type(self):
        return "controlled-rule-feedback"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        import json

        self.calls += 1
        if self.early_stop and self.calls == 1:
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content="I inspected the source; next I will write the rule.",
                            usage_metadata={
                                "input_tokens": 100,
                                "output_tokens": 50,
                                "total_tokens": 150,
                            },
                        )
                    )
                ]
            )
        step = self.calls - int(self.early_stop)
        tool_messages = [message for message in messages if isinstance(message, ToolMessage)]
        if step == 1:
            name, args = "web_rule_schema", {}
        elif step == 2:
            wrong = deepcopy(self.rule)
            wrong["listing"]["extraction"]["baseSelector"] = ".does-not-exist"
            name, args = "execute_web_rule", {"raw_rule": wrong}
        elif step == 3:
            feedback = json.loads(tool_messages[-1].content)
            assert feedback["status"] == "error", feedback
            self.saw_rejection = True
            name, args = "execute_web_rule", {"raw_rule": self.rule}
        elif step == 4:
            feedback = json.loads(tool_messages[-1].content)
            assert feedback["status"] == "completed", feedback
            assert feedback["execution_id"]
            name, args = (
                "FinalWebRule",
                {
                    "rule": self.rule,
                    "execution_id": feedback["execution_id"],
                    "assessment": "Titles and URLs match the fixture DOM.",
                    "limitations": ["History unverified"],
                },
            )
        else:
            pytest.fail("Agent called the model again after trusted submission")
        reply = AIMessage(
            content="",
            tool_calls=[{"id": f"call-{self.calls}", "name": name, "args": args}],
            usage_metadata={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        )
        return ChatResult(generations=[ChatGeneration(message=reply)])


@pytest.mark.postgres
@pytest.mark.parametrize("early_stop", [False, True])
async def test_subscribe_author_sync_new_item_and_structural_repair(monkeypatch, early_stop):
    page = {"html": '<article><a href="/one"><h2>One</h2></a></article>'}
    target_selector = {"value": "article"}
    models = []
    fetched = []

    async def fetch(_client, url, **kwargs):
        fetched.append(url)
        body = "User-agent: *\nAllow: /" if url.endswith("robots.txt") else page["html"]
        return httpx.Response(200, text=body, request=httpx.Request("GET", url))

    original_crawl = web_crawl.crawl_page

    async def crawl_with_controlled_http(*args, **kwargs):
        kwargs["fetch"] = fetch
        return await original_crawl(*args, **kwargs)

    monkeypatch.setattr(web_crawl, "crawl_page", crawl_with_controlled_http)

    class FixtureCLI:
        def __init__(self, settings, source_url, **kwargs):
            self.url = source_url

        async def command(self, argv, *, observed_urls=()):
            assert argv == ["open", self.url]
            response = await fetch(None, self.url)
            return {
                "status": "ok",
                "url": self.url,
                "page_revision": "fixture",
                "format": "html",
                "output": response.text,
                "error": None,
            }

        async def aclose(self):
            pass

    monkeypatch.setattr(cli_browser, "CLIBrowserSession", FixtureCLI)
    monkeypatch.setattr("app.ingestion.sources.web_blog.safe_get", fetch)
    monkeypatch.setattr(candidates, "_enqueue_default_title_translation", AsyncMock())

    def model_factory(*args, **kwargs):
        model = FeedbackModel(rule=_rule(target_selector["value"]), early_stop=early_stop)
        models.append(model)
        return model

    settings = Settings(
        _env_file=None,
        web_rule_agent_engine_id="deepseek-v4-flash",
        deepseek_api_key="test-only-no-provider-requests",
    )
    identity = canonical_web_identity(URL)
    user_id = uuid4()
    async with _database() as (factory, source_id):
        try:
            async with factory() as session:
                await session.execute(
                    text("INSERT INTO profiles (id) VALUES (:id)"),
                    {"id": user_id},
                )
                source = await session.get(FeedSource, source_id)
                source.kind = SourceKind.WEB
                source.canonical_key = identity.canonical_key
                # This suite isolates the Agent loop after a negative RSS probe.
                source.config = {
                    "resolve_site_avatar": False,
                    "web_feed_discovery": {
                        "version": 1,
                        "status": "not_found",
                        "source_url": source.canonical_url,
                    },
                }
                await session.commit()

            async with factory() as session:
                await subscribe_to_shared_source(
                    session,
                    user_id=user_id,
                    identity=identity,
                    display_name="Example",
                    folder_name=None,
                )
            async with factory() as session:
                initial_job = await session.scalar(
                    select(WebRuleJob).where(WebRuleJob.source_id == source_id)
                )
                assert initial_job.status == "queued"
                assert (await session.get(SourceSyncState, source_id)).next_scan_at is None

            assert (
                await run_web_rule_author_once(
                    factory, settings=settings, model_factory=model_factory
                )
                == 1
            )
            async with factory() as session:
                job = await session.get(WebRuleJob, initial_job.id)
                assert job.status == "succeeded", (job.last_error_code, job.diagnostics)
                assert job.model_calls_reserved == 4 + int(early_stop)
                assert job.goal_resumptions_reserved == int(early_stop)
                assert job.validation_calls_reserved == 2
                assert job.total_tokens == 600 + 150 * int(early_stop)
                source = await session.get(FeedSource, source_id)
                assert source.config["web_rule_revision"] == 1
            assert models[0].saw_rejection

            async def scan():
                async with factory() as session:
                    state = await session.get(SourceSyncState, source_id)
                    state.next_scan_at = datetime.now(UTC)
                    await session.commit()
                async with factory() as session:
                    claims = await sync.claim_due_sources(session, limit=100)
                claim = next(item for item in claims if item.source_id == source_id)
                await sync.scan_claimed_source(factory, claim)
                await candidates.process_candidates(factory)

            await scan()
            await scan()
            assert len(models) == 1
            page["html"] = '<article><a href="/two"><h2>Two</h2></a></article>' + page["html"]
            await scan()
            async with factory() as session:
                contents = list(
                    await session.scalars(
                        select(Content).where(Content.authority_source_id == source_id)
                    )
                )
                assert {item.title for item in contents} == {"One", "Two"}
                assert all(not item.body_html for item in contents)
            assert len(models) == 1

            page["html"] = '<section class="item"><a href="/three"><h2>Three</h2></a></section>'
            target_selector["value"] = "section.item"
            await scan()
            async with factory() as session:
                state = await session.get(SourceSyncState, source_id)
                assert state.web_rule_structural_failures == 1
                assert (
                    list(
                        await session.scalars(
                            select(WebRuleJob).where(
                                WebRuleJob.source_id == source_id, WebRuleJob.reason == "repair"
                            )
                        )
                    )
                    == []
                )
            await scan()
            assert (
                await run_web_rule_author_once(
                    factory, settings=settings, model_factory=model_factory
                )
                == 1
            )
            async with factory() as session:
                repair = await session.scalar(
                    select(WebRuleJob).where(
                        WebRuleJob.source_id == source_id, WebRuleJob.reason == "repair"
                    )
                )
                assert repair.status == "succeeded", (repair.last_error_code, repair.diagnostics)
                assert repair.validation_calls_reserved == 3  # old-rule preflight + two candidates
                assert (await session.get(FeedSource, source_id)).config["web_rule_revision"] == 2
            await scan()
            await scan()
            assert len(models) == 2 and all(model.saw_rejection for model in models)
            async with factory() as session:
                titles = set(
                    await session.scalars(
                        select(Content.title).where(Content.authority_source_id == source_id)
                    )
                )
                assert titles == {"One", "Two", "Three"}
            assert set(fetched) <= {URL, "https://example.com/robots.txt"}
        finally:
            async with factory() as session:
                await session.execute(text("DELETE FROM profiles WHERE id=:id"), {"id": user_id})
                await session.commit()


@pytest.mark.postgres
@pytest.mark.live_provider
@pytest.mark.skipif(
    os.environ.get("READER_RUN_LIVE_WEB_RULE_AGENT") != "1",
    reason="explicit opt-in required for live provider usage on the guarded disposable database",
)
async def test_live_provider_authors_a_rule_and_model_free_replay():
    """Opt-in acceptance evidence; never connects to the configured application database."""
    import asyncio
    import json
    from pathlib import Path

    from langchain_core.callbacks import BaseCallbackHandler

    from app.ingestion.url_safety import PublicAsyncClient
    from app.web_rule_agent import PROMPT_VERSION, VALIDATOR_VERSION
    from app.web_rule_agent.model import build_rule_chat_model

    source_url = os.environ.get("READER_WEB_RULE_LIVE_SOURCE_URL", "https://blog.cloudflare.com/")
    identity = canonical_web_identity(source_url)
    engine_id = os.environ.get("READER_WEB_RULE_LIVE_ENGINE_ID", "deepseek-v4-flash")
    settings = Settings(web_rule_agent_engine_id=engine_id)
    user_id = uuid4()
    evidence_path = Path(
        os.environ.get("READER_WEB_RULE_EVIDENCE_PATH", "/tmp/reader-web-rule-agent-live.json")
    )
    evidence = {
        "source_url": source_url,
        "started_at": datetime.now(UTC).isoformat(),
        "prompt_version": PROMPT_VERSION,
        "validator_version": VALIDATOR_VERSION,
        "limits": {
            "total_tokens": settings.web_rule_agent_max_total_tokens,
            "model_calls": settings.web_rule_agent_max_model_calls,
        },
        "model_turns": [],
    }

    class CaptureModelTurns(BaseCallbackHandler):
        """Opt-in experiment evidence; capture replies, never requests or credentials."""

        def on_llm_end(self, response, **kwargs):
            for generation in response.generations:
                for result in generation:
                    message = getattr(result, "message", None)
                    if isinstance(message, AIMessage):
                        evidence["model_turns"].append(
                            {
                                "content": message.content,
                                "tool_calls": message.tool_calls,
                                "invalid_tool_calls": message.invalid_tool_calls,
                                "finish_reason": message.response_metadata.get("finish_reason"),
                                "usage": message.usage_metadata,
                            }
                        )

    def model_factory(*args, **kwargs):
        model = build_rule_chat_model(*args, **kwargs)
        model.callbacks = [CaptureModelTurns()]
        return model

    async with _database() as (factory, source_id):
        try:
            async with factory() as session:
                await session.execute(
                    text("INSERT INTO profiles (id) VALUES (:id)"),
                    {"id": user_id},
                )
                source = await session.get(FeedSource, source_id)
                source.kind = SourceKind.WEB
                source.canonical_key = identity.canonical_key
                source.canonical_url = identity.canonical_url
                # This suite isolates the Agent loop after a negative RSS probe.
                source.config = {
                    "resolve_site_avatar": False,
                    "web_feed_discovery": {
                        "version": 1,
                        "status": "not_found",
                        "source_url": source.canonical_url,
                    },
                }
                await session.commit()
            async with factory() as session:
                await subscribe_to_shared_source(
                    session,
                    user_id=user_id,
                    identity=identity,
                    display_name=identity.canonical_url,
                    folder_name=None,
                )
            while True:
                await run_web_rule_author_once(
                    factory, settings=settings, model_factory=model_factory
                )
                async with factory() as session:
                    job = await session.scalar(
                        select(WebRuleJob).where(WebRuleJob.source_id == source_id)
                    )
                    assert job is not None
                    evidence.update(
                        {
                            "status": job.status,
                            "stage": job.stage,
                            "engine": job.engine_snapshot,
                            "model_calls": job.model_calls_reserved,
                            "tool_calls": job.tool_calls_reserved,
                            "validation_calls": job.validation_calls_reserved,
                            "goal_resumptions": job.goal_resumptions_reserved,
                            "input_tokens": job.input_tokens,
                            "output_tokens": job.output_tokens,
                            "unknown_usage_count": job.unknown_usage_count,
                            "error_code": job.last_error_code,
                            "diagnostics": job.diagnostics,
                            "candidate_rule": job.candidate_rule,
                            "validation": job.validation_report,
                        }
                    )
                    if job.status not in {"queued", "retry_wait", "running"}:
                        break
                    if job.started_at is None:
                        pytest.fail("Live author was not claimed; inspect runtime configuration")
                    delay = max(
                        0.1, min(30, (job.available_at - datetime.now(UTC)).total_seconds())
                    )
                await asyncio.sleep(delay)
            assert job.status == "succeeded", evidence
            async with factory() as session:
                source = await session.get(FeedSource, source_id)
                raw_rule = source.config["web_rule"]
                evidence["rule"] = raw_rule
                evidence["version"] = source.config["web_rule_active_version"]
            repeats = []
            replay_metadata = []
            async with PublicAsyncClient(follow_redirects=True) as client:
                for _ in range(2):
                    adapter = WebBlogSourceAdapter(
                        WebBlogSourceConfig(
                            url=source_url,
                            resolve_site_avatar=False,
                            rule=parse_web_rule(raw_rule, source_url=source_url),
                        ),
                        client,
                    )
                    async with asyncio.timeout(120):
                        page = await adapter.scan_page(
                            SourceScanRequest(
                                initial=False,
                                conditional=False,
                                max_raw_items=50,
                            )
                        )
                    repeats.append(
                        [{"url": item.external_url, "title": item.title} for item in page.items]
                    )
                    replay_metadata.append(
                        {
                            "items": len(page.items),
                            "published_at": sum(
                                item.published_at is not None for item in page.items
                            ),
                            "author": sum(bool(item.author_name) for item in page.items),
                            "continuation_available": page.next_continuation is not None,
                            "listing_evidence": page.web_listing_evidence,
                        }
                    )
            evidence["model_free_replays"] = repeats
            evidence["model_free_replay_metadata"] = replay_metadata
            assert repeats[0] and repeats[0] == repeats[1]
            evidence["replay_status"] = "pass"
        finally:
            evidence["finished_at"] = datetime.now(UTC).isoformat()
            evidence_path.write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2, default=str)
            )
            async with factory() as session:
                await session.execute(text("DELETE FROM profiles WHERE id=:id"), {"id": user_id})
                await session.commit()
