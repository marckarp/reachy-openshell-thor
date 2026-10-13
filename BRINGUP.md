# Bring-up from zero

Run from `~/Desktop/mkarp`. One layer at a time. Each layer has a check; stop and look if
the output differs from what's described.

Assumed already done once with sudo (see `openshell/SETUP.md` steps 1 to 4 if not):
OpenShell package installed, your user in the `docker` and `kvm` groups, the udev rule for
`/dev/kvm`, `NVIDIA_API_KEY` exported in `~/.bashrc`, and the `reachy-agent-minimal:latest`
image built from `agent/Dockerfile`.

For voice (step 6b) also once: `robot/venv/bin/pip install kokoro-onnx soundfile` and the two
Kokoro files in `robot/models/` (`kokoro-v1.0.onnx`, `voices-v1.0.bin`, ~340 MB, from the
`thewh1teagle/kokoro-onnx` GitHub release `model-files-v1.0`).

## 0. Make sure everything is down

```sh
systemctl --user stop openshell-gateway
rm -rf ~/.local/state/openshell ~/.config/openshell/gateways ~/.config/openshell/gateway.toml
pkill -f 'reachy-mini-daemon --fastapi'      # harmless if not running
```

## 1. Robot — hardware only, no AI, no OpenShell

```sh
robot/start-daemon.sh
robot/venv/bin/python robot/move_demo.py
robot/start-speaker.sh
curl -s -X POST 127.0.0.1:8042/play -H 'content-type: application/json' -d '{"name":"wake_up"}'
```

What it does: starts Pollen's daemon from `robot/venv`. It finds the USB serial port, wakes
the robot, runs the 50 Hz motor loop, and serves `http://127.0.0.1:8000`. The demo sends
four plain HTTP goto requests.
Expect: `daemon ready`, the head moves up, left, right, front, then `speaker service ready`
and the wake-up sound from the robot. The speaker script also sets the card volume to 100%.

## 2. Gateway config — pick the microVM driver

```sh
cp openshell/gateway.toml ~/.config/openshell/gateway.toml
openshell-gateway config preflight --path ~/.config/openshell/gateway.toml
```

What it does: this Jetson kernel has no Landlock, so OpenShell's Docker driver cannot start
a sandbox. The config pins the KVM microVM driver instead.
Expect: preflight prints nothing (or only "VM host tool ..." lines). Any `Error:` means stop.

## 3. Gateway — start it and point the CLI at it

```sh
systemctl --user start openshell-gateway
openshell gateway add https://127.0.0.1:17670 --local --name openshell
openshell status
```

What it does: starts the control-plane process (it generates its TLS certificates on first
start, which is why it starts before you register it). `gateway add` tells the `openshell`
command where the gateway is. `status` makes the CLI connect.
Expect: `Status: Connected` and `Authentication: Authenticated (mTLS transport)`.
If `status` says connection refused: `journalctl --user -u openshell-gateway -n 20`.

## 4. Provider — hand the API key to the gateway

```sh
openshell profile import -f openshell/reachy-nvidia.yaml
openshell provider create --name reachy-nvidia --type reachy-nvidia --credential NVIDIA_API_KEY
openshell provider get reachy-nvidia
```

What it does: the profile says "a credential called NVIDIA_API_KEY may be sent as a bearer
token to integrate.api.nvidia.com and nowhere else, by these binaries". `provider create`
with a bare `--credential NVIDIA_API_KEY` reads the value from your shell environment and
sends it to the gateway, which stores it encrypted in `~/.local/state/openshell/gateway/`.
From now on the sandbox only ever sees a placeholder.
Expect: `Created provider reachy-nvidia`; `get` shows `Credential keys: NVIDIA_API_KEY`
and never prints the value.

## 5. Sandbox — a small VM holding the one-file agent

```sh
openshell sandbox create --name reachy-agent --from reachy-agent-minimal:latest \
  --policy openshell/policy-motion-enabled.yaml --provider reachy-nvidia --detach --no-tty
openshell sandbox get reachy-agent
openshell policy get reachy-agent
```

What it does: converts the image into a VM root disk (first time only, ~30 s), boots it,
applies the policy, attaches the provider. The agent file is already inside the image at
`/sandbox/minimal_agent.py`. The VM has no network card: every connection goes through the
gateway's policy check.
Expect: `Phase: Ready`. The policy shows four allowed paths under port 8000 and three under 8042.

## 6. Talk

```sh
./talk.sh "Reachy, look left"
```

What it does: runs `/sandbox/minimal_agent.py` inside the sandbox with your sentence.
The agent sends the prompt + three tools to Nemotron through the gateway, gets back
`perform([{"yaw": 40}])`, clamps and converts it, and POSTs `/api/move/goto` to the daemon
through the policy.
Expect three lines: `[model chose] perform({'steps': '1 steps'})`, `[robot said] ... completed`,
`[reply]`, and the head moves. Then try `./talk.sh "Reachy, do a little dance"`: the dance tune
starts and one `perform` of 8 to 12 steps follows.

## 6b. Speak (needs the local vLLM from step 8)

```sh
./voice.sh --once          # press Enter, say "Reachy, look right"
```

What it does: records 4 s from the robot mic, base64-pipes the WAV into the sandbox, and the
agent sends it to Nemotron 3 Nano Omni as audio. Omni does the listening itself (no whisper),
emits the same `move_head` tool call, and the move crosses the same policy. The host speaks
the `[reply]` line through `POST /say` on the speaker service (Kokoro TTS).
Expect: the three lines from step 6, the head moves, and Reachy answers out loud.
Check the boundary still holds: the sandbox gets 403 on `/say`:
`openshell logs reachy-agent | grep "8042/say"` shows `DENIED`.

Hands-free instead:

```sh
robot/venv/bin/python robot/listen.py
```

Say something unrelated: expect `heard 1.8 s -> asking Reachy` then `(not for me)`. Say
"Reachy, look left": expect the tool call, the move, and the spoken reply. Needs
`robot/models/silero_vad.onnx` (2 MB, from the `snakers4/silero-vad` repo,
`src/silero_vad/data/silero_vad.onnx`).

## 7. Prove the boundary

```sh
openshell policy set reachy-agent --policy openshell/policy-motion-disabled.yaml --wait
./talk.sh "Reachy, look left"
openshell logs reachy-agent | grep -E "ALLOWED|DENIED" | tail -3
openshell policy set reachy-agent --policy openshell/policy-motion-enabled.yaml --wait
```

What it does: hot-swaps to the policy that lacks the `goto` allow rule. Same sentence, same
model, same tool call, but the gateway answers 403 and the robot stays still. The log is the
gateway's own record. The last line restores motion.
Expect: the confused chirp from the speaker, `[robot said] ... policy_denied`, reply says OpenShell
blocked it, log shows `DENIED`. (Sounds and speech are allowed in both policies; the move is not,
so `./talk.sh "Reachy, say hello out loud"` still talks under the locked policy.)

## 8. Optional: run the model on the Thor instead of build.nvidia.com

```sh
vllm/start-vllm.sh                     # first time: pull + 21 GB download, then ~3 min
vllm/test-tools.sh                     # expect a move_head tool call
openshell profile import -f openshell/local-vllm.yaml
openshell provider create --name local-vllm --type local-vllm
openshell sandbox delete reachy-agent
openshell sandbox create --name reachy-agent --from reachy-agent-minimal:latest \
  --policy openshell/policy-motion-enabled.yaml --provider local-vllm \
  --env CHAT_BASE_URL=http://host.openshell.internal:8001/v1 --detach --no-tty
./talk.sh "Reachy, look left"
```

What it does: same agent, same policy, different provider. The `local-vllm` provider has no
credential; it only grants the sandbox permission to reach port 8001 on the host. To go back
to the hosted model, recreate the sandbox with `--provider reachy-nvidia` and no `--env`.

## Day-to-day after this

Only step 1 needs repeating after a reboot (`robot/start-daemon.sh`, `robot/start-speaker.sh`, and `vllm/start-vllm.sh` if you use the local model). The gateway is a
systemd user service and comes back on login; the sandbox persists and restarts with it
(`openshell sandbox start reachy-agent` if it shows Stopped). Then `./talk.sh`.

## Tear down to zero again

```sh
openshell sandbox delete reachy-agent
openshell provider delete reachy-nvidia
openshell profile delete reachy-nvidia
openshell gateway remove openshell
systemctl --user stop openshell-gateway
rm -rf ~/.local/state/openshell ~/.config/openshell/gateways ~/.config/openshell/gateway.toml
pkill -f 'reachy-mini-daemon --fastapi'
```
