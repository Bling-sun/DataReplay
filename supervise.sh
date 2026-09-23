#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
mkdir -p runtime
exec >>runtime/server.log 2>&1
child_pid=''
cleanup() {
  trap - EXIT INT TERM HUP
  if [[ -n "$child_pid" ]]; then
    kill "$child_pid" 2>/dev/null || true
    wait "$child_pid" 2>/dev/null || true
  fi
  rm -f runtime/server.pid
}
trap cleanup EXIT
trap 'exit 0' INT TERM HUP
while true; do
  echo "[$(date -Is)] Starting DataReplay"
  ./run_server.sh "$@" &
  child_pid=$!
  echo "$child_pid" >runtime/server.pid
  wait "$child_pid"
  result=$?
  child_pid=''
  rm -f runtime/server.pid
  echo "[$(date -Is)] DataReplay exited ($result); restarting in 5 seconds"
  sleep 5 &
  child_pid=$!
  wait "$child_pid"
  child_pid=''
done
