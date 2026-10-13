#!/bin/sh
# Talk to Reachy out loud.
#
#   ./voice.sh          press Enter, speak for 4 s, Reachy acts and answers. Ctrl-D to quit.
#   ./voice.sh --once   one turn
#   VOICE_SECONDS=6 ./voice.sh
#
# The mic WAV is base64-piped into the sandbox; agent/minimal_agent.py --audio hands it to
# Nemotron 3 Nano Omni on the Thor's vLLM, which does the listening and picks the tool.
# Same policy path as talk.sh. The reply is spoken by robot/speaker.py (/say, Kokoro TTS)
# on the host; the sandbox cannot reach /say.
cd "$(dirname "$0")"
WAV=${TMPDIR:-/tmp}/reachy-voice.wav
MIC="plughw:CARD=Audio,DEV=0"

turn() {
  echo "listening (${VOICE_SECONDS:-4} s)..."
  arecord -q -D "$MIC" -f S16_LE -r 16000 -c 1 -d "${VOICE_SECONDS:-4}" "$WAV" || return
  out=$(base64 -w0 "$WAV" | openshell sandbox exec --name reachy-agent --no-tty -- \
    /opt/venv/bin/python /sandbox/minimal_agent.py --audio 2>&1 \
    | grep -E '^\[(model chose|robot said|reply|busy)\]|Error')
  printf '%s\n' "$out"
  reply=$(printf '%s\n' "$out" | sed -n 's/^\[reply\] *//p')
  [ -n "$reply" ] && printf '%s' "$reply" \
    | python3 -c 'import json,sys; print(json.dumps({"text": sys.stdin.read()}))' \
    | curl -s -X POST 127.0.0.1:8042/say -H 'content-type: application/json' -d @- >/dev/null
}

if [ "$1" = "--once" ]; then
  turn
  exit
fi

echo "Press Enter to talk to Reachy. Ctrl-D to quit."
while IFS= read -r _; do
  turn
done
