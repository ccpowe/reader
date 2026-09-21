#!/usr/bin/env bash
set -euo pipefail

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
cd "$ROOT"

CONFIG=.env.docker
DOCKER_DIR=.docker
INPUT_DIR=$DOCKER_DIR/inputs
SECRET_DIR=$DOCKER_DIR/secrets
RUNTIME_DIR=$DOCKER_DIR/runtime
LOCK_FILE=$DOCKER_DIR/operation.lock
BASE_ENV=$RUNTIME_DIR/base.env
ACTIVE_ENV=$RUNTIME_DIR/active.env
CANDIDATE_ENV=$RUNTIME_DIR/candidate.env
X_STATE=$RUNTIME_DIR/x-enabled

die() { printf 'docker-init: %s\n' "$*" >&2; exit 1; }

check_private_directory() {
  local path=$1 owner mode
  test -d "$path" && test ! -L "$path" || die "$path must be a real directory"
  owner=$(stat -c %u "$path")
  mode=$(stat -c %a "$path")
  test "$owner" = "$(id -u)" || die "$path must be owned by the current user"
  test "$mode" = 700 || die "$path must have mode 0700 (found $mode)"
}

bootstrap_directories() {
  if test -e "$DOCKER_DIR"; then
    check_private_directory "$DOCKER_DIR"
  else
    (umask 077; mkdir "$DOCKER_DIR")
  fi
  for path in "$INPUT_DIR" "$SECRET_DIR" "$RUNTIME_DIR"; do
    if test -e "$path"; then check_private_directory "$path"; else (umask 077; mkdir "$path"); fi
  done
}

bootstrap_directories
exec 9>"$LOCK_FILE"
chmod 0600 "$LOCK_FILE"
flock -x 9

require_config() {
  test -f "$CONFIG" && test ! -L "$CONFIG" || die "copy .env.docker.example to .env.docker first"
}

atomic_text() {
  local path=$1 mode=$2
  python3 -c '
import os, pathlib, sys, tempfile
target = pathlib.Path(sys.argv[1])
mode = int(sys.argv[2], 8)
data = sys.stdin.buffer.read()
fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
try:
    os.fchmod(fd, mode)
    os.write(fd, data)
    os.fsync(fd)
    os.close(fd); fd = -1
    os.replace(temporary, target)
    directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(directory)
    finally: os.close(directory)
finally:
    if fd >= 0: os.close(fd)
    try: os.unlink(temporary)
    except FileNotFoundError: pass
' "$path" "$mode"
}

random_secret() {
  python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
}

ensure_input() {
  local path=$INPUT_DIR/$1
  if ! test -e "$path"; then (umask 077; : > "$path"); fi
  test -f "$path" && test ! -L "$path" || die "$path must be a regular file"
  test "$(stat -c %u "$path")" = "$(id -u)" || die "$path must be owned by the current user"
  test "$(stat -c %a "$path")" = 600 || die "$path must have mode 0600"
}

ensure_generated_secret() {
  local path=$SECRET_DIR/$1 value
  if test -e "$path"; then
    test -f "$path" && test ! -L "$path" || die "$path must be a regular file"
    test "$(stat -c %u "$path")" = "$(id -u)" || die "$path must be owned by the current user"
    test "$(stat -c %a "$path")" = 440 || die "$path must have mode 0440"
    test "$(stat -c %g "$path")" = "$(id -g)" || die "$path must use the deployment secret group"
    return
  fi
  value=$(random_secret)
  printf '%s\n' "$value" | atomic_text "$path" 0440
  chgrp "$(id -g)" "$path"
}

seal_input() {
  local name=$1
  ensure_input "$name"
  python3 - "$INPUT_DIR/$name" "$SECRET_DIR/$name" "$(id -u)" "$(id -g)" <<'PY'
import os, pathlib, stat, sys, tempfile
source, target = map(pathlib.Path, sys.argv[1:3])
expected_uid, target_gid = map(int, sys.argv[3:5])
metadata = source.lstat()
if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != expected_uid or stat.S_IMODE(metadata.st_mode) != 0o600:
    raise SystemExit(f'{source} must be a current-user-owned 0600 regular file')
data = source.read_bytes()
if len(data) > 1024 * 1024:
    raise SystemExit(f'{source} exceeds 1 MiB')
fd, temporary = tempfile.mkstemp(prefix=f'.{target.name}.', dir=target.parent)
try:
    os.fchmod(fd, 0o440)
    os.fchown(fd, expected_uid, target_gid)
    os.write(fd, data)
    os.fsync(fd)
    os.close(fd); fd = -1
    os.replace(temporary, target)
    directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(directory)
    finally: os.close(directory)
finally:
    if fd >= 0: os.close(fd)
    try: os.unlink(temporary)
    except FileNotFoundError: pass
PY
}

env_value() {
  local file=$1 key=$2
  awk -F= -v key="$key" '$1 == key { sub(/^[^=]*=/, ""); print; found=1 } END { if (!found) exit 1 }' "$file"
}

write_base_env() {
  local deployment project docker_gid secret_gid
  if test -f "$BASE_ENV"; then
    deployment=$(env_value "$BASE_ENV" READER_DEPLOYMENT_ID)
    project=$(env_value "$BASE_ENV" COMPOSE_PROJECT_NAME)
  else
    deployment=$(python3 -c 'import uuid; print(uuid.uuid4())')
    project=reader-${deployment%%-*}
  fi
  docker_gid=$(stat -c %g /var/run/docker.sock 2>/dev/null) || die "/var/run/docker.sock is unavailable"
  secret_gid=$(id -g)
  cat <<EOF | atomic_text "$BASE_ENV" 0600
COMPOSE_PROJECT_NAME=$project
READER_DEPLOYMENT_ID=$deployment
DOCKER_GID=$docker_gid
READER_SECRET_GID=$secret_gid
DB_DATA_NETWORK=$project-db-data
BROWSER_CONTROL_NETWORK=$project-browser-control
BROWSER_DATA_NETWORK=$project-browser-data
BROWSER_EGRESS_NETWORK=$project-browser-egress
APP_EGRESS_NETWORK=$project-app-egress
SCWEET_CONTROL_NETWORK=$project-scweet-control
X_EGRESS_NETWORK=$project-x-egress
EOF
}

initialize() {
  require_config
  ensure_input deepseek-api-key
  ensure_input openrouter-api-key
  ensure_input youtube-data-api-key
  ensure_input scweet-cookies.json
  ensure_generated_secret database-password
  ensure_generated_secret server-access-token
  ensure_generated_secret auth-jwt-secret
  ensure_generated_secret browser-manager-token
  ensure_generated_secret scweet-service-token
  seal_input deepseek-api-key
  seal_input openrouter-api-key
  seal_input youtube-data-api-key
  seal_input scweet-cookies.json
  write_base_env
  if ! test -f "$X_STATE"; then printf '0\n' | atomic_text "$X_STATE" 0600; fi
}

compose_with() {
  local image_env=$1; shift
  docker compose --env-file "$CONFIG" --env-file "$BASE_ENV" --env-file "$image_env" "$@"
}

wait_healthy() {
  local env_file=$1 service=$2 attempts=${3:-60} container state
  container=$(compose_with "$env_file" ps -q "$service")
  test -n "$container" || die "$service container was not created"
  for ((i=0; i<attempts; i++)); do
    state=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container")
    case "$state" in
      healthy|running) return 0 ;;
      unhealthy|exited|dead) docker logs --tail 80 "$container" >&2 || true; die "$service entered $state" ;;
    esac
    sleep 2
  done
  docker logs --tail 80 "$container" >&2 || true
  die "$service did not become healthy"
}

build_candidate() {
  local with_x=$1 stamp reader_tag browser_tag scweet_tag reader_id browser_id scweet_id
  stamp=$(git rev-parse --short=12 HEAD)-$(date -u +%Y%m%d%H%M%S)
  reader_tag=reader-backend:$stamp
  browser_tag=reader-browser:$stamp
  scweet_tag=reader-scweet:$stamp
  docker build --file services/browser_runtime/Dockerfile.browser --tag "$browser_tag" .
  docker build --file backend/Dockerfile --tag "$reader_tag" .
  browser_id=$(docker image inspect --format '{{.Id}}' "$browser_tag")
  reader_id=$(docker image inspect --format '{{.Id}}' "$reader_tag")
  scweet_id=reader-scweet-unavailable
  if test "$with_x" = 1; then
    docker build --file services/scweet_service/Dockerfile --tag "$scweet_tag" services
    scweet_id=$(docker image inspect --format '{{.Id}}' "$scweet_tag")
  elif test -f "$ACTIVE_ENV"; then
    scweet_id=$(env_value "$ACTIVE_ENV" SCWEET_IMAGE_ID || printf reader-scweet-unavailable)
  fi
  cat <<EOF | atomic_text "$CANDIDATE_ENV" 0600
READER_IMAGE_ID=$reader_id
BROWSER_IMAGE_ID=$browser_id
SCWEET_IMAGE_ID=$scweet_id
EOF
  if test "$with_x" = 1; then
    compose_with "$CANDIDATE_ENV" --profile scweet config --quiet
  else
    compose_with "$CANDIDATE_ENV" config --quiet
  fi
}

validate_cookie() {
  python3 - "$INPUT_DIR/scweet-cookies.json" <<'PY'
import json, pathlib, sys
try:
    value=json.loads(pathlib.Path(sys.argv[1]).read_text())
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit('scweet-cookies.json must contain valid JSON') from exc
if not isinstance(value, list) or not value:
    raise SystemExit('scweet-cookies.json must contain at least one account')
for record in value:
    cookies = record.get('cookies') if isinstance(record, dict) else None
    if not isinstance(cookies, dict) or not str(cookies.get('auth_token', '')).strip():
        raise SystemExit('each Scweet account must contain cookies.auth_token')
PY
}

drain_active() {
  test -f "$ACTIVE_ENV" || return 0
  local manager expected actual label deployment containers volumes running_core
  compose_with "$ACTIVE_ENV" --profile scweet stop worker >/dev/null
  deployment=$(env_value "$BASE_ENV" READER_DEPLOYMENT_ID)
  manager=$(compose_with "$ACTIVE_ENV" ps -a -q browser-manager)
  if test -z "$manager" || test "$(docker inspect --format '{{.State.Running}}' "$manager")" != true; then
    containers=$(docker ps -aq \
      --filter label=io.reader.browser-task.managed=true \
      --filter "label=io.reader.browser-task.deployment=$deployment")
    volumes=$(docker volume ls -q \
      --filter label=io.reader.browser-task.managed=true \
      --filter "label=io.reader.browser-task.deployment=$deployment")
    running_core=$(compose_with "$ACTIVE_ENV" ps -q api worker browser-manager browser-reaper)
    test -z "$containers" && test -z "$volumes" \
      || die "browser manager is unavailable while owned task resources remain"
    test -z "$running_core" || die "browser manager is unavailable while deployment services are running"
    return 0
  fi
  expected=$(env_value "$ACTIVE_ENV" READER_IMAGE_ID)
  actual=$(docker inspect --format '{{.Image}}' "$manager")
  label=$(docker inspect --format '{{index .Config.Labels "io.reader.deployment"}}' "$manager")
  test "$actual" = "$expected" || die "refusing to drain a manager with an unexpected image"
  test "$label" = "$deployment" || die "refusing to drain another deployment"
  docker exec "$manager" reader-browser-admin drain
}

stop_active_apps() {
  test -f "$ACTIVE_ENV" || return 0
  compose_with "$ACTIVE_ENV" --profile scweet stop worker api browser-reaper browser-manager >/dev/null
}

remove_scweet() {
  local env_file=$1 scweet
  scweet=$(compose_with "$env_file" --profile scweet ps -a -q scweet)
  if test -n "$scweet"; then
    compose_with "$env_file" --profile scweet stop scweet >/dev/null
    compose_with "$env_file" --profile scweet rm -f scweet >/dev/null
  fi
}

run_up() {
  local requested=${1:-retain} with_x
  initialize
  case "$requested" in
    with-x) with_x=1 ;;
    without-x) with_x=0 ;;
    retain) with_x=$(cat "$X_STATE") ;;
    *) die "unsupported up mode" ;;
  esac
  test "$with_x" = 0 || test "$with_x" = 1 || die "invalid saved X state"
  if test "$with_x" = 1; then validate_cookie; fi
  build_candidate "$with_x"

  drain_active
  stop_active_apps

  compose_with "$CANDIDATE_ENV" --profile tools run --rm pg-init
  compose_with "$CANDIDATE_ENV" up -d postgres
  wait_healthy "$CANDIDATE_ENV" postgres
  compose_with "$CANDIDATE_ENV" --profile tools run --rm migrate
  compose_with "$CANDIDATE_ENV" --profile tools run --rm state-init
  compose_with "$CANDIDATE_ENV" --profile tools run --rm network-init

  # From this point onward candidate containers own the fixed service names.
  # Persist their exact image identity first so a failed start remains
  # recoverable by the next up or down operation.
  atomic_text "$ACTIVE_ENV" 0600 < "$CANDIDATE_ENV"
  compose_with "$CANDIDATE_ENV" create browser-manager browser-reaper api worker >/dev/null
  compose_with "$CANDIDATE_ENV" up -d browser-manager
  wait_healthy "$CANDIDATE_ENV" browser-manager
  compose_with "$CANDIDATE_ENV" up -d browser-reaper api
  wait_healthy "$CANDIDATE_ENV" api
  if test "$with_x" = 1; then
    compose_with "$CANDIDATE_ENV" --profile scweet up -d scweet
    wait_healthy "$CANDIDATE_ENV" scweet
  else
    remove_scweet "$CANDIDATE_ENV"
  fi
  compose_with "$CANDIDATE_ENV" up -d worker
  printf '%s\n' "$with_x" | atomic_text "$X_STATE" 0600
  local bind port
  bind=$(awk -F= '$1=="READER_API_BIND_HOST" {sub(/^[^=]*=/,""); print}' "$CONFIG")
  port=$(awk -F= '$1=="READER_API_PORT" {sub(/^[^=]*=/,""); print}' "$CONFIG")
  printf 'Reader is running at %s:%s\n' "${bind:-127.0.0.1}" "${port:-8000}"
}

run_down() {
  initialize
  test -f "$ACTIVE_ENV" || die "Reader has no active Docker deployment"
  drain_active
  compose_with "$ACTIVE_ENV" --profile scweet --profile tools down
}

run_status() {
  initialize
  if ! test -f "$ACTIVE_ENV"; then printf 'Reader has no active Docker deployment.\n'; return; fi
  compose_with "$ACTIVE_ENV" --profile scweet ps
  local port bind code
  bind=$(awk -F= '$1=="READER_API_BIND_HOST" {sub(/^[^=]*=/,""); print}' "$CONFIG")
  port=$(awk -F= '$1=="READER_API_PORT" {sub(/^[^=]*=/,""); print}' "$CONFIG")
  bind=${bind:-127.0.0.1}; port=${port:-8000}
  code=$(curl --silent --output /dev/null --write-out '%{http_code}' "http://$bind:$port/worker-ready" || true)
  printf 'worker-ready HTTP %s (503 is expected when no translation provider key is configured)\n' "${code:-unreachable}"
}

run_token() {
  initialize
  cat "$SECRET_DIR/server-access-token"
}

usage() {
  cat <<'EOF'
Usage: ./docker-init.sh init|up [--with-x|--without-x]|down|status|token
EOF
}

case ${1:-} in
  init) test "$#" = 1 || die "init accepts no arguments"; initialize; printf 'Docker inputs are ready under .docker/inputs.\n' ;;
  up)
    case ${2:-} in
      '') mode=retain ;;
      --with-x) mode=with-x ;;
      --without-x) mode=without-x ;;
      *) die "up accepts only --with-x or --without-x" ;;
    esac
    test "$#" -le 2 || die "too many arguments"
    run_up "$mode"
    ;;
  down) test "$#" = 1 || die "down accepts no arguments"; run_down ;;
  status) test "$#" = 1 || die "status accepts no arguments"; run_status ;;
  token) test "$#" = 1 || die "token accepts no arguments"; run_token ;;
  *) usage; exit 2 ;;
esac
