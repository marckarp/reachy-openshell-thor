"""The whole idea in one file: sentence in, model picks a tool, Python moves the robot.

Runs INSIDE the OpenShell sandbox (so the model key is a placeholder and the
robot request crosses the policy). Same model, same tool, same poses as the
full app in src/, with nothing else.

  openshell sandbox exec --name reachy-agent --no-tty -- \
      /opt/venv/bin/python /sandbox/minimal_agent.py "Reachy, look left"
"""

import json
import math
import os
import sys
import time

import httpx
from openai import OpenAI

MODEL = os.getenv("CHAT_MODEL_NAME", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning")
FALLBACK_MODEL = os.getenv("CHAT_FALLBACK_MODEL", "nvidia/nemotron-3-super-120b-a12b")  # on 503 busy
# Where the model lives: build.nvidia.com by default, or the Thor's own vLLM when
# CHAT_BASE_URL=http://host.openshell.internal:8001/v1 is set on the sandbox.
BASE_URL = os.getenv("CHAT_BASE_URL", "https://integrate.api.nvidia.com/v1")
API_KEY = os.getenv("NVIDIA_API_KEY") or "unused"  # local vLLM needs no key
# Nemotron "thinks" before answering. For a one-word tool choice that is ~4 s of extra
# tokens on the Thor versus ~1 s without. Off by default on the local server.
THINKING = os.getenv("CHAT_THINKING", "1" if "integrate.api.nvidia.com" in BASE_URL else "0") == "1"
EXTRA = {} if THINKING else {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
ROBOT = "http://host.openshell.internal:8000"    # the Reachy daemon, seen through OpenShell
SPEAKER = "http://host.openshell.internal:8042"  # trusted speaker service (robot/speaker.py)
SOUNDS = ["confused", "impatient", "wake_up", "sleep", "dance", "count"]
DENIED_SOUND = "confused"  # Reachy cannot talk yet, so it chirps when OpenShell says no

# 1. What the model is told.
SYSTEM_PROMPT = (
    "You control a Reachy Mini robot. Only call move_head when the user asks Reachy to look "
    "left, right, up, down, or front. Only call play_sound when the user asks for a sound or a "
    "mood like confused, impatient, wake up, sleep, dance, or count. If a tool result says "
    "policy_denied, say that OpenShell policy blocked the action and do not retry. "
    "Keep replies to one short sentence."
)

# 2. The only thing the model may choose. The enum is the entire robot vocabulary.
TOOLS = [{
    "type": "function",
    "function": {
        "name": "move_head",
        "description": "Move Reachy's head through fixed directions, in order.",
        "parameters": {
            "type": "object",
            "properties": {"directions": {"type": "array", "items": {
                "type": "string", "enum": ["left", "right", "up", "down", "front"]}}},
            "required": ["directions"],
        },
    },
}, {
    "type": "function",
    "function": {
        "name": "play_sound",
        "description": "Play one of Reachy's built-in sounds through its speaker.",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string", "enum": SOUNDS}},
            "required": ["name"],
        },
    },
}]

# 3. Python's table: word -> (pitch, yaw) in radians. The model never sees numbers.
POSES = {
    "left": (0.0, math.radians(40)), "right": (0.0, math.radians(-40)),
    "up": (math.radians(-30), 0.0), "down": (math.radians(30), 0.0), "front": (0.0, 0.0),
}


def play_sound(name: str) -> dict:
    """Ask the trusted speaker service for one named sound. OpenShell allows or denies the POST."""
    if name not in SOUNDS:
        return {"status": "invalid_sound", "name": name}
    r = httpx.post(f"{SPEAKER}/play", json={"name": name}, timeout=20)
    if r.status_code == 403:
        return {"status": "policy_denied", "sound": name}
    r.raise_for_status()
    return {"status": "played", "name": name}


def move_head(directions: list[str]) -> dict:
    """Send one fixed goto per word. OpenShell allows or denies each POST."""
    for word in directions:
        if word not in POSES:
            return {"status": "invalid_direction", "direction": word}
        pitch, yaw = POSES[word]
        body = {"head_pose": {"x": 0, "y": 0, "z": 0, "roll": 0, "pitch": pitch, "yaw": yaw},
                "duration": 1.0, "interpolation": "minjerk"}
        r = httpx.post(f"{ROBOT}/api/move/goto", json=body, timeout=5)
        if r.status_code == 403:
            try:
                play_sound(DENIED_SOUND)  # audible "no"; itself subject to policy
            except Exception:  # noqa: BLE001 - the denial is the result, not the sound
                pass
            return {"status": "policy_denied", "direction": word, "detail": r.json().get("detail", "")}
        r.raise_for_status()
        move_id = r.json()["uuid"]
        while move_id in httpx.get(f"{ROBOT}/api/move/running", timeout=5).json():
            time.sleep(0.1)
    return {"status": "completed", "directions": directions}


def ask(client: OpenAI, messages: list, **kw):
    """One chat completion; if the free endpoint is full (503), retry on the fallback model."""
    for model in (MODEL, FALLBACK_MODEL):
        try:
            return client.chat.completions.create(model=model, messages=messages, tools=TOOLS, **EXTRA, **kw)
        except Exception as exc:  # noqa: BLE001 - keep the example short
            if "503" not in str(exc) or model == FALLBACK_MODEL:
                raise
            print(f"[busy] {model} is full, trying {FALLBACK_MODEL}", file=sys.stderr)


def main(sentence: str) -> None:
    client = OpenAI(base_url=BASE_URL, api_key=API_KEY)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": sentence}]

    # 4. Ask the model. It answers with text, or with one or more tool calls.
    #    Loop so "look left, then make a sound" can take several calls or rounds.
    msg = ask(client, messages, tool_choice="auto").choices[0].message
    for _round in range(4):
        if not msg.tool_calls:
            break
        messages.append(msg)
        for call in msg.tool_calls:
            args = json.loads(call.function.arguments or "{}")   # e.g. {"directions": ["left"]}
            print(f"[model chose] {call.function.name}({args})")

            # 5. Python runs the tool and tells the model what happened.
            if call.function.name == "play_sound":
                result = play_sound(args.get("name", ""))
            elif call.function.name == "move_head":
                result = move_head(args.get("directions", []))
            else:
                result = {"status": "unknown_tool", "name": call.function.name}
            print(f"[robot said]  {result}")
            messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)})
        msg = ask(client, messages).choices[0].message

    text = (msg.content or "").strip() or "(done)"
    print(f"[reply]       {text}")


if __name__ == "__main__":
    main(" ".join(sys.argv[1:]) or "Reachy, look up.")
