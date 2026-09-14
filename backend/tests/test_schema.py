from io import StringIO

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.storage import models  # noqa: F401
from app.storage.database import Base
from app.storage.schema import REQUIRED_SCHEMA_REVISION


def test_core_tables_are_registered() -> None:
    assert {
        "profiles",
        "feed_sources",
        "source_subscriptions",
        "source_sync_runs",
        "source_sync_states",
        "web_rule_jobs",
        "web_rule_agent_runtime",
        "ingestion_candidates",
        "web_frontier",
        "provider_quota_usage",
        "worker_loop_heartbeats",
        "app_schema_contracts",
        "ranking_snapshots",
        "ranking_provider_budgets",
        "contents",
        "source_entries",
        "content_media",
        "translation_artifacts",
        "translation_work",
        "user_translation_preferences",
        "user_content_states",
        "user_saved_contents",
    }.issubset(Base.metadata.tables)


def test_translation_runtime_has_one_work_authority_and_immutable_artifacts() -> None:
    assert "content_translations" not in Base.metadata.tables
    assert "translation_jobs" not in Base.metadata.tables
    work = Base.metadata.tables["translation_work"]
    artifact = Base.metadata.tables["translation_artifacts"]

    assert {
        "priority",
        "lease_token",
        "lease_expires_at",
        "source_text",
        "context_before",
        "context_after",
    }.issubset(work.columns.keys())
    assert "status" not in artifact.columns
    assert "translated_text" in artifact.columns
    for table, constraint_name in (
        (work, "ck_translation_work_purpose"),
        (artifact, "ck_translation_artifacts_purpose"),
    ):
        constraint = next(
            constraint for constraint in table.constraints if constraint.name == constraint_name
        )
        purpose_sql = str(constraint.sqltext)
        assert "ranking_description" in purpose_sql
        assert "web_segment" in purpose_sql
        assert "caption" in purpose_sql


def test_content_url_hash_is_unique_only_within_its_authoritative_source() -> None:
    contents = Base.metadata.tables["contents"]
    index = next(
        index for index in contents.indexes if index.name == "uq_contents_authority_url_hash"
    )

    assert index.unique is True
    assert tuple(column.name for column in index.columns) == ("authority_source_id", "url_hash")
    assert "url_hash IS NOT NULL" in str(index.dialect_options["postgresql"]["where"])
    assert contents.c.authority_source_id.nullable is False
    assert all(index.name != "uq_contents_url_hash" for index in contents.indexes)


def test_source_entry_content_ownership_is_database_enforced() -> None:
    contents = Base.metadata.tables["contents"]
    entries = Base.metadata.tables["source_entries"]
    ownership_key = next(
        constraint
        for constraint in contents.constraints
        if constraint.name == "uq_contents_id_authority_source"
    )
    ownership_fk = next(
        constraint
        for constraint in entries.constraints
        if constraint.name == "fk_source_entries_content_authority"
    )

    assert tuple(column.name for column in ownership_key.columns) == (
        "id",
        "authority_source_id",
    )
    assert tuple(column.name for column in ownership_fk.columns) == ("content_id", "source_id")
    assert tuple(element.target_fullname for element in ownership_fk.elements) == (
        "contents.id",
        "contents.authority_source_id",
    )


def test_feed_source_has_no_legacy_runtime_schedule_columns() -> None:
    columns = Base.metadata.tables["feed_sources"].columns

    assert "next_sync_at" not in columns
    assert "last_synced_at" not in columns
    assert "etag" not in columns
    assert "consecutive_failures" not in columns


def test_candidate_identity_and_feed_sort_are_database_enforced() -> None:
    candidates = Base.metadata.tables["ingestion_candidates"]
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in candidates.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }

    assert ("source_id", "native_id") in unique_columns
    assert Base.metadata.tables["source_entries"].c.feed_sort_at.nullable is False
    assert Base.metadata.tables["content_media"].c.is_active.nullable is False


def test_web_frontier_tracks_discovery_and_fetch_evidence() -> None:
    frontier = Base.metadata.tables["web_frontier"]

    assert {
        "original_url",
        "normalized_url",
        "canonical_url",
        "etag",
        "last_modified",
        "content_hash",
    }.issubset(frontier.columns.keys())
    status_constraint = next(
        constraint
        for constraint in frontier.constraints
        if constraint.name == "ck_web_frontier_status"
    )
    assert "pending" in str(status_constraint.sqltext)
    assert "fetched" in str(status_constraint.sqltext)
    assert "uncertain" in str(status_constraint.sqltext)


def test_runtime_safety_columns_and_claim_index_are_registered() -> None:
    contents = Base.metadata.tables["contents"]
    snapshots = Base.metadata.tables["ranking_snapshots"]
    claim = next(
        index
        for index in Base.metadata.tables["translation_work"].indexes
        if index.name == "ix_translation_work_claim"
    )

    assert "authority_source_id" in contents.columns
    assert {
        "last_accessed_at",
        "is_ready",
        "refresh_token",
        "refresh_expires_at",
        "manual_refresh_after",
    }.issubset(snapshots.columns.keys())
    assert tuple(column.name for column in claim.columns)[0] == "engine_fingerprint"


def test_required_schema_revision_is_the_alembic_head() -> None:
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))

    assert scripts.get_current_head() == REQUIRED_SCHEMA_REVISION


def test_complete_offline_upgrade_can_be_rendered() -> None:
    config = Config("alembic.ini")
    config.set_main_option(
        "sqlalchemy.url",
        "postgresql+asyncpg://offline:offline@example.invalid/offline",
    )
    output = StringIO()
    config.output_buffer = output

    command.upgrade(config, "head", sql=True)

    rendered = output.getvalue()
    assert "UPDATE translation_artifacts" in rendered
    assert "reader-runtime-v17" in rendered
    assert "FROM feed_sources AS source" in rendered
    assert "UPDATE ranking_snapshots" in rendered
    assert "SET next_refresh_at = now()" in rendered
    assert "jsonb_array_length(payload -> 'items') > 0" in rendered
    assert "CROSS JOIN (VALUES" in rendered
    assert "reddit_snapshot_reconciliation" in rendered
    assert "reader_json_ascii" in rendered
    assert "-- Running upgrade 20260810_16 -> 20260811_17" in rendered
    assert "-- Running upgrade 20260811_17 -> 20260815_18" in rendered
    assert "-- Running upgrade 20260815_18 -> 20260828_19" in rendered
    assert "-- Running upgrade 20260828_20 -> 20260902_21" in rendered
    assert "-- Running upgrade 20260902_21 -> 20260902_22" in rendered
    assert "-- Running upgrade 20260902_22 -> 20260902_23" in rendered
    assert "-- Running upgrade 20260902_23 -> 20260908_24" in rendered
    assert "-- Running upgrade 20260908_24 -> 20260909_25" in rendered
    assert "-- Running upgrade 20260909_25 -> 20260910_26" in rendered
    assert f"-- Running upgrade 20260910_27 -> {REQUIRED_SCHEMA_REVISION}" in rendered
    assert "CREATE TABLE web_rule_jobs" in rendered
    assert "CREATE TABLE web_rule_agent_runtime" in rendered
    assert "web_rule_structural_failures" in rendered
    assert "ADD COLUMN goal_resumptions_reserved INTEGER DEFAULT '0' NOT NULL" in rendered
    assert "ck_web_rule_jobs_goal_resumptions" in rendered
    assert "web_rule_required" in rendered
    assert "DELETE FROM web_frontier" in rendered
    assert "DELETE FROM ranking_snapshots" in rendered
    assert "UPDATE user_translation_preferences" in rendered
    assert "translation_engine_retired" in rendered
    assert "CREATE TABLE worker_loop_heartbeats" in rendered
    assert "reader-runtime-v18" in rendered
    assert "reader-runtime-v19" in rendered
    assert "reader-runtime-v20" in rendered
    assert "reader-runtime-v21" in rendered
    assert "reader-runtime-v22" in rendered
    assert "reader-runtime-v23" in rendered
    assert "ck_translation_quota_audits_before_state" in rendered
    assert "ck_translation_quota_audits_after_state" in rendered
    assert "WHEN kind = 'article' THEN 'failed'" in rendered
