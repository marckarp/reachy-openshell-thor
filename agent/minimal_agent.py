"""The whole idea in one file: words in, model composes a performance, Python clamps and moves the robot.

Runs INSIDE the OpenShell sandbox (so the model key is a placeholder and every
robot or speaker request crosses the policy).

  openshell sandbox exec --name reachy-agent --no-tty -- \
      /opt/venv/bin/python /sandbox/minimal_agent.py "Reachy, look left"

Or speak instead of type: base64 WAV on stdin, Nemotron Omni does the listening.

  base64 -w0 voice.wav | openshell sandbox exec --name reachy-agent --no-tty -- \
      /opt/venv/bin/python /sandbox/minimal_agent.py --audio
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
SOUNDS = ["yes", "no", "love", "confused", "impatient", "wake_up", "sleep", "dance", "count"]
DENIED_SOUND = "confused"  # audible "no" when OpenShell blocks a move

# 1. The body the model is given. Degrees and millimetres in the schema (model-friendly);
#    Python converts to the daemon's radians and metres. The daemon does NOT reject
#    out-of-range poses (verified), so these clamps are the only guard.
LIMITS_DEG = {"pitch": 30, "yaw": 45, "roll": 25, "antenna": 90, "body_yaw": 90}
Z_MM, DUR = 20, (0.2, 3.0)
MAX_STEPS, MAX_TOTAL_S = 16, 30.0

SYSTEM_PROMPT = (
    "You are Reachy, a small desk robot: a head on a 6-motor platform, two antennas, a body that "
    "rotates, and a speaker. Tools: perform(steps) moves the body through a sequence of poses; "
    "speak(text) says words aloud in your own voice; play_sound(name) plays a built-in sound. "
    f"Each step holds a pose: pitch -{LIMITS_DEG['pitch']}..{LIMITS_DEG['pitch']} deg (negative looks up), "
    f"yaw -{LIMITS_DEG['yaw']}..{LIMITS_DEG['yaw']} (positive looks left), roll -{LIMITS_DEG['roll']}..{LIMITS_DEG['roll']} "
    f"(head tilt), z -{Z_MM}..{Z_MM} mm (duck or stretch), antennas [left, right] -{LIMITS_DEG['antenna']}..{LIMITS_DEG['antenna']} deg, "
    f"body_yaw -{LIMITS_DEG['body_yaw']}..{LIMITS_DEG['body_yaw']} deg, duration {DUR[0]}..{DUR[1]} s. Unset fields are 0. "
    f"At most {MAX_STEPS} steps; out-of-range values are clamped. "
    "Always put a whole motion in ONE perform call with all its steps in order; never one call per step. "
    "Patterns: look left is one step yaw 40. Nod is pitch 20 then 0. Shake no is yaw 30, -30, 30, 0. "
    "A dance is one perform call of 8 to 12 quick steps (0.3 to 0.6 s) alternating yaw and roll with "
    "antennas and body_yaw moving too, ending at all zeros; call play_sound 'dance' right before it so "
    "there is music (it starts playing and returns at once). To sing, write 2 to 4 short lines and pass "
    "them to speak, then perform a gentle sway. End every sequence at the neutral pose unless asked to "
    "hold. If a tool result says policy_denied, say that OpenShell policy blocked the action and do not "
    "retry. You may also answer questions and chat. Replies are spoken aloud: one or two short plain "
    "sentences, no emoji, no markdown, no stage directions in parentheses. After tools, finish with a "
    "short spoken sentence that says what you just did, in your own words."
)

# Always-on listening (robot/listen.py) sends every utterance in the room. The model is the
# wake word: it only acts when addressed, and the host drops IGNORE replies silently.
AUDIO_PROMPT = (
    " You are hearing a live microphone. First decide: did the speaker address you by name "
    "(Reachy, hey Reachy, Richie, Reach)? If yes, respond: move if asked to move, otherwise answer "
    "the question or remark like a friendly assistant, in one or two short spoken sentences. "
    "If the speaker did not address you by name, or the audio is silence, noise, or unclear, "
    "call no tools and reply with exactly: IGNORE"
)

# 2. What the model may call. Numbers in a bounded envelope instead of five fixed words.
STEP_SCHEMA = {
    "type": "object",
    "properties": {
        "pitch": {"type": "number", "description": "deg, negative looks up"},
        "yaw": {"type": "number", "description": "deg, positive looks left"},
        "roll": {"type": "number", "description": "deg, head tilt"},
        "z": {"type": "number", "description": "mm, negative ducks"},
        "antennas": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2,
                     "description": "deg [left, right]"},
        "body_yaw": {"type": "number", "description": "deg, body rotation"},
        "duration": {"type": "number", "description": "seconds for this step"},
    },
}
TOOLS = [{
    "type": "function",
    "function": {
        "name": "perform",
        "description": "Move the body through a sequence of poses, one after another. Each step is an "
                       "absolute pose held at the end of its duration. Unset fields are 0 (neutral).",
        "parameters": {"type": "object",
                       "properties": {"steps": {"type": "array", "minItems": 1, "maxItems": MAX_STEPS, "items": STEP_SCHEMA}},
                       "required": ["steps"]},
    },
}, {
    "type": "function",
    "function": {
        "name": "speak",
        "description": "Say this text aloud through the robot speaker (text to speech). Use for talking or singing.",
        "parameters": {"type": "object", "properties": {"text": {"type": "string", "maxLength": 300}}, "required": ["text"]},
    },
}, {
    "type": "function",
    "function": {
        "name": "play_sound",
        "description": "Play one of Reachy's built-in sounds through its speaker.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string", "enum": SOUNDS}}, "required": ["name"]},
    },
}]


def clamp(v, lo, hi) -> float:
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return 0.0


def to_goto(step: dict) -> dict:
    """One model step (deg, mm) -> one daemon goto body (rad, m), clamped to the safe envelope."""
    deg = lambda k: math.radians(clamp(step.get(k, 0), -LIMITS_DEG[k], LIMITS_DEG[k]))  # noqa: E731
    ants = step.get("antennas") or [0, 0]
    ants = [math.radians(clamp(a, -LIMITS_DEG["antenna"], LIMITS_DEG["antenna"])) for a in list(ants)[:2]] + [0.0, 0.0]
    return {
        "head_pose": {"x": 0, "y": 0, "z": clamp(step.get("z", 0), -Z_MM, Z_MM) / 1000.0,
                      "roll": deg("roll"), "pitch": deg("pitch"), "yaw": deg("yaw")},
        "antennas": ants[:2],
        "body_yaw": deg("body_yaw"),
        "duration": clamp(step.get("duration", 0.8), *DUR),
        "interpolation": "minjerk",
    }


def play_sound(name: str) -> dict:
    """Ask the trusted speaker service for one named sound. OpenShell allows or denies the POST."""
    if name not in SOUNDS:
        return {"status": "invalid_sound", "name": name}
    try:
        r = httpx.post(f"{SPEAKER}/play", json={"name": name}, timeout=20)
    except httpx.HTTPError as exc:
        return {"status": "speaker_unavailable", "name": name, "error": type(exc).__name__}
    if r.status_code == 403:
        return {"status": "policy_denied", "sound": name}
    if r.status_code != 200:
        return {"status": "speaker_error", "name": name, "http": r.status_code}
    return {"status": "played", "name": name}


def speak(text: str) -> dict:
    """Text to speech on the host (Kokoro). OpenShell allows or denies the POST."""
    text = (text or "").strip()[:300]
    if not text:
        return {"status": "empty"}
    try:
        r = httpx.post(f"{SPEAKER}/say", json={"text": text}, timeout=90)
    except httpx.HTTPError as exc:
        return {"status": "speaker_unavailable", "error": type(exc).__name__}
    if r.status_code == 403:
        return {"status": "policy_denied", "detail": "POST /say not permitted by policy"}
    if r.status_code != 200:
        return {"status": "speaker_error", "http": r.status_code}
    return {"status": "said", "chars": len(text)}


def wait_for(move_id: str) -> None:
    """The daemon reports running moves as a list of {"uuid": ...}; poll until ours is gone."""
    deadline = time.monotonic() + DUR[1] + 5
    while time.monotonic() < deadline:
        running = httpx.get(f"{ROBOT}/api/move/running", timeout=5).json()
        if not any(m.get("uuid") == move_id for m in running):
            return
        time.sleep(0.05)


def perform(steps: list) -> dict:
    """Send one clamped goto per step and wait for each. OpenShell allows or denies each POST."""
    steps = [s for s in (steps or []) if isinstance(s, dict)][:MAX_STEPS]
    if not steps:
        return {"status": "no_steps"}
    bodies = [to_goto(s) for s in steps]
    total = sum(b["duration"] for b in bodies)
    if total > MAX_TOTAL_S:                      # scale durations so the whole thing fits
        for b in bodies:
            b["duration"] = max(DUR[0], b["duration"] * MAX_TOTAL_S / total)
    done = 0
    for body in bodies:
        r = httpx.post(f"{ROBOT}/api/move/goto", json=body, timeout=5)
        if r.status_code == 403:
            try:
                play_sound(DENIED_SOUND)  # audible "no"; itself subject to policy
            except Exception:  # noqa: BLE001 - the denial is the result, not the sound
                pass
            return {"status": "policy_denied", "steps_done": done, "detail": r.json().get("detail", "")}
        r.raise_for_status()
        wait_for(r.json()["uuid"])
        done += 1
    return {"status": "completed", "steps": done, "seconds": round(sum(b["duration"] for b in bodies), 1)}


def ask(client: OpenAI, messages: list, **kw):
    """One chat completion; if the free endpoint is full (503), retry on the fallback model."""
    for model in (MODEL, FALLBACK_MODEL):
        try:
            return client.chat.completions.create(model=model, messages=messages, tools=TOOLS, **EXTRA, **kw)
        except Exception as exc:  # noqa: BLE001 - keep the example short
            if "503" not in str(exc) or model == FALLBACK_MODEL:
                raise
            print(f"[busy] {model} is full, trying {FALLBACK_MODEL}", file=sys.stderr)


def user_message(sentence: str, wav_b64: str | None) -> dict:
    """Text from argv, or a base64 WAV from stdin that the model listens to directly (Omni does the ASR)."""
    if wav_b64:
        return {"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": wav_b64, "format": "wav"}}]}
    return {"role": "user", "content": sentence}


def main(sentence: str, wav_b64: str | None = None) -> None:
    client = OpenAI(base_url=BASE_URL, api_key=API_KEY)
    system = SYSTEM_PROMPT + (AUDIO_PROMPT if wav_b64 else "")
    messages = [{"role": "system", "content": system}, user_message(sentence, wav_b64)]

    # 3. Ask the model. It answers with text, or with one or more tool calls.
    #    Loop so "play the dance tune, then dance" can take several calls or rounds.
    msg = ask(client, messages, tool_choice="auto").choices[0].message
    for _round in range(6):
        if not msg.tool_calls:
            break
        messages.append(msg)
        for call in msg.tool_calls:
            args = json.loads(call.function.arguments or "{}")
            shown = {k: (f"{len(v)} steps" if k == "steps" else v) for k, v in args.items()}
            print(f"[model chose] {call.function.name}({shown})")

            # 4. Python runs the tool inside the safe envelope and tells the model what happened.
            if call.function.name == "perform":
                result = perform(args.get("steps", []))
            elif call.function.name == "speak":
                result = speak(args.get("text", ""))
            elif call.function.name == "play_sound":
                result = play_sound(args.get("name", ""))
            else:
                result = {"status": "unknown_tool", "name": call.function.name}
            print(f"[robot said]  {result}")
            messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)})
        msg = ask(client, messages).choices[0].message

    text = (msg.content or "").strip() or "(done)"
    print(f"[reply]       {text}")


if __name__ == "__main__":
    if sys.argv[1:2] == ["--audio"]:
        main("", wav_b64=sys.stdin.read().strip())   # base64 WAV piped in by voice.sh / listen.py
    else:
        main(" ".join(sys.argv[1:]) or "Reachy, look up.")
