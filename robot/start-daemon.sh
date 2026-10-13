#!/bin/sh
# Start the Reachy Mini daemon against the USB robot, in the background.
# The daemon owns the serial port and exposes the REST API on 127.0.0.1:8000.
# Log: logs/reachy-daemon.log. Stop it with: pkill -f reachy-mini-daemon
set -e
cd "$(dirname "$0")"
mkdir -p logs
if curl -s -m 2 http://127.0.0.1:8000/api/daemon/status >/dev/null 2>&1; then
  echo "daemon already running on :8000"
  exit 0
fi
nohup venv/bin/reachy-mini-daemon \
  --fastapi-host 0.0.0.0 --fastapi-port 8000 \
  --log-level INFO \
  >> logs/reachy-daemon.log 2>&1 &
echo "started daemon pid $!"
for i in $(seq 1 40); do
  if curl -s -m 2 http://127.0.0.1:8000/api/daemon/status >/dev/null 2>&1; then
    echo "daemon ready"; exit 0
  fi
  sleep 1
done
echo "daemon did not come up; see logs/reachy-daemon.log" >&2
exit 1
