# Reachy Mini + OpenShell on the Jetson Thor

Talk to the robot in plain English. A model picks a head move, OpenShell decides
whether it's allowed, the robot daemon moves the motors. This folder is split by
role so each part can be understood on its own.

```
mkarp/
  talk.sh          <- run this. Type "look left" and Reachy does it.
  voice.sh         <- or this. Press Enter, say "look left"; Reachy does it and answers out loud.
  robot/listen.py  <- or this. Always listening; acts only when you address "Reachy".
  robot/           the robot by itself (no AI, no OpenShell)
  openshell/       the gatekeeper: policies, provider, gateway config. SETUP.md = how it was configured
  agent/           ONE file, minimal_agent.py: prompt, tool, word-to-pose table, model call. Dockerfile builds the sandbox image
  vllm/            host the same model on the Thor with vLLM instead of build.nvidia.com. README.md explains the choices
  BRINGUP.md       step-by-step: bring everything up from zero, with checks
  LEARNINGS.md     every gotcha hit on this machine, in order
  openshell-walkthrough.html   diagram of one request end to end
```

## Three things must be running

| What | Start it | Check it |
|---|---|---|
| Robot daemon (owns the USB motors, REST on :8000) | `robot/start-daemon.sh` | `curl -s 127.0.0.1:8000/api/daemon/status` |
| Speaker service (plays 6 named sounds on :8042) | `robot/start-speaker.sh` | `curl -s 127.0.0.1:8042/sounds` |
| OpenShell gateway (user service, port 17670) | starts at login; `systemctl --user restart openshell-gateway` | `openshell status` |
| Sandbox `reachy-agent` (a small VM holding the agent) | already created; `openshell sandbox get reachy-agent` should say `Ready` | same |

Then `./talk.sh`. Starting from nothing? Follow `BRINGUP.md`.

## robot/  — the robot alone

- `start-daemon.sh` starts Pollen's Reachy daemon from `robot/venv`. The daemon finds the
  USB serial port by itself, runs the 50 Hz motor loop, and serves `http://127.0.0.1:8000`.
- `move_demo.py` moves the head with plain HTTP calls. No model, no OpenShell. Good for
  checking the hardware: `robot/venv/bin/python robot/move_demo.py`.
- `speaker.py` + `start-speaker.sh`: tiny trusted service that plays one of nine named sounds
  (six built-in Reachy sounds plus synthesized `yes`, `no` and `love` in `sounds/`) through the robot
  speaker over ALSA. The daemon's own audio path does not work on this host.
  The sandbox reaches it as `host.openshell.internal:8042`; it accepts names only, never paths.
  It also has `POST /say {"text": ...}` (Kokoro TTS, weights in `robot/models/`) which only the
  host may call: the policy allows the sandbox `POST /play` and nothing else on 8042.
- `usbreset.sh` fixes the Thor's internal USB hub when the robot vanishes from `lsusb`. Needs sudo.
- `logs/reachy-daemon.log` daemon log.

## openshell/  — who may call what

- `policy-motion-enabled.yaml` lets the sandbox call `POST /api/move/goto` (robot moves).
- `policy-motion-disabled.yaml` same file without that one rule (robot refuses with a 403).
  Both allow the speaker service (`POST /play` on 8042), so Reachy can always chirp.
  Switch live: `openshell policy set reachy-agent --policy openshell/<file> --wait`
- `reachy-nvidia.yaml` provider profile. The gateway holds your NVIDIA API key and injects it
  only into requests to integrate.api.nvidia.com. The sandbox never sees the real key.
- `gateway.toml` copy of `~/.config/openshell/gateway.toml`. Uses the KVM microVM driver because
  this Jetson kernel lacks Landlock, which the Docker driver needs.
- `99-kvm-openshell.rules` udev rule installed in `/etc/udev/rules.d/` so the gateway can open `/dev/kvm`.
- See decisions as they happen: `openshell logs reachy-agent | grep -E "ALLOWED|DENIED"`.

## agent/  — the brain inside the sandbox

`agent/minimal_agent.py` is the whole agent, about 130 lines, numbered 1 to 5 inside:
system prompt, two tools (`move_head`, `play_sound`), the word-to-angle table, the call to
Nemotron, and the HTTP requests to the robot and the speaker. When OpenShell denies a move,
the agent plays the confused chirp so the refusal is audible. Model: `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`
(fallback `nemotron-3-super-120b-a12b`). SDK: the plain `openai` client.

`agent/Dockerfile` builds the sandbox image (Python + openai + httpx + that file).
After editing the agent, push it into the running sandbox without a rebuild:
```sh
openshell sandbox exec --name reachy-agent --no-tty -- sh -c 'cat > /sandbox/minimal_agent.py' < agent/minimal_agent.py
```

## Talk out loud: voice.sh

```sh
./voice.sh            # press Enter, speak for 4 s, Reachy acts and answers
./voice.sh --once
```

No separate speech-to-text model. The mic WAV (ALSA card `Audio`, 16 kHz mono) is base64-piped
into the sandbox, and `minimal_agent.py --audio` sends it to Nemotron 3 Nano Omni as an
`input_audio` part. Omni has a Parakeet audio encoder built in, so it listens, reasons, and
picks `move_head` in one call. The robot request then takes the exact same policy path as
`talk.sh`. The `[reply]` line is spoken by the host via `POST /say` on the speaker service.

```
mic -> arecord -> base64 -> sandbox agent -> Omni (vLLM :8001, listens) -> tool call
    -> POST /api/move/goto -> OpenShell policy -> daemon -> motors
    -> [reply] -> host: /say (Kokoro) -> robot speaker
```

Omni audio-in needs the local vLLM (`CHAT_BASE_URL` set). Requires `kokoro-onnx` in `robot/venv`
and the two files in `robot/models/` (see `BRINGUP.md`).

### How audio goes in and out

Audio is never streamed to the model. It moves as one clip per sentence:

```
IN   robot mic (USB, ALSA card "Audio", 16 kHz mono)
     -> host: arecord keeps the mic open; Silero VAD scores each 32 ms slice "speech / not speech"
     -> speech starts: host buffers (plus 0.3 s pre-roll); 0.7 s of silence: the sentence is cut
     -> one WAV (~1-6 s) -> base64 text -> stdin of `openshell sandbox exec` -> agent --audio
     -> agent puts the WAV in a single chat request as {"type":"input_audio"} -> vLLM :8001
     -> Omni's built-in audio encoder turns it into tokens; the LLM reasons, picks a tool, replies
OUT  reply text -> back out of the sandbox on stdout as "[reply] ..."
     -> host: POST /say to robot/speaker.py -> Kokoro (CPU, ~0.5 s per sentence) -> WAV
     -> aplay on the same USB card -> robot speaker
     -> mic was closed while speaking; reopens, back to VAD
```

Consequences: ~1-2 s between the end of your sentence and the answer; no barge-in or
mid-sentence reaction; a 4 s clip is ~128 KB of base64 through the exec channel; while the room
is quiet nothing crosses the sandbox boundary at all. The sandbox never touches the mic or the
speaker device; it only ever sees a WAV on stdin and prints text.

### Always on: robot/listen.py

```sh
robot/venv/bin/python robot/listen.py
```

Same turn as `voice.sh`, but hands-free. Silero VAD (2 MB ONNX, CPU, `robot/models/silero_vad.onnx`)
watches the mic 32 ms at a time; when speech starts it buffers, when 0.7 s of silence follows it
sends that one utterance. There is no separate wake-word model: the agent's `AUDIO_PROMPT` tells
Omni to reply exactly `IGNORE` unless the speaker addressed Reachy, and the host drops those
replies silently. The mic is closed while Reachy speaks so it does not hear itself.

Trade-off: every utterance in the room reaches the GPU (~1 s) and the sandbox. For a demo that is
fine; a shipped product would put a wake-word model (openWakeWord, Porcupine) in front of the VAD.

Everything above is on-device: Silero VAD and Kokoro TTS on the host CPU, Nemotron Omni on the
Thor GPU via vLLM. Nothing leaves the machine.

## Models in use (all on the Thor)

| Job | Model | Runs where | Called by |
|---|---|---|---|
| Understand text or speech, pick the tool, write the reply | Nemotron 3 Nano Omni 30B-A3B (NVFP4), audio encoder built in | GPU, vLLM container `reachy-vllm`, :8001 | sandbox agent, through the OpenShell `local-vllm` provider |
| Hear when speech starts and stops | Silero VAD v5 (2 MB ONNX) | host CPU, `robot/listen.py` | host only |
| Speak the reply | Kokoro-82M (ONNX) | host CPU, `robot/speaker.py` `/say` on :8042 | host only; sandbox gets 403 |

Weights: Omni in `~/.cache/huggingface`, Silero and Kokoro in `robot/models/`. No cloud calls;
`build.nvidia.com` is only used if the sandbox is recreated with `--provider reachy-nvidia`.

## Model: hosted or local

By default the agent calls Nemotron 3 Nano Omni on build.nvidia.com through the `reachy-nvidia`
provider. `vllm/start-vllm.sh` serves the same model locally on port 8001; `vllm/README.md` has the
steps to point the sandbox at it (provider `local-vllm`, env `CHAT_BASE_URL`).

## How one request flows

you -> talk.sh -> sandbox agent -> (OpenShell proxy adds key) -> Nemotron on build.nvidia.com
    -> tool call move_head(["left"]) -> Python maps to a fixed pose -> POST /api/move/goto
    -> OpenShell policy allow/deny -> Reachy daemon -> USB serial -> motors

Open `openshell-walkthrough.html` for the picture.

## Testing the policy boundary

Hot-swap to the deny policy and send the same sentence:

```sh
openshell policy set reachy-agent --policy openshell/policy-motion-disabled.yaml --wait
./talk.sh "Reachy, look left"
```

Expected output — model still calls the tool, gateway blocks it, agent plays the confused chirp:
```
[model chose] move_head({'directions': ['left']})
[robot said]  {'status': 'policy_denied', 'direction': 'left', 'detail': 'POST /api/move/goto not permitted by policy'}
[reply]       OpenShell policy blocked the action.
```

Confirm in the gateway log (the speaker POST is ALLOWED even under the deny policy):
```sh
openshell logs reachy-agent | grep -E "ALLOWED|DENIED" | tail -5
```

Restore motion and verify the head moves:
```sh
openshell policy set reachy-agent --policy openshell/policy-motion-enabled.yaml --wait
./talk.sh "Reachy, look left"
# [robot said]  {'status': 'completed', 'directions': ['left']}
```

The policy swap is instant and live — no sandbox restart needed.
