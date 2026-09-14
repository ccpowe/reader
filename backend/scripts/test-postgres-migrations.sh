#!/usr/bin/env bash
set -euo pipefail

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
backend_dir="$(dirname -- "$script_dir")"
container_name="reader-postgres-migration-test-$$"
database_password="reader-test-only"

cleanup() {
  docker rm -f "$container_name" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

docker run --detach --rm --memory 384m --cpus 1 \
  --name "$container_name" \
  --env POSTGRES_PASSWORD="$database_password" \
  --env POSTGRES_DB=reader_test \
  --publish 127.0.0.1::5432 \
  postgres:18 >/dev/null

for _attempt in $(seq 1 30); do
  # The image briefly starts a Unix-socket-only server while initialising the
  # database. Probe TCP so only the final server can satisfy readiness.
  if docker exec "$container_name" pg_isready --host 127.0.0.1 \
    --username postgres --dbname reader_test >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! docker exec "$container_name" pg_isready --host 127.0.0.1 \
  --username postgres --dbname reader_test >/dev/null 2>&1; then
  echo "temporary PostgreSQL did not become ready" >&2
  exit 1
fi

guard_token="$(tr -d '-' </proc/sys/kernel/random/uuid)"
docker exec --interactive "$container_name" \
  psql --set=ON_ERROR_STOP=1 --username postgres --dbname reader_test \
  --set=guard_token="$guard_token" >/dev/null <<'SQL'
CREATE SCHEMA reader_test_guard;
CREATE TABLE reader_test_guard.ownership (
  singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
  token text NOT NULL
);
INSERT INTO reader_test_guard.ownership (token) VALUES (:'guard_token');
SQL

database_port="$(docker port "$container_name" 5432/tcp | awk -F: 'END {print $NF}')"
cd "$backend_dir"
# Targeted persistence checks need the migration bootstrap test in the targets;
# each invocation owns a fresh, empty database.
if [ "$#" -eq 0 ]; then
  set -- tests/test_postgres_migrations.py tests/test_translation_quota_postgres.py tests/test_ranking_saved_postgres.py tests/test_x_feed_postgres.py tests/test_candidate_recovery_postgres.py tests/test_profile_recovery_postgres.py tests/test_content_retention.py tests/test_translation_lifecycle_postgres.py tests/test_crawl4ai_persistence_postgres.py tests/test_web_rule_jobs_postgres.py tests/test_web_rule_agent_e2e_postgres.py tests/test_web_rule_scan_leases_postgres.py tests/test_web_feed_discovery_postgres.py tests/test_reader_auth_postgres.py
fi
READER_RUN_POSTGRES_TESTS=1 \
READER_TEST_POSTGRES_DSN="postgresql://postgres:${database_password}@127.0.0.1:${database_port}/reader_test" \
READER_TEST_POSTGRES_GUARD="$guard_token" \
uv run pytest -q "$@"
