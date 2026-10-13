# Reachy Mini + OpenShell on Jetson Thor

Port of the NVIDIA dev note
"Policy-controlling Reachy Mini with OpenShell" to this Thor, with the model on
build.nvidia.com instead of OpenAI Realtime. Status 2026-10-12: phase 1 (text-only
policy demo) works end to end.

## Learnings (what cost time, in order hit)

1. **Reachy Mini Lite needs a host daemon.** The blog assumes a Wireless unit with a Pi. The
   Lite shows up as a USB hub (serial `1a86:55d3`, camera `38fb:1002`, audio `38fb:1001`).
   The Thor runs `reachy-mini-daemon` from the `reachy-mini` PyPI package; it auto-finds the
   serial port by USB IDs.
2. **A stuck Thor USB hub hides the robot.** No kernel plug events at all means the hub, not the
   robot. `robot/usbreset.sh` (toggle `authorized` on bus 1-4) brings it back.
3. **Daemon must bind 0.0.0.0, not 127.0.0.1,** or sandboxes cannot reach it via
   `host.openshell.internal`.
4. **`export` in the prompt does not reach other shells.** Put `NVIDIA_API_KEY` in `~/.bashrc`.
   Non-interactive shells return early from bashrc, so scripts read it with grep + eval.
5. **Group changes need a new login.** `usermod -aG docker` did not reach the running systemd
   user session, so the OpenShell gateway (a user service) could not open the Docker socket.
   Short-term fix was an ACL on `/var/run/docker.sock`.
6. **OpenShell gateway needs schema v2 TOML** (`[openshell] version = 2`) and, on this box, an
   explicit `compute_driver`. Auto-detection gives a confusing "no suitable driver" error.
7. **Jetson kernel has no Landlock** (`CONFIG_SECURITY_LANDLOCK` unset, syscall ENOSYS).
   OpenShell's Docker sandbox exits in under a second with
   `ControlSupervisorStartFailed` and no useful log. Fix: `compute_driver = "vm"` (KVM microVM,
   guest kernel has Landlock). Alternative not taken: rebuild the kernel.
8. **logind rewrites `/dev/kvm` ACLs,** wiping a `setfacl`. A udev rule setting mode 0666 is the
   durable fix; the VM launcher otherwise dies with "cannot open /dev/kvm".
9. **OpenShell matches the real binary, not the symlink.** Policies listing only
   `/opt/venv/bin/python` deny everything because the process is `/usr/local/bin/python3.12`.
   The denial reason is in `openshell logs <sandbox>`, not in the HTTP error.
10. **In VM mode `host.openshell.internal` resolves to a synthetic 198.18.x address** and the
    gateway connects to host loopback, so `allowed_ips` must include `127.0.0.1/32`.
11. **Provider profiles grant the model endpoint.** No policy block for integrate.api.nvidia.com
    is needed; the key is a placeholder inside the sandbox and the proxy swaps it in.
12. **`${VAR}` expansion only happens in the dotenv file,** not in `--env` values. Uploading a
    `.env` with `CHAT_API_KEY=${NVIDIA_API_KEY}` and pointing `REACHY_MINI_DOTENV_PATH` at it
    is how the placeholder key reaches the chat client.
13. **`reachy-mini-backend-check --stage chat` uses fake tools.** It validates the model, not the
    policy. `text_turn.py` builds the real handler so tool calls cross OpenShell.
14. **Nemotron 3 Nano Omni does tool calling and image input** on the chat completions route,
    and the agent correctly reports a `policy_denied` tool result instead of retrying.

15. **Voice on the Thor is blocked by one missing plugin, not by hardware.** The Reachy mic and
    speaker work over plain ALSA (card 2) and the camera opens with OpenCV on /dev/video0. But the
    Reachy daemon's media server needs GStreamer's Rust `webrtcsink`, which Ubuntu 24.04 has no
    package for; Pollen's docs build it from gst-plugins-rs 0.14.5 with cargo-c. Without it the
    SDK falls back to WebRTC and the native app crashes on connect. Removed for now.
16. **OpenShell 0.1.3 prefixes service hostnames with the workspace** (`default--reachy-agent--audio`).
    The native controller was patched (settings.py, `workspace` field) and its tests updated.

## Where to look (reading guide)

Paths are relative to `~/Desktop/mkarp`. `APP` = `agent`.

**The agent (runs inside the sandbox)**
- System prompt: `APP/src/reachy_mini_conversation_app/profiles/_reachy_mini_conversation_app_locked_profile/instructions.txt`
  (13 lines; loaded by `prompts.py::get_session_instructions`).
- Tool schemas the model sees and the word-to-pose table: `APP/src/reachy_mini_conversation_app/rest_tool_transport.py`
  (`_MOVE_HEAD_SPEC`, `_STOP_MOTION_SPEC`, `_DIRECTION_POSES`, `_goto_and_wait`).
- Chat loop (send prompt + tools to the model, run tool calls, feed results back): `APP/src/reachy_mini_conversation_app/chat_completions.py`.
- Backend selection and env/.env handling: `APP/src/reachy_mini_conversation_app/config.py`, `backend_runtime.py`, `local_stt_backend.py`.
- Headless service the native app would talk to (`/health`, `/audio`): `sandbox_audio.py`; its start/stop: `sandbox_control.py`.
- Sandbox image recipe: `APP/Dockerfile.openshell` + `APP/requirements-rest.txt`.

**Where the agent actually lives at runtime**
- OpenShell sandbox `reachy-agent` (a KVM microVM). Inside: code in `/opt/venv`, config `/sandbox/.env`,
  test entry `/sandbox/text_turn.py`, agent log `/sandbox/logs/reachy-agent.log`.
- Host copies of what was uploaded: `agent/sandbox.env`, `agent/text_turn.py`.
- Get a shell: `openshell sandbox exec --name reachy-agent -- sh`.

**OpenShell (control plane and policy)**
- Gateway config: `~/.config/openshell/gateway.toml` (copy in `openshell/gateway.toml`); service `systemctl --user status openshell-gateway`.
- Policies (what the sandbox may call): `openshell/policy-*.yaml`. Switch with `openshell policy set reachy-agent --policy <file> --wait`.
- Provider holding the NVIDIA key: profile `openshell/reachy-nvidia.yaml`; `openshell provider get reachy-nvidia`.
- Allow/deny audit trail: `openshell logs reachy-agent` (look for `NET:OPEN`, `HTTP:POST`, `DENIED`).
- VM driver state and disks: `~/.local/state/openshell/vm-driver/`.

**The robot**
- Daemon (owns USB serial, REST on :8000): `robot/venv/lib/python3.12/site-packages/reachy_mini/daemon/`;
  routes in `daemon/app/routers/move.py`; start with `robot/start-daemon.sh`; log `robot/logs/reachy-daemon.log`.
- REST without a model or OpenShell: `robot/move_demo.py`.

**Host-side helpers**
- `talk.sh` natural-language prompt. `voice-parked/` parked voice work. `apps_venv` symlink lets the daemon see the native app.

## Layout

See `README.md` for the folder map. Short form: `talk.sh` to use it; `robot/` hardware side;
`openshell/` policies, provider, gateway config and `SETUP.md`; `agent/` the one-file agent and its Dockerfile; Speech work was removed on 2026-10-12; lessons 15-16 record what was learned.

## Why it differs from the guide

- Reachy Mini Lite has no onboard computer, so the Thor runs the daemon.
- Jetson kernel has no Landlock, so OpenShell's Docker driver cannot start a sandbox.
  The gateway uses the KVM microVM driver instead. `/dev/kvm` must be 0666 (udev rule);
  logind rewrites ACLs so `setfacl` alone does not persist.
- `/opt/venv/bin/python` is a symlink; policies also list `/usr/local/bin/python3.12`.
- In VM mode `host.openshell.internal` maps to host loopback, so `127.0.0.1/32` is in
  `allowed_ips`.
- Model endpoint comes from the `reachy-nvidia` provider, not a policy block.
  `NVIDIA_API_KEY` lives only in `~/.bashrc`.

## Daily operation

```sh
robot/start-daemon.sh                      # robot
openshell status                       # gateway (systemd --user openshell-gateway)
openshell sandbox get reachy-agent     # expect Phase: Ready
openshell sandbox exec --name reachy-agent --no-tty -- \
  /opt/venv/bin/python /sandbox/text_turn.py "Reachy, look up."
openshell policy set reachy-agent --policy openshell/policy-motion-enabled.yaml --wait
openshell policy set reachy-agent --policy openshell/policy-motion-disabled.yaml --wait
```

## Simple use (no voice)

```sh
./talk.sh                        # interactive prompt, type sentences
./talk.sh "Reachy, look left"    # one-shot
```

## Next

Voice, when wanted: build `webrtcsink` (gst-plugins-rs) so the daemon media server starts, then bring back
the native Reachy app and a speech adapter (hosted Parakeet + Magpie verified working on 2026-10-12).

17. **The upstream app was replaced by one file.** `agent/minimal_agent.py` (prompt, tool enum, pose
    table, model call, goto request) does the same job as the multi-file app for this demo, with a
    15-line Dockerfile. Camera, voice, simulator and Gradio paths were dropped on 2026-10-12.

18. **The speaker worked all along; it was quiet.** The Reachy USB audio card boots at 62% (-23 dB),
    inaudible in a room. `amixer -c Audio sset PCM,0 100%` fixes it; `robot/start-speaker.sh` does this.
    The daemon's `/api/media/play_sound` returns `ok` even when its media layer is down and nothing
    plays, so a trusted ALSA speaker service (`robot/speaker.py`) replaces it.
19. **Local vLLM: turn thinking off for tool choices.** ~4 s with reasoning, ~1 s without, same
    tool call. `minimal_agent.py` passes `enable_thinking: false` when `CHAT_BASE_URL` is local.
