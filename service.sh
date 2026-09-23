#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
session=datareplay
action="${1:-status}"
if (( $# )); then shift; fi
case "$action" in
  start)
    if tmux has-session -t "=$session" 2>/dev/null; then
      echo 'DataReplay supervisor is already running'
      exit 0
    fi
    printf -v launch '%q ' "$PWD/supervise.sh" "$@"
    tmux new-session -d -s "$session" -c "$PWD" "exec $launch"
    echo 'DataReplay supervisor started; log: runtime/server.log'
    ;;
  stop)
    if tmux has-session -t "=$session" 2>/dev/null; then
      supervisor_pid=$(tmux display-message -p -t "$session:0.0" '#{pane_pid}')
      kill -TERM "$supervisor_pid"
      for (( i=0; i<50; i++ )); do
        if ! tmux has-session -t "=$session" 2>/dev/null; then break; fi
        sleep 0.1
      done
      if tmux has-session -t "=$session" 2>/dev/null; then
        echo 'Supervisor has not stopped yet; check runtime/server.log' >&2
        exit 1
      fi
    fi
    echo 'DataReplay stopped'
    ;;
  restart) "$0" stop; "$0" start "$@" ;;
  status)
    tmux has-session -t "=$session" 2>/dev/null || { echo 'DataReplay is stopped'; exit 1; }
    echo 'DataReplay supervisor is running'
    if [[ -f runtime/server.pid ]]; then
      ps -p "$(cat runtime/server.pid)" -o pid,etime,args
    else
      echo 'Server is restarting; check runtime/server.log'
      exit 1
    fi
    ;;
  logs) tail -n 80 -f runtime/server.log ;;
  *) echo "Usage: $0 {start|stop|restart|status|logs} [server arguments]" >&2; exit 2 ;;
esac
