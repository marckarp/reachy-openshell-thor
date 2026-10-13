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

## Setup (first time on a machine)

Done once; after that only the "must be running" table below matters. Every step has detail
and checks in `BRINGUP.md` and `openshell/SETUP.md`.

**Hardware.** Jetson Thor (JetPack 7 / L4T R38, aarch64), Reachy Mini Lite on a **USB-A** port
(the Thor's USB-C is device-mode only), robot on its own power adapter.

1. **Host packages**
   ```sh
   sudo apt install -y python3.12-venv python3-dev alsa-utils sox docker.io
   sudo usermod -aG dialout,docker,kvm $USER        # serial port, images, microVMs; re-login after
   ```
2. **Robot venv** (daemon, speaker service, listener)
   ```sh
   python3 -m venv robot/venv
   robot/venv/bin/pip install reachy-mini fastapi uvicorn httpx kokoro-onnx soundfile onnxruntime
   ```
3. **Voice models** into `robot/models/` (git-ignored, ~340 MB)
   - `kokoro-v1.0.onnx`, `voices-v1.0.bin` from GitHub `thewh1teagle/kokoro-onnx`, release `model-files-v1.0`
   - `silero_vad.onnx` from GitHub `snakers4/silero-vad`, path `src/silero_vad/data/silero_vad.onnx`
4. **OpenShell** (`openshell/SETUP.md` steps 1 to 5): install script, `gateway.toml` with the KVM
   microVM driver, the `/dev/kvm` udev rule, start + register the gateway.
   ```sh
   curl -LsSf https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh | sudo sh
   cp openshell/gateway.toml ~/.config/openshell/gateway.toml
   sudo cp openshell/99-kvm-openshell.rules /etc/udev/rules.d/ && sudo udevadm control --reload-rules
   systemctl --user enable --now openshell-gateway && sudo loginctl enable-linger $USER
   openshell gateway add https://127.0.0.1:17670 --local --name openshell
   ```
5. **Model on the Thor** (`vllm/README.md`): `vllm/start-vllm.sh` pulls the vLLM image and
   ~21 GB of Nemotron 3 Nano Omni weights on first run, then serves on :8001.
   ```sh
   openshell profile import -f openshell/local-vllm.yaml
   openshell provider create --name local-vllm --type local-vllm
   ```
   (Cloud alternative: `reachy-nvidia` provider with `NVIDIA_API_KEY` in `~/.bashrc`, see `BRINGUP.md` step 4.)
6. **Sandbox image and sandbox** (`agent/README.md`)
   ```sh
   cd agent && docker buildx build --load --tag reachy-agent-minimal:latest --file Dockerfile . && cd ..
   openshell sandbox create --name reachy-agent --from reachy-agent-minimal:latest \
     --policy openshell/policy-motion-enabled.yaml --provider local-vllm \
     --env CHAT_BASE_URL=http://host.openshell.internal:8001/v1 --detach --no-tty
   ```

Nothing in this repo holds a credential; the only secret (`NVIDIA_API_KEY`, cloud path only)
lives in `~/.bashrc` and inside the gateway's encrypted store.

## Four things must be running

| What | Start it | Check it |
|---|---|---|
| Robot daemon (owns the USB motors, REST on :8000) | `robot/start-daemon.sh` | `curl -s 127.0.0.1:8000/api/daemon/status` |
| Speaker service (named sounds + `/say` TTS on :8042) | `robot/start-speaker.sh` | `curl -s 127.0.0.1:8042/sounds` |
| vLLM with Nemotron Omni (:8001) | `vllm/start-vllm.sh` (2–4 min to load) | `curl -s 127.0.0.1:8001/v1/models` |
| OpenShell gateway (user service, port 17670) + sandbox `reachy-agent` | gateway starts at login; `openshell sandbox start reachy-agent` if Stopped | `openshell status` · `openshell sandbox get reachy-agent` says `Ready` |

Then `./talk.sh`, or `robot/listen.sh start` to talk out loud. Starting from nothing? Follow `BRINGUP.md`.

## robot/  — the robot alone

- `start-daemon.sh` starts Pollen's Reachy daemon from `robot/venv`. The daemon finds the
  USB serial port by itself, runs the 50 Hz motor loop, and serves `http://127.0.0.1:8000`.
- `move_demo.py` moves the head with plain HTTP calls. No model, no OpenShell. Good for
  checking the hardware: `robot/venv/bin/python robot/move_demo.py`.
- `speaker.py` + `start-speaker.sh`: tiny trusted service that plays one of nine named sounds
  (six built-in Reachy sounds plus synthesized `yes`, `no` and `love` in `sounds/`) through the robot
  speaker over ALSA. The daemon's own audio path does not work on this host.
  The sandbox reaches it as `host.openshell.internal:8042`; it accepts names only, never paths.
  It also has `POST /say {"text": ...}` (Kokoro TTS, weights in `robot/models/`), used by the
  host to speak replies and by the agent's `speak` tool. `/play` returns at once so the robot can
  dance while the tune plays; `/say` blocks until spoken, so `listen.py` knows when to reopen the mic.
- `usbreset.sh` fixes the Thor's internal USB hub when the robot vanishes from `lsusb`. Needs sudo.
- `logs/reachy-daemon.log` daemon log.

## openshell/  — who may call what

- `policy-motion-enabled.yaml` lets the sandbox call `POST /api/move/goto` (robot moves).
- `policy-motion-disabled.yaml` same file without that one rule (robot refuses with a 403).
  Both allow the speaker service (`POST /play` and `POST /say` on 8042), so Reachy can always
  chirp and talk, including to tell you a move was blocked.
  Switch live: `openshell policy set reachy-agent --policy openshell/<file> --wait`
- `reachy-nvidia.yaml` provider profile. The gateway holds your NVIDIA API key and injects it
  only into requests to integrate.api.nvidia.com. The sandbox never sees the real key.
- `gateway.toml` copy of `~/.config/openshell/gateway.toml`. Uses the KVM microVM driver because
  this Jetson kernel lacks Landlock, which the Docker driver needs.
- `99-kvm-openshell.rules` udev rule installed in `/etc/udev/rules.d/` so the gateway can open `/dev/kvm`.
- See decisions as they happen: `openshell logs reachy-agent | grep -E "ALLOWED|DENIED"`.

## agent/  — the brain inside the sandbox

`agent/minimal_agent.py` is the whole agent, numbered 1 to 4 inside: a system prompt that
describes the body and its safe ranges, three tools (`perform(steps)`, `speak(text)`,
`play_sound(name)`), Python's clamp from the model's degrees to the daemon's radians, and the
HTTP requests to the robot and the speaker. "Dance" is the model composing 8 to 12 poses; "sing"
is it writing lines for `speak`. Every number is clamped (pitch ±30°, yaw ±45°, roll ±25°,
z ±20 mm, antennas ±90°, body ±90°, ≤16 steps, ≤30 s) because the daemon itself does not reject
out-of-range poses. When OpenShell denies a move, the agent plays the confused chirp and says so.
Model: `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`. SDK: the plain `openai` client.

Trade-off, stated plainly: the first version let the model say only five words and Python owned
every number. Now the model owns the numbers inside an envelope. OpenShell still decides *whether*
the sandbox may move the robot; Python decides *how far*. See `agent/README.md`.

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

you -> talk.sh / listen.py -> sandbox agent -> OpenShell -> Nemotron Omni on vLLM :8001
    -> tool call perform([{yaw: 40}]) -> Python clamps, converts to radians -> POST /api/move/goto
    -> OpenShell policy allow/deny -> Reachy daemon -> USB serial -> motors
    -> speak("...") -> OpenShell -> /say on the host -> Kokoro -> robot speaker

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
