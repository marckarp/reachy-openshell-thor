# Reachy Mini + OpenShell on the Jetson Thor

Talk to the robot in plain English. A model picks a head move, OpenShell decides
whether it's allowed, the robot daemon moves the motors. This folder is split by
role so each part can be understood on its own.

```
mkarp/
  talk.sh          <- run this. Type "look left" and Reachy does it.
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
- `speaker.py` + `start-speaker.sh`: tiny trusted service that plays one of eight named sounds
  (six built-in Reachy sounds plus synthesized `yes` and `no` in `sounds/`) through the robot
  speaker over ALSA. The daemon's own audio path does not work on this host.
  The sandbox reaches it as `host.openshell.internal:8042`; it accepts names only, never paths.
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

## Model: hosted or local

By default the agent calls Nemotron 3 Nano Omni on build.nvidia.com through the `reachy-nvidia`
provider. `vllm/start-vllm.sh` serves the same model locally on port 8001; `vllm/README.md` has the
steps to point the sandbox at it (provider `local-vllm`, env `CHAT_BASE_URL`).

## How one request flows

you -> talk.sh -> sandbox agent -> (OpenShell proxy adds key) -> Nemotron on build.nvidia.com
    -> tool call move_head(["left"]) -> Python maps to a fixed pose -> POST /api/move/goto
    -> OpenShell policy allow/deny -> Reachy daemon -> USB serial -> motors

Open `openshell-walkthrough.html` for the picture.
