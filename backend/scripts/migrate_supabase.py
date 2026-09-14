"""Move a revision-27 Supabase snapshot into an empty revision-28 PostgreSQL database.

Run from backend with its virtualenv. Credentials stay in environment files, never
in command arguments::

    python scripts/migrate_supabase.py export --source-env /private/old.env \
        --archive /private/reader-transfer
    # On the independent target, run Alembic upgrade head before importing.
    python scripts/migrate_supabase.py import --target-env /private/new.env \
        --archive /private/reader-transfer --dry-run
    python scripts/migrate_supabase.py import --target-env /private/new.env \
        --archive /private/reader-transfer
    python scripts/migrate_supabase.py verify --target-env /private/new.env \
        --archive /private/reader-transfer

Stop the old API/worker before export and leave the new services stopped until
verification completes. Export uses a read-only repeatable-read transaction, but
cannot prevent later source writes or changes in external Storage. Import is one
transaction, rejects nonempty targets, and never changes the source. An existing
archive is never overwritten. The archive contains password hashes and private
application data: retain its 0700 directory / 0600 file permissions.

Old sessions are intentionally not transferred. Disabled/deleted/passwordless
accounts retain their profiles and data, without receiving a login identity.
Only public avatars on the configured original Supabase origin are downloaded;
external avatar URLs are preserved. Any failed trusted-avatar download aborts the
export, so a migration cannot silently lose the original storage dependency.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
import os
import re
import stat
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit
from uuid import UUID, uuid4

import asyncpg
import httpx
from dotenv import dotenv_values

SOURCE_REVISION = "20260910_27"
TARGET_REVISION = "20260912_28"
FORMAT_VERSION = 1
AUTH_TABLES = {"reader_users", "reader_sessions", "reader_refresh_tokens", "profile_avatars"}
METADATA_TABLES = {"alembic_version", "app_schema_contracts"}
KNOWN_CONTRACTS = {f"reader-runtime-v{version}" for version in range(17, 25)}
MAX_AVATAR_BYTES = 5 * 1024 * 1024
IDENTIFIER = re.compile(r"[a-z_][a-z_0-9]*\Z")
BCRYPT = re.compile(r"\$2[aby]\$(0[4-9]|[12][0-9]|3[01])\$[./A-Za-z0-9]{53}\Z")


class TransferError(Exception):
    """An intentionally secret-free error suitable for operator output."""


def identifier(value: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise TransferError("Unsupported database identifier; no changes made.")
    return f'"{value}"'


def settings_from_file(path: Path | None) -> dict[str, str]:
    # Explicit files are authoritative; never mix a new process APP_DATABASE_URL
    # with the source credentials from a different file.
    values = dotenv_values(path, interpolate=False) if path else os.environ
    return {key: value for key, value in values.items() if value is not None}


def connection_options(settings: Mapping[str, str]) -> dict[str, Any]:
    dsn = settings.get("APP_DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
    parts = urlsplit(dsn)
    if parts.scheme not in {"postgres", "postgresql"} or not parts.hostname:
        raise TransferError("The selected environment needs a PostgreSQL APP_DATABASE_URL.")
    ssl_value = settings.get("APP_DATABASE_SSL", "false").lower()
    if ssl_value not in {"true", "false", "1", "0", "yes", "no"}:
        raise TransferError("APP_DATABASE_SSL must be a boolean.")
    return {
        "dsn": dsn,
        "ssl": "require" if ssl_value in {"true", "1", "yes"} else None,
        "timeout": 20,
        "command_timeout": 300,
    }


def database_identity(settings: Mapping[str, str]) -> str:
    parts = urlsplit(connection_options(settings)["dsn"])
    identity = f"{parts.hostname}:{parts.port or 5432}/{unquote(parts.path)}"
    return hashlib.sha256(identity.encode()).hexdigest()


def trusted_origin(settings: Mapping[str, str]) -> str | None:
    value = settings.get("APP_SUPABASE_URL") or settings.get("SUPABASE_URL")
    if not value:
        return None
    parts = urlsplit(value)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.path not in {"", "/"}
        or parts.query
        or parts.fragment
    ):
        raise TransferError("The source Supabase URL must be an HTTPS origin.")
    return f"https://{parts.hostname.lower()}:{parts.port if parts.port is not None else 443}"


def is_trusted_avatar(url: str, origin: str | None) -> bool:
    try:
        parts = urlsplit(url)
        port = parts.port if parts.port is not None else 443
        normalized = f"{parts.scheme}://{(parts.hostname or '').lower()}:{port}"
        if normalized != origin:
            # Without the original origin we cannot prove a Supabase URL safe.
            if (parts.hostname or "").endswith(".supabase.co"):
                raise TransferError("A Supabase avatar has no matching configured trusted origin.")
            return False
        path = unquote(parts.path)
        if (
            parts.username is not None
            or parts.password is not None
            or parts.fragment
            or any(character.isspace() or ord(character) < 32 for character in url)
            or not path.startswith("/storage/v1/object/public/avatars/")
            or any(segment in {".", ".."} for segment in path.split("/"))
            or "\\" in path
            or "%" in path
        ):
            raise TransferError("A trusted-origin avatar has an unsupported storage path.")
        return True
    except ValueError:
        raise TransferError("An avatar URL is malformed.") from None


def image_content_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    raise TransferError("A trusted avatar is not a supported PNG, JPEG, or WebP image.")


async def download_avatar(client: httpx.AsyncClient, url: str, origin: str) -> bytes:
    if not is_trusted_avatar(url, origin):
        raise TransferError("Refusing to download an untrusted avatar.")
    async with client.stream("GET", url, follow_redirects=False) as response:
        if response.status_code != 200:
            raise TransferError("A trusted avatar download failed; the export is incomplete.")
        data = bytearray()
        async for chunk in response.aiter_bytes():
            data.extend(chunk)
            if len(data) > MAX_AVATAR_BYTES:
                raise TransferError("A trusted avatar exceeds the transfer size limit.")
    image_content_type(bytes(data))
    return bytes(data)


def private_write(path: Path, data: bytes) -> None:
    with open(path, "xb", opener=lambda name, flags: os.open(name, flags, 0o600)) as output:
        output.write(data)


def private_path(archive: Path, filename: str) -> Path:
    if Path(filename).name != filename or filename in {"", ".", ".."}:
        raise TransferError("An archive entry has an unsafe file name.")
    path = archive / filename
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise TransferError("Archive entries must be regular private files (0600).")
    return path


def check_archive_directory(archive: Path) -> None:
    info = archive.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077:
        raise TransferError("The archive must be a private directory (0700), without symlinks.")


def read_archive_json(archive: Path, filename: str) -> Any:
    data = private_path(archive, filename).read_bytes()
    return json.loads(gzip.decompress(data) if filename.endswith(".gz") else data)


def stream_digest(path: Path, *, compressed: bool) -> str:
    digest = hashlib.sha256()
    opener = gzip.open if compressed else open
    with opener(path, "rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def read_manifest(archive: Path) -> dict[str, Any]:
    check_archive_directory(archive)
    manifest = read_archive_json(archive, "manifest.json")
    if (
        manifest.get("format_version") != FORMAT_VERSION
        or manifest.get("source_revision") != SOURCE_REVISION
        or manifest.get("target_revision") != TARGET_REVISION
        or not isinstance(manifest.get("tables"), dict)
    ):
        raise TransferError("Unsupported or incomplete transfer manifest.")
    for name, table in manifest["tables"].items():
        identifier(name)
        if table["file"] != f"public.{name}.copy.gz":
            raise TransferError("A table archive has an unexpected file name.")
        for column in table["columns"]:
            identifier(column["name"])
        if stream_digest(private_path(archive, table["file"]), compressed=True) != table["sha256"]:
            raise TransferError("A table archive failed its checksum; no data will be imported.")
    for entry in [manifest["auth"], manifest["profile_check"], *manifest["avatars"]]:
        compressed = entry["file"].endswith(".gz")
        if (
            stream_digest(private_path(archive, entry["file"]), compressed=compressed)
            != entry["sha256"]
        ):
            raise TransferError("An account or avatar archive failed its checksum.")
    return manifest


def parse_datetime(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def plan_accounts(
    users: list[dict[str, Any]], profile_ids: set[str], exported_at: datetime
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    active = []
    seen_ids: set[str] = set()
    seen_emails: set[str] = set()
    skipped = {"disabled": 0, "without_password": 0, "profiles_without_auth": 0}
    for user in users:
        user_id = str(UUID(str(user["id"])))
        if user_id in seen_ids:
            raise TransferError("The account snapshot contains duplicate user IDs.")
        seen_ids.add(user_id)
        email = (user.get("email") or "").strip().lower()
        if email:
            if email in seen_emails:
                raise TransferError(
                    "Normalized source email addresses conflict; import is blocked."
                )
            seen_emails.add(email)
        banned_until = parse_datetime(user.get("banned_until"))
        if user.get("deleted_at") or (banned_until and banned_until > exported_at):
            skipped["disabled"] += 1
            continue
        password_hash = user.get("encrypted_password") or ""
        if not email or not password_hash:
            skipped["without_password"] += 1
            continue
        if not BCRYPT.fullmatch(password_hash):
            raise TransferError(
                "A source account has an unsupported password hash; import is blocked."
            )
        if len(email) > 320 or "@" not in email or any(character.isspace() for character in email):
            raise TransferError(
                "A source account has an invalid normalized email; import is blocked."
            )
        if user_id not in profile_ids:
            raise TransferError(
                "A login account has no source profile; reconcile it before import."
            )
        if user.get("created_at") is None or user.get("updated_at") is None:
            raise TransferError("A source login account has missing timestamps; import is blocked.")
        active.append({**user, "id": user_id, "email": email})
    skipped["profiles_without_auth"] = len(profile_ids - seen_ids)
    return active, skipped


async def table_metadata(connection: asyncpg.Connection) -> dict[str, dict[str, Any]]:
    # asyncpg decodes PostgreSQL's internal "char" as bytes (including a truthy
    # b'\x00' for the empty identity/generated flags). Convert catalog values in
    # SQL so ordinary tables and columns are not mistaken for unsupported ones.
    rows = await connection.fetch(
        """SELECT c.relname AS name, c.relkind::text AS relkind,
            row_security_active(c.oid) AS rls_active
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') ORDER BY c.relname"""
    )
    tables = {}
    for row in rows:
        name = row["name"]
        identifier(name)
        if row["relkind"] != "r":
            raise TransferError("Partitioned tables require an explicit transfer strategy.")
        if row["rls_active"]:
            raise TransferError("The database role cannot prove a complete snapshot through RLS.")
        columns = await connection.fetch(
            """SELECT a.attname AS name, format_type(a.atttypid, a.atttypmod) AS type,
                n.nspname AS type_schema, a.attidentity <> '' AS has_identity,
                a.attgenerated <> '' AS is_generated,
                pg_get_expr(d.adbin, d.adrelid) AS default_value
            FROM pg_attribute a JOIN pg_type t ON t.oid = a.atttypid
            JOIN pg_namespace n ON n.oid = t.typnamespace
            LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
            WHERE a.attrelid = $1::regclass AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY a.attnum""",
            f"public.{name}",
        )
        if any(
            column["type_schema"] != "pg_catalog"
            or column["has_identity"]
            or (column["default_value"] or "").startswith("nextval(")
            for column in columns
        ):
            raise TransferError("A public table uses unsupported custom types or identity columns.")
        primary_key = await connection.fetch(
            """SELECT a.attname AS name FROM pg_index i
            CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY k(attnum, position)
            JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum
            WHERE i.indrelid = $1::regclass AND i.indisprimary ORDER BY k.position""",
            f"public.{name}",
        )
        tables[name] = {
            "columns": [{"name": column["name"], "type": column["type"]} for column in columns],
            "primary_key": [column["name"] for column in primary_key],
        }
        if any(column["is_generated"] for column in columns):
            raise TransferError("Generated public columns require an explicit transfer strategy.")
    return tables


def copy_query(name: str, table: dict[str, Any], *, without_avatar: bool = False) -> str:
    columns = [
        identifier(column["name"])
        for column in table["columns"]
        if not without_avatar or column["name"] != "avatar_url"
    ]
    # Source and target cluster locales may differ. Canonical text ordering also
    # makes composite keys deterministic without relying on either locale.
    order = ", ".join(f'{identifier(column)}::text COLLATE "C"' for column in table["primary_key"])
    fallback_order = 'to_jsonb(snapshot_row)::text COLLATE "C"'
    return (
        f"SELECT {', '.join(columns)} FROM public.{identifier(name)} AS snapshot_row "
        f"ORDER BY {order or fallback_order}"
    )


async def copy_digest(
    connection: asyncpg.Connection, query: str, output: Callable[[bytes], Any] | None = None
) -> tuple[str, int]:
    digest = hashlib.sha256()

    async def consume(chunk: bytes) -> None:
        digest.update(chunk)
        if output is not None:
            output(chunk)

    status = await connection.copy_from_query(query, output=consume, format="text")
    return digest.hexdigest(), int(status.split()[-1])


async def export_copy(
    connection: asyncpg.Connection, archive: Path, filename: str, query: str
) -> dict[str, Any]:
    with open(
        archive / filename, "xb", opener=lambda name, flags: os.open(name, flags, 0o600)
    ) as raw_output:
        with gzip.GzipFile(fileobj=raw_output, mode="wb", mtime=0) as output:
            digest, count = await copy_digest(connection, query, output.write)
    return {"file": filename, "sha256": digest, "count": count}


def serialize_json(value: Any) -> bytes:
    return json.dumps(value, default=str, sort_keys=True, indent=2).encode()


async def source_accounts(connection: asyncpg.Connection) -> list[dict[str, Any]]:
    required = {"id", "email", "encrypted_password", "created_at", "updated_at"}
    columns = {
        row["column_name"]
        for row in await connection.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'auth' AND table_name = 'users'"
        )
    }
    if not required.issubset(columns):
        raise TransferError("The source auth.users identity/password columns are unavailable.")
    if await connection.fetchval("SELECT row_security_active('auth.users'::regclass)"):
        raise TransferError("The source database role cannot read all authentication accounts.")
    selected = sorted(required) + [
        column if column in columns else f"NULL AS {column}"
        for column in ("banned_until", "deleted_at")
    ]
    return [
        dict(row)
        for row in await connection.fetch(
            f"SELECT {', '.join(selected)} FROM auth.users ORDER BY id"
        )
    ]


async def canonical_copy_settings(connection: asyncpg.Connection) -> None:
    # Text COPY is portable across the source PG17 and target PG18. PostgreSQL
    # itself encodes/decodes NULL, escapes, JSON, arrays and timestamps. Fix text
    # formatting on both connections so content checksums remain meaningful.
    await connection.execute("SET LOCAL DateStyle = 'ISO, YMD'")
    await connection.execute("SET LOCAL TimeZone = 'UTC'")
    await connection.execute("SET LOCAL IntervalStyle = 'postgres'")
    await connection.execute("SET LOCAL extra_float_digits = 3")
    await connection.execute("SET LOCAL bytea_output = 'hex'")
    await connection.execute("SET LOCAL client_encoding = 'UTF8'")
    await connection.execute("SET LOCAL lock_timeout = '15s'")


async def export_snapshot(
    settings: Mapping[str, str], archive: Path, *, dry_run: bool = False
) -> dict[str, Any]:
    if archive.exists():
        raise TransferError("The archive already exists; choose a new directory.")
    connection = await asyncpg.connect(**connection_options(settings))
    try:
        async with connection.transaction(isolation="repeatable_read", readonly=True):
            await canonical_copy_settings(connection)
            versions = await connection.fetch("SELECT version_num FROM public.alembic_version")
            if [row["version_num"] for row in versions] != [SOURCE_REVISION]:
                raise TransferError(
                    "Export requires exactly the supported source Alembic revision."
                )
            tables = await table_metadata(connection)
            if AUTH_TABLES.intersection(tables):
                raise TransferError(
                    "The source already contains local-auth tables; export is blocked."
                )
            profiles = await connection.fetch(
                "SELECT id, avatar_url FROM public.profiles ORDER BY id"
            )
            users = await source_accounts(connection)
            exported_at = await connection.fetchval("SELECT transaction_timestamp()")
            account_rows, skipped = plan_accounts(
                users, {str(profile["id"]) for profile in profiles}, exported_at
            )
            origin = trusted_origin(settings)
            avatars = [
                profile
                for profile in profiles
                if profile["avatar_url"] and is_trusted_avatar(profile["avatar_url"], origin)
            ]
            summary = {
                "tables": len(tables),
                "profiles": len(profiles),
                "auth_users": len(users),
                "login_accounts": len(account_rows),
                "avatars": len(avatars),
                **skipped,
            }
            if dry_run:
                return {"operation": "export-dry-run", **summary}
            archive.mkdir(mode=0o700, parents=False)
            manifest = {
                "format_version": FORMAT_VERSION,
                "source_revision": SOURCE_REVISION,
                "target_revision": TARGET_REVISION,
                "source_identity": database_identity(settings),
                "exported_at": exported_at.isoformat(),
                "tables": {},
                "avatars": [],
                "summary": summary,
                "original_avatar_urls": {
                    str(profile["id"]): profile["avatar_url"] for profile in profiles
                },
            }
            for name, table in tables.items():
                manifest["tables"][name] = {
                    **table,
                    **await export_copy(
                        connection, archive, f"public.{name}.copy.gz", copy_query(name, table)
                    ),
                }
            manifest["profile_check"] = await export_copy(
                connection,
                archive,
                "profiles-without-avatar.copy.gz",
                copy_query("profiles", tables["profiles"], without_avatar=True),
            )
            auth_data = serialize_json(users)
            private_write(archive / "auth-users.json.gz", gzip.compress(auth_data, mtime=0))
            manifest["auth"] = {
                "file": "auth-users.json.gz",
                "sha256": hashlib.sha256(auth_data).hexdigest(),
                "count": len(users),
            }
            manifest["contracts"] = [
                dict(row)
                for row in await connection.fetch(
                    "SELECT contract, installed_at FROM public.app_schema_contracts "
                    "ORDER BY contract"
                )
            ]
            async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
                for profile in avatars:
                    data = await download_avatar(client, profile["avatar_url"], origin)
                    user_id, version = str(profile["id"]), str(uuid4())
                    filename = f"avatar.{user_id}.bin"
                    private_write(archive / filename, data)
                    manifest["avatars"].append(
                        {
                            "user_id": user_id,
                            "version": version,
                            "file": filename,
                            "content_type": image_content_type(data),
                            "size": len(data),
                            "sha256": hashlib.sha256(data).hexdigest(),
                        }
                    )
            # This is the completion marker. Failed exports intentionally have no manifest.
            private_write(archive / "manifest.json", serialize_json(manifest))
        read_manifest(archive)
        return {"operation": "export", "verified": True, **summary}
    finally:
        await connection.close()


def dependency_order(tables: set[str], edges: list[tuple[str, str]]) -> list[str]:
    dependencies = {name: set() for name in tables}
    for child, parent in edges:
        if child in tables and child != parent:
            if parent not in tables:
                raise TransferError("A transferred table depends on an untransferred table.")
            dependencies[child].add(parent)
    ordered = []
    while dependencies:
        ready = sorted(name for name, parents in dependencies.items() if not parents)
        if not ready:
            raise TransferError("Cross-table foreign-key cycles require an explicit transfer plan.")
        ordered.extend(ready)
        for name in ready:
            del dependencies[name]
        for parents in dependencies.values():
            parents.difference_update(ready)
    return ordered


async def check_target(
    connection: asyncpg.Connection, manifest: dict[str, Any], *, require_empty: bool
) -> list[str]:
    versions = await connection.fetch("SELECT version_num FROM public.alembic_version")
    if [row["version_num"] for row in versions] != [TARGET_REVISION]:
        raise TransferError("The target must already be migrated to exactly revision 28.")
    tables = await table_metadata(connection)
    source_tables = set(manifest["tables"])
    if set(tables) != source_tables | AUTH_TABLES:
        raise TransferError(
            "Source and target public-table inventories do not match the migration."
        )
    for name in source_tables:
        if tables[name] != {
            "columns": manifest["tables"][name]["columns"],
            "primary_key": manifest["tables"][name]["primary_key"],
        }:
            raise TransferError("A target table does not match its source column/type contract.")
    if require_empty:
        for name in sorted(set(tables) - METADATA_TABLES - {"web_rule_agent_runtime"}):
            if await connection.fetchval(f"SELECT EXISTS(SELECT 1 FROM public.{identifier(name)})"):
                raise TransferError(
                    "The target contains application data; refusing to overwrite it."
                )
        contracts = await connection.fetch("SELECT contract FROM public.app_schema_contracts")
        if {row["contract"] for row in contracts} != KNOWN_CONTRACTS:
            raise TransferError(
                "The target schema-contract seeds do not match a fresh revision 28."
            )
        runtime = await connection.fetch("SELECT * FROM public.web_rule_agent_runtime")
        if len(runtime) != 1 or not pristine_runtime(dict(runtime[0])):
            raise TransferError("The target runtime seed has changed; refusing to overwrite it.")
    edges = await connection.fetch(
        """SELECT child.relname AS child, parent.relname AS parent,
            pn.nspname AS parent_schema
        FROM pg_constraint fk
        JOIN pg_class child ON child.oid = fk.conrelid
        JOIN pg_namespace cn ON cn.oid = child.relnamespace
        JOIN pg_class parent ON parent.oid = fk.confrelid
        JOIN pg_namespace pn ON pn.oid = parent.relnamespace
        WHERE fk.contype = 'f' AND cn.nspname = 'public'"""
    )
    data_tables = source_tables - METADATA_TABLES
    if any(edge["parent_schema"] != "public" for edge in edges if edge["child"] in data_tables):
        raise TransferError("The target still has external-schema foreign-key dependencies.")
    return dependency_order(data_tables, [(row["child"], row["parent"]) for row in edges])


def pristine_runtime(row: dict[str, Any]) -> bool:
    ignored = {"created_at", "updated_at"}
    return (
        row.get("id") == "default"
        and row.get("engine_snapshot") in ({}, "{}")
        and all(
            value is None
            for key, value in row.items()
            if key not in ignored | {"id", "engine_snapshot"}
        )
    )


async def verify_tables(
    connection: asyncpg.Connection, manifest: dict[str, Any], *, migrated_avatars: bool
) -> None:
    for name, table in manifest["tables"].items():
        if name in METADATA_TABLES:
            continue
        without_avatar = migrated_avatars and name == "profiles"
        expected = manifest["profile_check"] if without_avatar else table
        digest, count = await copy_digest(
            connection, copy_query(name, table, without_avatar=without_avatar)
        )
        if count != expected["count"] or digest != expected["sha256"]:
            raise TransferError("Target row counts or content checksums differ from the snapshot.")
    for contract in manifest["contracts"]:
        installed_at = await connection.fetchval(
            "SELECT installed_at FROM public.app_schema_contracts WHERE contract=$1",
            contract["contract"],
        )
        if installed_at != parse_datetime(contract["installed_at"]):
            raise TransferError("A legacy schema contract was not preserved.")


def accounts_from_archive(manifest: dict[str, Any], archive: Path) -> list[dict[str, Any]]:
    users = read_archive_json(archive, manifest["auth"]["file"])
    if len(users) != manifest["auth"]["count"]:
        raise TransferError("The authentication snapshot row count is inconsistent.")
    profile_ids = set(manifest["original_avatar_urls"])
    accounts, skipped = plan_accounts(users, profile_ids, parse_datetime(manifest["exported_at"]))
    if len(accounts) != manifest["summary"]["login_accounts"] or any(
        count != manifest["summary"][key] for key, count in skipped.items()
    ):
        raise TransferError("The account migration plan differs from the export.")
    return accounts


async def verify_accounts_and_avatars(
    connection: asyncpg.Connection, manifest: dict[str, Any], archive: Path
) -> None:
    accounts = accounts_from_archive(manifest, archive)
    rows = await connection.fetch("SELECT * FROM public.reader_users ORDER BY id")
    actual = {str(row["id"]): dict(row) for row in rows}
    if len(actual) != len(accounts):
        raise TransferError("The target login-account count differs from the snapshot.")
    for account in accounts:
        row = actual.get(account["id"])
        if row is None or any(
            (
                row["email"] != account["email"],
                row["password_hash"] != account["encrypted_password"],
                row["created_at"] != parse_datetime(account["created_at"]),
                row["updated_at"] != parse_datetime(account["updated_at"]),
            )
        ):
            raise TransferError("A migrated login account differs from the source snapshot.")
    for name in ("reader_sessions", "reader_refresh_tokens"):
        if await connection.fetchval(f"SELECT count(*) FROM public.{identifier(name)}"):
            raise TransferError(
                "New sessions exist; verification requires services to remain stopped."
            )
    if await connection.fetchval("SELECT count(*) FROM public.profile_avatars") != len(
        manifest["avatars"]
    ):
        raise TransferError("The target avatar count differs from the snapshot.")
    migrated_ids = {avatar["user_id"] for avatar in manifest["avatars"]}
    profile_rows = await connection.fetch("SELECT id, avatar_url FROM public.profiles")
    original_urls = manifest["original_avatar_urls"]
    if {str(row["id"]) for row in profile_rows} != set(original_urls):
        raise TransferError("The target profile identities differ from the snapshot.")
    if any(
        str(row["id"]) not in migrated_ids and row["avatar_url"] != original_urls[str(row["id"])]
        for row in profile_rows
    ):
        raise TransferError("An external or empty avatar reference was not preserved.")
    for avatar in manifest["avatars"]:
        row = await connection.fetchrow(
            "SELECT a.version, a.content_type, a.data, p.avatar_url FROM public.profile_avatars a "
            "JOIN public.profiles p ON p.id=a.user_id WHERE a.user_id=$1",
            UUID(avatar["user_id"]),
        )
        expected_url = f"/avatars/{avatar['user_id']}/{avatar['version']}"
        if row is None or any(
            (
                str(row["version"]) != avatar["version"],
                row["content_type"] != avatar["content_type"],
                hashlib.sha256(row["data"]).hexdigest() != avatar["sha256"],
                row["avatar_url"] != expected_url,
            )
        ):
            raise TransferError("A migrated avatar or profile reference differs from the snapshot.")


async def import_snapshot(
    settings: Mapping[str, str], archive: Path, *, dry_run: bool = False, verify_only: bool = False
) -> dict[str, Any]:
    manifest = read_manifest(archive)
    accounts = accounts_from_archive(manifest, archive)
    if database_identity(settings) == manifest["source_identity"]:
        raise TransferError("Source and target identify the same database; import is blocked.")
    connection = await asyncpg.connect(**connection_options(settings))
    try:
        async with connection.transaction(
            isolation="repeatable_read", readonly=dry_run or verify_only
        ):
            await canonical_copy_settings(connection)
            if not dry_run and not verify_only:
                names = sorted(set(manifest["tables"]) | AUTH_TABLES)
                await connection.execute(
                    "LOCK TABLE "
                    + ", ".join(f"public.{identifier(name)}" for name in names)
                    + " IN ACCESS EXCLUSIVE MODE"
                )
            order = await check_target(connection, manifest, require_empty=not verify_only)
            if dry_run:
                return {"operation": "import-dry-run", "target_empty": True, **manifest["summary"]}
            if not verify_only:
                await connection.execute("DELETE FROM public.web_rule_agent_runtime")
                for name in order:
                    table = manifest["tables"][name]
                    with gzip.open(private_path(archive, table["file"]), "rb") as source:
                        status = await connection.copy_to_table(
                            name,
                            schema_name="public",
                            source=source,
                            format="text",
                            columns=[column["name"] for column in table["columns"]],
                        )
                    if int(status.split()[-1]) != table["count"]:
                        raise TransferError("COPY imported an unexpected row count; rolling back.")
                for contract in manifest["contracts"]:
                    await connection.execute(
                        "INSERT INTO public.app_schema_contracts(contract, installed_at) "
                        "VALUES($1,$2) "
                        "ON CONFLICT(contract) DO UPDATE SET installed_at=EXCLUDED.installed_at",
                        contract["contract"],
                        parse_datetime(contract["installed_at"]),
                    )
                await verify_tables(connection, manifest, migrated_avatars=False)
                for account in accounts:
                    await connection.execute(
                        "INSERT INTO public.reader_users"
                        "(id,email,password_hash,created_at,updated_at) "
                        "VALUES($1,$2,$3,$4,$5)",
                        UUID(account["id"]),
                        account["email"],
                        account["encrypted_password"],
                        parse_datetime(account["created_at"]),
                        parse_datetime(account["updated_at"]),
                    )
                for avatar in manifest["avatars"]:
                    data = private_path(archive, avatar["file"]).read_bytes()
                    if (
                        len(data) != avatar["size"]
                        or image_content_type(data) != avatar["content_type"]
                    ):
                        raise TransferError("An archived avatar has inconsistent media metadata.")
                    await connection.execute(
                        "INSERT INTO public.profile_avatars(user_id,version,content_type,data) "
                        "VALUES($1,$2,$3,$4)",
                        UUID(avatar["user_id"]),
                        UUID(avatar["version"]),
                        avatar["content_type"],
                        data,
                    )
                    await connection.execute(
                        "UPDATE public.profiles SET avatar_url=$1 WHERE id=$2",
                        f"/avatars/{avatar['user_id']}/{avatar['version']}",
                        UUID(avatar["user_id"]),
                    )
            await verify_tables(connection, manifest, migrated_avatars=True)
            await verify_accounts_and_avatars(connection, manifest, archive)
        return {
            "operation": "verify" if verify_only else "import",
            "verified": True,
            **manifest["summary"],
        }
    finally:
        await connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subcommands = parser.add_subparsers(dest="operation", required=True)
    for operation in ("export", "import", "verify"):
        subparser = subcommands.add_parser(operation)
        subparser.add_argument("--archive", type=Path, required=True)
        subparser.add_argument(
            "--source-env" if operation == "export" else "--target-env", type=Path
        )
        if operation != "verify":
            subparser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.operation == "export":
            result = asyncio.run(
                export_snapshot(
                    settings_from_file(args.source_env), args.archive, dry_run=args.dry_run
                )
            )
        else:
            result = asyncio.run(
                import_snapshot(
                    settings_from_file(args.target_env),
                    args.archive,
                    dry_run=getattr(args, "dry_run", False),
                    verify_only=args.operation == "verify",
                )
            )
        print(json.dumps(result, sort_keys=True))
        return 0
    except TransferError as error:
        print(f"Transfer blocked: {error}", file=sys.stderr)
    except Exception as error:
        # asyncpg/HTTP errors can contain hashes, SQL rows, DSNs, signed URLs or
        # response bodies. Never print their message or traceback to deployment logs.
        print(
            f"Transfer failed ({type(error).__name__}); private details suppressed.",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
