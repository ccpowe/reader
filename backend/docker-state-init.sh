#!/bin/sh
set -eu

state_dir=${APP_BROWSER_TASK_STATE_DIR:-/var/lib/reader-browser}
test -d "$state_dir"
test ! -L "$state_dir"
owner=$(stat -c '%u:%g' "$state_dir")
case "$owner" in
  0:0|10001:20000) ;;
  *) echo "browser state volume has unexpected owner: $owner" >&2; exit 1 ;;
esac
chown 10001:20000 "$state_dir"
test "$(stat -c '%u:%g' "$state_dir")" = 10001:20000

# This process has shared GID traversal but no capability to read the 0600 HMAC
# key. Validate metadata only and leave SQLite recovery files untouched.
python - "$state_dir" <<'PY'
import pathlib
import stat
import sys

root = pathlib.Path(sys.argv[1])
stable = {
    "tasks.sqlite3",
    "tasks.sqlite3-wal",
    "tasks.sqlite3-shm",
    "tasks.sqlite3-journal",
    "controller.lock",
    "capability.key",
}
for child in root.iterdir():
    metadata = child.lstat()
    is_capability = child.name == "capability.key" or child.name.startswith(
        ".capability-key-"
    )
    if child.name not in stable and not is_capability:
        raise SystemExit(f"unexpected browser state object: {child.name}")
    if not stat.S_ISREG(metadata.st_mode):
        raise SystemExit(f"browser state object is not regular: {child.name}")
    sqlite_sidecar = child.name in {
        "tasks.sqlite3-wal",
        "tasks.sqlite3-shm",
        "tasks.sqlite3-journal",
    }
    allowed_uids = {10_001, 10_002} if sqlite_sidecar else {10_001}
    if metadata.st_uid not in allowed_uids or metadata.st_gid != 20_000:
        raise SystemExit(f"browser state object has unsafe ownership: {child.name}")
    mode = stat.S_IMODE(metadata.st_mode)
    if is_capability and mode != 0o600:
        raise SystemExit(f"browser capability object has unsafe mode: {child.name}")
    if child.name == "controller.lock" and mode not in {0o600, 0o640, 0o660}:
        raise SystemExit("browser controller lock has unsafe mode")
    if not is_capability and child.name != "controller.lock" and mode != 0o660:
        raise SystemExit(f"browser SQLite object has unsafe mode: {child.name}")
PY
