#!/bin/sh
# Prove the local model does tool calling, straight against vLLM (no OpenShell, no robot).
# Sends the same move_head tool the agent uses and the sentence "Reachy, look left".
BASE="${1:-http://127.0.0.1:8001/v1}"
MODEL="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"

echo "== models served =="
curl -s "$BASE/models" | python3 -c 'import json,sys; [print(" ", m["id"]) for m in json.load(sys.stdin)["data"]]'

echo "== tool call test =="
start=$(date +%s.%N)
curl -s "$BASE/chat/completions" -H 'content-type: application/json' -d @- <<'EOF' > /tmp/reachy-vllm-test.json
{
  "model": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
  "messages": [
    {"role": "system", "content": "You control a Reachy Mini robot. Only call move_head when the user asks Reachy to look left, right, up, down, or front."},
    {"role": "user", "content": "Reachy, look left."}
  ],
  "tools": [{"type": "function", "function": {"name": "move_head",
    "description": "Move Reachy's head through fixed directions, in order.",
    "parameters": {"type": "object", "properties": {"directions": {"type": "array",
      "items": {"type": "string", "enum": ["left", "right", "up", "down", "front"]}}}, "required": ["directions"]}}}],
  "tool_choice": "auto",
  "max_tokens": 512
}
EOF
end=$(date +%s.%N)
python3 - <<'EOF'
import json
d = json.load(open("/tmp/reachy-vllm-test.json"))
if "error" in d:
    print("ERROR:", d["error"]); raise SystemExit(1)
m = d["choices"][0]["message"]
tc = m.get("tool_calls") or []
print("  tool_calls:", [(t["function"]["name"], t["function"]["arguments"]) for t in tc] or "NONE")
print("  content   :", (m.get("content") or "")[:120])
u = d.get("usage", {}); print("  tokens    :", u.get("prompt_tokens"), "in /", u.get("completion_tokens"), "out")
EOF
echo "  latency   : $(echo "$end - $start" | bc) s"
