"""Real PostgreSQL cache lifetime, alias reuse, and stale-publication checks."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, update
from test_content_retention import _database

from app.core.settings import Settings
from app.domain.enums import TranslationPurpose, TranslationScope
from app.storage.models import (
    Content,
    RankingSnapshot,
    TranslationArtifact,
    TranslationPreference,
    TranslationWork,
)
from app.translation.demand import default_translation_engine
from app.translation.domain import TranslationDemand
from app.translation.lifecycle import (
    clear_user_ephemeral_cache,
    current_ephemeral_context,
    purge_translation_cache,
)
from app.translation.store import (
    WorkOutcome,
    claim_translation_work,
    ensure_translation_work,
    load_and_ensure_translation_states,
    load_translation_states,
    persist_translation_artifacts,
    persist_work_outcomes,
)

pytestmark = pytest.mark.postgres


def _configure(monkeypatch):
    settings = Settings(_env_file=None, APP_DEEPSEEK_API_KEY="test-only")
    monkeypatch.setattr("app.core.settings.get_settings", lambda: settings)
    return default_translation_engine(settings)


@pytest.mark.asyncio
async def test_shared_model_and_purpose_fallback_prefers_exact_and_does_not_enqueue(monkeypatch):
    engine = _configure(monkeypatch)
    async with _database() as (factory, source, _users):
        title = f"Unique title {uuid4()}"
        demand = TranslationDemand(
            item_id="title", text=title, purpose=TranslationPurpose.TITLE, target_locale="zh-CN"
        )
        old_engine = replace(engine, model_name="previous-model")
        async with factory() as session:
            session.add(Content(authority_source_id=source, kind="article", title=title))
            await session.commit()
            assert (
                await persist_translation_artifacts(
                    session, [demand], {"title": "旧标题"}, engine=old_engine
                )
                == 1
            )
            ranked = replace(demand, purpose=TranslationPurpose.RANKING_TITLE)
            states, changed = await load_and_ensure_translation_states(
                session, [ranked], engine=engine
            )
            assert changed == 0
            assert states[ranked.identity(engine)].translated_text == "旧标题"
            assert await ensure_translation_work(session, [demand], engine=engine) == 0
            assert (
                await session.scalar(
                    select(TranslationWork.id).where(
                        TranslationWork.source_hash == demand.identity(engine).source_hash
                    )
                )
                is None
            )
            await persist_translation_artifacts(
                session, [demand], {"title": "新标题"}, engine=engine
            )
            states = await load_translation_states(
                session, [ranked.identity(engine)], engine=engine
            )
            assert states[ranked.identity(engine)].translated_text == "新标题"
            new_prompt = replace(engine, prompt_version="new-prompt")
            states = await load_translation_states(
                session, [ranked.identity(new_prompt)], engine=new_prompt
            )
            assert ranked.identity(new_prompt) not in states
            await session.execute(
                delete(TranslationArtifact).where(
                    TranslationArtifact.source_hash == demand.identity(engine).source_hash
                )
            )
            await session.commit()


@pytest.mark.asyncio
async def test_ephemeral_expiry_is_success_based_and_user_and_generation_isolated(monkeypatch):
    engine = _configure(monkeypatch)
    async with _database() as (factory, _source, users):
        async with factory() as session:
            context = await current_ephemeral_context(session, users[0])
            await session.commit()
            demand = TranslationDemand(
                item_id="cue",
                text=f"Cue {uuid4()}",
                purpose=TranslationPurpose.CAPTION,
                target_locale="zh-CN",
                scope=TranslationScope.USER,
                owner_id=users[0],
                cache_generation=context.cache_generation,
            )
            succeeded_at = datetime.now(UTC) - timedelta(minutes=59)
            assert (
                await persist_translation_artifacts(
                    session, [demand], {"cue": "字幕"}, engine=engine, created_at=succeeded_at
                )
                == 1
            )
            states = await load_translation_states(
                session, [demand.identity(engine)], engine=engine
            )
            assert states[demand.identity(engine)].cache_expires_at == succeeded_at + timedelta(
                hours=1
            )
            other = replace(demand, owner_id=users[1])
            assert not await load_translation_states(
                session, [other.identity(engine)], engine=engine
            )
            await session.execute(
                update(TranslationArtifact)
                .where(TranslationArtifact.owner_id == users[0])
                .values(created_at=datetime.now(UTC) - timedelta(minutes=61))
            )
            await session.commit()
            assert not await load_translation_states(
                session, [demand.identity(engine)], engine=engine
            )
            assert (
                await persist_translation_artifacts(
                    session, [demand], {"cue": "新字幕"}, engine=engine
                )
                == 1
            )
            # Changing preferences gives A->B->A a new generation even when the
            # effective engine eventually returns to A.
            session.add(
                TranslationPreference(
                    user_id=users[0],
                    target_locale="zh-CN",
                    provider_mode="app_default",
                    is_enabled=True,
                )
            )
            await session.flush()
            await clear_user_ephemeral_cache(session, users[0])
            await session.commit()
            assert (
                await persist_translation_artifacts(
                    session, [demand], {"cue": "迟到结果"}, engine=engine
                )
                == 0
            )
            assert not await load_translation_states(
                session, [demand.identity(engine)], engine=engine
            )
            other_context = await current_ephemeral_context(session, users[1])
            await session.commit()
            other = replace(other, cache_generation=other_context.cache_generation)
            assert (
                await persist_translation_artifacts(
                    session, [other], {"cue": "其他用户"}, engine=engine
                )
                == 1
            )
            await clear_user_ephemeral_cache(session, users[0])
            states = await load_translation_states(session, [other.identity(engine)], engine=engine)
            assert states[other.identity(engine)].translated_text == "其他用户"
            await session.commit()


@pytest.mark.asyncio
async def test_ranking_reference_protects_all_models_and_deleted_content_cannot_publish(
    monkeypatch,
):
    engine = _configure(monkeypatch)
    async with _database() as (factory, source, _users):
        title = f"Ranking retained {uuid4()}"
        demand = TranslationDemand(
            item_id="title", text=title, purpose=TranslationPurpose.TITLE, target_locale="zh-CN"
        )
        key = f"test:{uuid4()}"
        async with factory() as session:
            content = Content(authority_source_id=source, kind="article", title=title)
            session.add(content)
            now = datetime.now(UTC)
            session.add(
                RankingSnapshot(
                    cache_key=key,
                    kind="reddit",
                    parameters={},
                    payload={"items": [{"title": title}]},
                    fetched_at=now,
                    next_refresh_at=now,
                )
            )
            await session.commit()
            await ensure_translation_work(session, [demand], engine=engine)
            await session.commit()
            claimed = await claim_translation_work(
                session, engine_fingerprints=[engine.fingerprint], limit=10, lease_seconds=60
            )
            our_claim = [w for w in claimed if w.source_hash == demand.identity(engine).source_hash]
            assert len(our_claim) == 1
            await session.execute(delete(Content).where(Content.id == content.id))
            await session.commit()
            await persist_work_outcomes(
                session, our_claim, {our_claim[0].id: WorkOutcome(translated_text="榜单引用")}
            )
            await purge_translation_cache(session)
            await session.commit()
            assert (
                await load_translation_states(session, [demand.identity(engine)], engine=engine)
            )[demand.identity(engine)].translated_text == "榜单引用"
            await session.execute(delete(RankingSnapshot).where(RankingSnapshot.cache_key == key))
            await purge_translation_cache(session)
            await session.commit()
            assert not await load_translation_states(
                session, [demand.identity(engine)], engine=engine
            )
            # A provider that finished after cleanup cannot resurrect an artifact.
            assert (
                await persist_work_outcomes(
                    session, our_claim, {our_claim[0].id: WorkOutcome(translated_text="过期结果")}
                )
                == 0
            )
            assert (
                await persist_translation_artifacts(
                    session, [demand], {"title": "过期结果"}, engine=engine
                )
                == 0
            )


@pytest.mark.asyncio
async def test_empty_orphan_cleanup_does_not_deadlock_candidate_worker(monkeypatch):
    import asyncio

    from app.storage.models import FeedSource, IngestionCandidate
    from app.translation import lifecycle
    from app.workers.candidates import process_candidates

    _configure(monkeypatch)
    async with _database() as (factory, source, _users):
        candidate_id = uuid4()
        async with factory() as session:
            now = datetime.now(UTC)
            session.add(
                IngestionCandidate(
                    id=candidate_id,
                    source_id=source,
                    native_id="pending",
                    payload={},
                    payload_hash="a" * 64,
                    observed_at=now,
                    suggested_feed_sort_at=now,
                    status="pending",
                )
            )
            await session.commit()
        attempted_lifecycle = asyncio.Event()
        original_lock = lifecycle.lock_shared_lifecycle

        async def observed_lock(session):
            attempted_lifecycle.set()
            await original_lock(session)

        async with factory() as cleanup:
            await original_lock(cleanup)
            monkeypatch.setattr(lifecycle, "lock_shared_lifecycle", observed_lock)
            task = asyncio.create_task(process_candidates(factory, limit=1))
            try:
                await asyncio.wait_for(attempted_lifecycle.wait(), timeout=5)
                async with factory() as observer:
                    # Worker waiting for lifecycle must not own the candidate row:
                    # deleting an empty orphan cascades through that same row.
                    found = await observer.scalar(
                        select(IngestionCandidate.id)
                        .where(IngestionCandidate.id == candidate_id)
                        .with_for_update(nowait=True)
                    )
                    assert found == candidate_id
                    await observer.rollback()
                await cleanup.execute(delete(FeedSource).where(FeedSource.id == source))
                await cleanup.commit()
                assert await asyncio.wait_for(task, timeout=5) == 0
            finally:
                await cleanup.rollback()
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
