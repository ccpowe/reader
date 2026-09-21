#!/bin/sh
set -eu

control_root=/run/reader-browser-task
browser_dir=$control_root/browser
adapter_dir=$control_root/adapter
lp_socket=$browser_dir/lightpanda.sock
cli_socket=$browser_dir/cli.sock
bridge_socket=$adapter_dir/bridge.sock
purpose=${READER_BROWSER_TASK_PURPOSE:?missing task purpose}

test "$purpose" = explore || test "$purpose" = render
test ! -L "$browser_dir" && test ! -L "$adapter_dir"
test "$(stat -c '%u:%g:%a' "$browser_dir")" = "999:20000:711"
test "$(stat -c '%u:%g:%a' "$adapter_dir")" = "10001:20000:711"
test ! -e "$lp_socket" && test ! -e "$cli_socket"

lp_pid=
lp_relay_pid=
bridge_relay_pid=
cli_relay_pid=
cleanup() {
  for pid in "$cli_relay_pid" "$bridge_relay_pid" "$lp_relay_pid" "$lp_pid"; do
    test -z "$pid" || kill "$pid" >/dev/null 2>&1 || true
  done
  for pid in "$cli_relay_pid" "$bridge_relay_pid" "$lp_relay_pid" "$lp_pid"; do
    test -z "$pid" || wait "$pid" >/dev/null 2>&1 || true
  done
}
trap cleanup EXIT HUP INT TERM

umask 0007
export AGENT_BROWSER_SOCKET_DIR=/tmp/reader-cli/sockets
export AGENT_BROWSER_IDLE_TIMEOUT_MS=0
mkdir -p "$AGENT_BROWSER_SOCKET_DIR"
chmod 0700 /tmp/reader-cli "$AGENT_BROWSER_SOCKET_DIR"
/reader/lightpanda serve \
  --host 127.0.0.1 --port 9222 \
  --http-proxy http://127.0.0.1:9 \
  --block-private-networks --block-cidrs 0.0.0.0/0 --block-cidrs ::/0 \
  --v8-max-heap-mb 64 --watchdog-ms 5000 \
  --http-timeout 1000 --http-connect-timeout 1000 \
  --http-max-concurrent 2 --ws-max-concurrent 0 \
  --cdp-max-message-size 33554432 --disable-metrics &
lp_pid=$!

socat "UNIX-LISTEN:$lp_socket,fork,user=999,group=20000,mode=0660" \
  TCP:127.0.0.1:9222 &
lp_relay_pid=$!

if test "$purpose" = explore; then
  probe_i=0
  while test "$probe_i" -lt 300 && test ! -S "$bridge_socket"; do
    kill -0 "$lp_pid" && kill -0 "$lp_relay_pid"
    probe_i=$((probe_i + 1))
    sleep 0.1
  done
  test -S "$bridge_socket"

  socat TCP-LISTEN:9333,bind=127.0.0.1,reuseaddr,fork \
    "UNIX-CONNECT:$bridge_socket" &
  bridge_relay_pid=$!
  socat "UNIX-LISTEN:$cli_socket,user=999,group=20000,mode=0660" \
    EXEC:'/usr/bin/python3 /reader/cli_runner.py' &
  cli_relay_pid=$!
fi

wait "$lp_pid"
