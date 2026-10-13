#!/bin/sh
# Always-on listener as a background process.
#   robot/listen.sh start     mic open, log to robot/logs/listen.log
#   robot/listen.sh log       follow the log (Ctrl-C stops following, not the listener)
#   robot/listen.sh stop
#   robot/listen.sh status
cd "$(dirname "$0")"
mkdir -p logs
PID=logs/listen.pid
LOG=logs/listen.log

running() { [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; }

case "$1" in
  start)
    if running; then echo "already running (pid $(cat "$PID"))"; exit 0; fi
    nohup venv/bin/python -u listen.py >> "$LOG" 2>&1 &
    echo $! > "$PID"
    echo "listener started (pid $!). Log: robot/listen.sh log" ;;
  stop)
    if running; then kill "$(cat "$PID")" && echo "stopped"; else echo "not running"; fi
    rm -f "$PID" ;;
  log)    tail -n 20 -f "$LOG" ;;
  status) if running; then echo "running (pid $(cat "$PID"))"; else echo "not running"; fi ;;
  *)      echo "usage: $0 start|stop|log|status"; exit 1 ;;
esac
