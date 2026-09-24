#!/bin/sh
set -eu
# Only initialize the dedicated credential volume; never rewrite credentials.
state_dir=/var/lib/reader-codex
test -d "$state_dir" && test ! -L "$state_dir"
owner=$(stat -c '%u:%g' "$state_dir")
case "$owner" in
  0:0)
    chmod 0700 "$state_dir"
    chown 10001:10001 "$state_dir"
    ;;
  10001:10001) ;;
  *) echo 'Codex auth volume has unexpected ownership' >&2; exit 1 ;;
esac
test "$(stat -c '%u:%g:%a' "$state_dir")" = 10001:10001:700
