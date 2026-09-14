"""Safety checks for the one-time, credential-bearing Supabase transfer tool."""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "migrate_supabase.py"
spec = importlib.util.spec_from_file_location("supabase_transfer", SCRIPT)
assert spec and spec.loader
transfer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transfer)

NOW = datetime(2026, 9, 12, tzinfo=UTC)
ORIGIN = "https://old-project.supabase.co:443"
AVATAR_URL = "https://old-project.supabase.co/storage/v1/object/public/avatars/user/avatar.png?v=1"
PNG = b"\x89PNG\r\n\x1a\n" + b"test-image-payload"
# A syntactically valid hash fixture, deliberately not a real user's password.
HASH = "$2a$10$" + "a" * 53


def account(**overrides):
    return {
        "id": str(uuid4()),
        "email": " Reader@Example.com ",
        "encrypted_password": HASH,
        "banned_until": None,
        "deleted_at": None,
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
        **overrides,
    }


def test_account_plan_preserves_ids_normalizes_email_and_disables_legacy_accounts():
    login = account()
    banned = account(email="banned@example.com", banned_until=(NOW + timedelta(days=1)).isoformat())
    deleted = account(email="deleted@example.com", deleted_at=NOW.isoformat())
    passwordless = account(email="oauth@example.com", encrypted_password="")
    users = [login, banned, deleted, passwordless]
    orphan_profile = str(uuid4())

    active, skipped = transfer.plan_accounts(
        users, {row["id"] for row in users} | {orphan_profile}, NOW
    )

    assert len(active) == 1
    assert active[0]["id"] == login["id"]
    assert active[0]["email"] == "reader@example.com"
    assert active[0]["encrypted_password"] == HASH
    assert skipped == {"disabled": 2, "without_password": 1, "profiles_without_auth": 1}


def test_expired_ban_does_not_permanently_disable_a_source_account():
    user = account(banned_until=(NOW - timedelta(seconds=1)).isoformat())
    active, skipped = transfer.plan_accounts([user], {user["id"]}, NOW)
    assert len(active) == 1
    assert skipped["disabled"] == 0


def test_normalized_email_collision_blocks_import_without_exposing_addresses():
    users = [account(), account(email="reader@example.COM")]
    with pytest.raises(transfer.TransferError, match="email addresses conflict") as error:
        transfer.plan_accounts(users, {row["id"] for row in users}, NOW)
    assert "example" not in str(error.value)
    assert HASH not in str(error.value)


@pytest.mark.parametrize("password_hash", ["plaintext-password", "$2z$10$" + "a" * 53, HASH[:-1]])
def test_unsupported_nonempty_password_hash_blocks_instead_of_losing_an_account(password_hash):
    user = account(encrypted_password=password_hash)
    with pytest.raises(transfer.TransferError, match="unsupported password hash"):
        transfer.plan_accounts([user], {user["id"]}, NOW)


def test_login_account_missing_profile_blocks_instead_of_losing_user_data():
    with pytest.raises(transfer.TransferError, match="no source profile"):
        transfer.plan_accounts([account()], set(), NOW)


def test_duplicate_account_id_is_rejected():
    user = account()
    with pytest.raises(transfer.TransferError, match="duplicate user IDs"):
        transfer.plan_accounts([user, user], {user["id"]}, NOW)


@pytest.mark.parametrize(
    "url",
    [
        "https://old-project.supabase.co/storage/v1/object/public/avatars/../secrets",
        "https://old-project.supabase.co/storage/v1/object/public/avatars/%2e%2e/secrets",
        "https://old-project.supabase.co/storage/v1/object/public/avatars/%252e%252e/secrets",
        "https://old-project.supabase.co/storage/v1/object/public/avatars/user\\secret",
        "https://old-project.supabase.co/storage/v1/object/sign/avatars/private?token=secret",
        "https://user:password@old-project.supabase.co/storage/v1/object/public/avatars/x.png",
        "https://@old-project.supabase.co/storage/v1/object/public/avatars/x.png",
        "https://old-project.supabase.co/storage/v1/object/public/avatars/x.png#fragment",
        "https://old-project.supabase.co/storage/v1/object/public/avatars/x\n.png",
        "https://other-project.supabase.co/storage/v1/object/public/avatars/x.png",
        "http://old-project.supabase.co/storage/v1/object/public/avatars/x.png",
    ],
)
def test_unsafe_or_untrusted_supabase_avatar_urls_are_blocked(url):
    with pytest.raises(transfer.TransferError):
        transfer.is_trusted_avatar(url, ORIGIN)


def test_only_configured_supabase_origin_is_downloaded():
    assert transfer.is_trusted_avatar(AVATAR_URL, ORIGIN)
    assert not transfer.is_trusted_avatar("http://127.0.0.1/private", ORIGIN)
    assert not transfer.is_trusted_avatar(
        "https://avatars.githubusercontent.com/avatar.png", ORIGIN
    )
    with pytest.raises(transfer.TransferError, match="trusted origin"):
        transfer.is_trusted_avatar(AVATAR_URL, None)


@pytest.mark.asyncio
async def test_avatar_redirect_cannot_reach_private_or_other_hosts():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(transfer.TransferError, match="download failed"):
            await transfer.download_avatar(client, AVATAR_URL, ORIGIN)

    assert len(requests) == 1
    assert requests[0].url.host == "old-project.supabase.co"


@pytest.mark.asyncio
async def test_avatar_download_validates_bytes_and_size(monkeypatch):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=PNG, headers={"content-type": "text/plain"})
        )
    ) as client:
        assert await transfer.download_avatar(client, AVATAR_URL, ORIGIN) == PNG
        monkeypatch.setattr(transfer, "MAX_AVATAR_BYTES", 8)
        with pytest.raises(transfer.TransferError, match="size limit"):
            await transfer.download_avatar(client, AVATAR_URL, ORIGIN)


@pytest.mark.asyncio
async def test_avatar_html_error_with_success_status_is_not_saved():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"<html>private error body</html>")
        )
    ) as client:
        with pytest.raises(transfer.TransferError, match="not a supported"):
            await transfer.download_avatar(client, AVATAR_URL, ORIGIN)


def test_environment_file_credentials_are_literal_and_not_mixed_with_process_env(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("APP_DATABASE_URL", "postgresql://wrong-process-db/reader")
    monkeypatch.setenv("PASSWORD", "must-not-expand")
    env_file = tmp_path / "old.env"
    env_file.write_text("APP_DATABASE_URL='postgresql://reader:${PASSWORD}@source/reader'\n")

    settings = transfer.settings_from_file(env_file)

    assert settings["APP_DATABASE_URL"] == "postgresql://reader:${PASSWORD}@source/reader"
    assert "PASSWORD" not in settings


def test_database_identity_ignores_password_but_distinguishes_database_and_endpoint():
    source = {"APP_DATABASE_URL": "postgresql://old:secret@database:5432/reader"}
    same = {"APP_DATABASE_URL": "postgresql+asyncpg://new:different@database/reader"}
    other = {"APP_DATABASE_URL": "postgresql://new:different@other-database/reader"}
    assert transfer.database_identity(source) == transfer.database_identity(same)
    assert transfer.database_identity(source) != transfer.database_identity(other)
    assert "secret" not in transfer.database_identity(source)


def test_dependency_order_preserves_references_and_allows_self_retries():
    tables = {"profiles", "sources", "jobs", "runtime"}
    edges = [("sources", "profiles"), ("jobs", "sources"), ("jobs", "jobs"), ("runtime", "jobs")]
    assert transfer.dependency_order(tables, edges) == ["profiles", "sources", "jobs", "runtime"]


def test_cross_table_cycle_and_missing_dependency_block_before_copy():
    with pytest.raises(transfer.TransferError, match="cycles"):
        transfer.dependency_order({"a", "b"}, [("a", "b"), ("b", "a")])
    with pytest.raises(transfer.TransferError, match="untransferred"):
        transfer.dependency_order({"a"}, [("a", "external")])


@pytest.mark.parametrize("primary_key", [["cache_key"], ["user_id", "window_start"], []])
def test_copy_checksum_order_is_independent_of_database_locale(primary_key):
    table = {
        "columns": [{"name": name, "type": "text"} for name in primary_key or ["payload"]],
        "primary_key": primary_key,
    }
    query = transfer.copy_query("ranking_snapshots", table)
    expected = (
        ", ".join(f'"{key}"::text COLLATE "C"' for key in primary_key)
        if primary_key
        else 'to_jsonb(snapshot_row)::text COLLATE "C"'
    )
    assert query.endswith(f"ORDER BY {expected}")


@pytest.mark.parametrize(
    "changed",
    [
        {"paused_code": "quota_limit"},
        {"current_job_id": str(uuid4())},
        {"engine_snapshot": '{"provider":"configured"}'},
        {"lease_token": str(uuid4())},
    ],
)
def test_runtime_seed_guard_rejects_existing_configuration_or_leases(changed):
    seed = {
        "id": "default",
        "engine_snapshot": "{}",
        "created_at": NOW,
        "updated_at": NOW,
        "paused_code": None,
        "current_job_id": None,
        "lease_token": None,
    }
    assert transfer.pristine_runtime(seed)
    assert not transfer.pristine_runtime({**seed, **changed})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "occupied_table", ["profiles", "worker_loop_heartbeats", "reader_sessions"]
)
async def test_target_guard_rejects_any_existing_business_or_session_rows(
    monkeypatch, occupied_table
):
    source_tables = {
        "profiles",
        "worker_loop_heartbeats",
        "alembic_version",
        "app_schema_contracts",
        "web_rule_agent_runtime",
    }
    metadata = {
        name: {"columns": [], "primary_key": []} for name in source_tables | transfer.AUTH_TABLES
    }
    manifest = {"tables": {name: metadata[name] for name in source_tables}}
    connection = AsyncMock()
    connection.fetch.return_value = [{"version_num": transfer.TARGET_REVISION}]
    connection.fetchval.side_effect = lambda query: f'"{occupied_table}"' in query
    monkeypatch.setattr(transfer, "table_metadata", AsyncMock(return_value=metadata))

    with pytest.raises(transfer.TransferError, match="contains application data"):
        await transfer.check_target(connection, manifest, require_empty=True)

    connection.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_target_guard_rejects_wrong_schema_revision_before_inspecting_data(monkeypatch):
    connection = AsyncMock()
    connection.fetch.return_value = [{"version_num": transfer.SOURCE_REVISION}]
    metadata = AsyncMock()
    monkeypatch.setattr(transfer, "table_metadata", metadata)

    with pytest.raises(transfer.TransferError, match="revision 28"):
        await transfer.check_target(connection, {}, require_empty=True)

    metadata.assert_not_awaited()
    connection.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_export_refuses_rls_filtered_table_instead_of_backing_up_an_incomplete_view():
    connection = AsyncMock()
    connection.fetch.return_value = [{"name": "profiles", "relkind": "r", "rls_active": True}]
    with pytest.raises(transfer.TransferError, match="complete snapshot through RLS"):
        await transfer.table_metadata(connection)
    assert connection.fetch.await_count == 1


@pytest.mark.asyncio
async def test_ordinary_table_metadata_allows_columns_without_identity_or_generation():
    connection = AsyncMock()
    connection.fetch.side_effect = [
        [{"name": "profiles", "relkind": "r", "rls_active": False}],
        [
            {
                "name": "id",
                "type": "uuid",
                "type_schema": "pg_catalog",
                "has_identity": False,
                "is_generated": False,
                "default_value": None,
            }
        ],
        [{"name": "id"}],
    ]
    assert await transfer.table_metadata(connection) == {
        "profiles": {"columns": [{"name": "id", "type": "uuid"}], "primary_key": ["id"]}
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", ["has_identity", "is_generated"])
async def test_generated_or_identity_columns_still_require_an_explicit_transfer_plan(flag):
    connection = AsyncMock()
    column = {
        "name": "id",
        "type": "integer",
        "type_schema": "pg_catalog",
        "has_identity": False,
        "is_generated": False,
        "default_value": None,
        flag: True,
    }
    connection.fetch.side_effect = [
        [{"name": "profiles", "relkind": "r", "rls_active": False}],
        [column],
        [{"name": "id"}],
    ]
    with pytest.raises(transfer.TransferError):
        await transfer.table_metadata(connection)


@pytest.mark.asyncio
async def test_actual_partitioned_table_is_still_rejected_without_copy():
    connection = AsyncMock()
    connection.fetch.return_value = [{"name": "events", "relkind": "p", "rls_active": False}]
    with pytest.raises(transfer.TransferError, match="Partitioned tables"):
        await transfer.table_metadata(connection)
    connection.copy_from_query.assert_not_awaited()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_catalog_metadata_against_real_postgres_read_only():
    """Opt-in read-only check for asyncpg catalog types on a prepared Reader DB.

    Set READER_TRANSFER_TEST_DATABASE_URL and, if needed,
    READER_TRANSFER_TEST_DATABASE_SSL through the environment, never CLI arguments.
    No schema, fixtures, or application rows are written by this check.
    """
    dsn = os.environ.get("READER_TRANSFER_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Set the dedicated transfer-test DSN to run read-only PostgreSQL verification.")
    connection = await transfer.asyncpg.connect(
        **transfer.connection_options(
            {
                "APP_DATABASE_URL": dsn,
                "APP_DATABASE_SSL": os.environ.get("READER_TRANSFER_TEST_DATABASE_SSL", "false"),
            }
        )
    )
    try:
        async with connection.transaction(isolation="repeatable_read", readonly=True):
            tables = await transfer.table_metadata(connection)
            assert tables["profiles"]["primary_key"] == ["id"]
            assert {column["name"]: column["type"] for column in tables["profiles"]["columns"]} == {
                "id": "uuid",
                "display_name": "character varying(120)",
                "avatar_url": "text",
                "created_at": "timestamp with time zone",
                "updated_at": "timestamp with time zone",
            }
    finally:
        await connection.close()


@pytest.fixture
def snapshot(tmp_path):
    archive = tmp_path / "snapshot"
    archive.mkdir(mode=0o700)

    def entry(filename, data):
        transfer.private_write(archive / filename, gzip.compress(data, mtime=0))
        return {"file": filename, "sha256": hashlib.sha256(data).hexdigest(), "count": 0}

    manifest = {
        "format_version": transfer.FORMAT_VERSION,
        "source_revision": transfer.SOURCE_REVISION,
        "target_revision": transfer.TARGET_REVISION,
        "source_identity": transfer.database_identity(
            {"APP_DATABASE_URL": "postgresql://source/reader"}
        ),
        "exported_at": NOW.isoformat(),
        "tables": {
            "profiles": {
                "columns": [{"name": "id", "type": "uuid"}],
                "primary_key": ["id"],
                **entry("public.profiles.copy.gz", b""),
            }
        },
        "auth": entry("auth-users.json.gz", b"[]"),
        "profile_check": entry("profiles-without-avatar.copy.gz", b""),
        "avatars": [],
        "original_avatar_urls": {},
        "contracts": [],
        "summary": {
            "login_accounts": 0,
            "disabled": 0,
            "without_password": 0,
            "profiles_without_auth": 0,
        },
    }
    transfer.private_write(archive / "manifest.json", json.dumps(manifest).encode())
    return archive, manifest


def test_snapshot_files_are_private_and_verified(snapshot):
    archive, manifest = snapshot
    assert archive.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in archive.iterdir())
    assert transfer.read_manifest(archive) == manifest


def test_modified_copy_payload_is_rejected_before_database_connection(snapshot, monkeypatch):
    archive, _ = snapshot
    (archive / "public.profiles.copy.gz").write_bytes(gzip.compress(b"modified"))
    connect = AsyncMock()
    monkeypatch.setattr(transfer.asyncpg, "connect", connect)
    with pytest.raises(transfer.TransferError, match="checksum"):
        transfer.read_manifest(archive)
    connect.assert_not_called()


def test_archive_symlink_and_world_readable_files_are_rejected(snapshot):
    archive, _ = snapshot
    filename = archive / "auth-users.json.gz"
    filename.chmod(0o644)
    with pytest.raises(transfer.TransferError, match="private files"):
        transfer.read_manifest(archive)
    filename.chmod(0o600)
    actual = filename.with_suffix(".original")
    filename.rename(actual)
    filename.symlink_to(actual)
    with pytest.raises(transfer.TransferError, match="regular private"):
        transfer.read_manifest(archive)


def test_archive_rejects_path_traversal_without_opening_outside_file(snapshot):
    archive, manifest = snapshot
    manifest["tables"]["profiles"]["file"] = "../private.copy.gz"
    (archive / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(transfer.TransferError, match="unexpected file name"):
        transfer.read_manifest(archive)


@pytest.mark.asyncio
async def test_target_matching_source_is_rejected_without_connecting(snapshot, monkeypatch):
    archive, _ = snapshot
    connect = AsyncMock()
    monkeypatch.setattr(transfer.asyncpg, "connect", connect)
    with pytest.raises(transfer.TransferError, match="same database"):
        await transfer.import_snapshot({"APP_DATABASE_URL": "postgresql://source/reader"}, archive)
    connect.assert_not_called()


@pytest.mark.asyncio
async def test_target_nonempty_guard_runs_before_deletion_or_copy(snapshot, monkeypatch):
    archive, _ = snapshot
    connection = AsyncMock()
    transaction = AsyncMock()
    connection.transaction = Mock(return_value=transaction)
    monkeypatch.setattr(transfer.asyncpg, "connect", AsyncMock(return_value=connection))
    monkeypatch.setattr(
        transfer,
        "check_target",
        AsyncMock(side_effect=transfer.TransferError("The target contains application data.")),
    )
    with pytest.raises(transfer.TransferError, match="contains application data"):
        await transfer.import_snapshot({"APP_DATABASE_URL": "postgresql://target/reader"}, archive)
    assert not any("DELETE" in call.args[0] for call in connection.execute.await_args_list)
    connection.copy_to_table.assert_not_awaited()
    assert transaction.__aexit__.await_args.args[0] is transfer.TransferError
    connection.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_verification_failure_rolls_back_the_whole_import(snapshot, monkeypatch):
    archive, _ = snapshot
    connection = AsyncMock()
    transaction = AsyncMock()
    connection.transaction = Mock(return_value=transaction)
    connection.copy_to_table.return_value = "COPY 0"
    monkeypatch.setattr(transfer.asyncpg, "connect", AsyncMock(return_value=connection))
    monkeypatch.setattr(transfer, "check_target", AsyncMock(return_value=["profiles"]))
    monkeypatch.setattr(
        transfer,
        "verify_tables",
        AsyncMock(side_effect=transfer.TransferError("Target content differs from the snapshot.")),
    )
    with pytest.raises(transfer.TransferError, match="differs"):
        await transfer.import_snapshot({"APP_DATABASE_URL": "postgresql://target/reader"}, archive)
    connection.copy_to_table.assert_awaited_once()
    assert connection.copy_to_table.await_args.kwargs["format"] == "text"
    assert transaction.__aexit__.await_args.args[0] is transfer.TransferError
    connection.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_dry_run_is_read_only_and_never_copies_or_deletes(snapshot, monkeypatch):
    archive, _ = snapshot
    connection = AsyncMock()
    connection.transaction = Mock(return_value=AsyncMock())
    monkeypatch.setattr(transfer.asyncpg, "connect", AsyncMock(return_value=connection))
    monkeypatch.setattr(transfer, "check_target", AsyncMock(return_value=["profiles"]))
    result = await transfer.import_snapshot(
        {"APP_DATABASE_URL": "postgresql://target/reader"}, archive, dry_run=True
    )
    assert result["target_empty"]
    connection.transaction.assert_called_once_with(isolation="repeatable_read", readonly=True)
    assert all(call.args[0].startswith("SET LOCAL ") for call in connection.execute.await_args_list)
    connection.copy_to_table.assert_not_awaited()


@pytest.mark.asyncio
async def test_export_dry_run_reads_consistent_snapshot_without_writing_files(
    tmp_path, monkeypatch
):
    user = account()
    archive = tmp_path / "not-created"
    connection = AsyncMock()
    connection.transaction = Mock(return_value=AsyncMock())
    connection.fetch.side_effect = [
        [{"version_num": transfer.SOURCE_REVISION}],
        [{"id": user["id"], "avatar_url": AVATAR_URL}],
    ]
    connection.fetchval.return_value = NOW
    monkeypatch.setattr(transfer.asyncpg, "connect", AsyncMock(return_value=connection))
    monkeypatch.setattr(transfer, "table_metadata", AsyncMock(return_value={"profiles": {}}))
    monkeypatch.setattr(transfer, "source_accounts", AsyncMock(return_value=[user]))

    result = await transfer.export_snapshot(
        {
            "APP_DATABASE_URL": "postgresql://source/reader",
            "APP_SUPABASE_URL": "https://old-project.supabase.co",
        },
        archive,
        dry_run=True,
    )

    assert result["login_accounts"] == 1 and result["avatars"] == 1
    assert not archive.exists()
    connection.transaction.assert_called_once_with(isolation="repeatable_read", readonly=True)
    connection.copy_from_query.assert_not_awaited()


@pytest.mark.asyncio
async def test_copy_export_streams_portable_text_and_computes_exact_count_and_checksum(tmp_path):
    connection = AsyncMock()
    payload = 'uuid\t{"body": "unicode: 中; tab: \\\\t"}\t\\N\n'.encode()

    async def copy(query, *, output, format):
        assert format == "text"
        await output(payload[:10])
        await output(payload[10:])
        return "COPY 1"

    connection.copy_from_query.side_effect = copy
    result = await transfer.export_copy(connection, tmp_path, "table.copy.gz", "SELECT body FROM t")

    assert result["count"] == 1
    assert result["sha256"] == hashlib.sha256(payload).hexdigest()
    assert gzip.decompress((tmp_path / "table.copy.gz").read_bytes()) == payload
    assert (tmp_path / "table.copy.gz").stat().st_mode & 0o777 == 0o600


def test_error_output_never_exposes_database_hash_token_or_private_url(
    tmp_path, monkeypatch, capsys
):
    secret = (
        "postgresql://user:password@db/reader $2a$HASH https://old-project/private?token=secret"
    )
    monkeypatch.setattr(transfer, "settings_from_file", Mock(return_value={}))
    monkeypatch.setattr(transfer, "export_snapshot", AsyncMock(side_effect=RuntimeError(secret)))

    assert transfer.main(["export", "--archive", str(tmp_path / "snapshot")]) == 1

    output = capsys.readouterr()
    assert output.out == ""
    assert "RuntimeError" in output.err
    assert all(
        value not in output.err for value in ["password", "$2a$", "https://", "token=secret"]
    )
