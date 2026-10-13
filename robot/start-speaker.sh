#!/bin/sh
# Start the trusted speaker service on 127.0.0.1:8042 (the sandbox sees it as
# host.openshell.internal:8042). Also makes sure the Reachy card's volume is up.
# Log: logs/speaker.log   Stop: pkill -f 'uvicorn speaker:app'
set -e
cd "$(dirname "$0")"
mkdir -p logs
amixer -q -c Audio sset PCM,0 100% unmute 2>/dev/null || true
amixer -q -c Audio sset PCM,1 100% unmute 2>/dev/null || true
if curl -s -m 2 http://127.0.0.1:8042/sounds >/dev/null 2>&1; then
  echo "speaker service already running on :8042"; exit 0
fi
nohup venv/bin/uvicorn speaker:app --host 127.0.0.1 --port 8042 >> logs/speaker.log 2>&1 &
for i in $(seq 1 15); do
  curl -s -m 2 http://127.0.0.1:8042/sounds >/dev/null 2>&1 && { echo "speaker service ready"; exit 0; }
  sleep 1
done
echo "speaker service did not come up; see logs/speaker.log" >&2; exit 1
