# agent/ — one file: words in, a performance out, robot moves

`minimal_agent.py` is the entire agent. Read it top to bottom, it is numbered 1 to 4:

1. `SYSTEM_PROMPT` tells the model what body it has and the safe ranges for each joint, in
   degrees and millimetres: pitch ±30, yaw ±45, roll ±25, z ±20 mm, antennas ±90, body_yaw ±90,
   0.2 to 3 s per step, at most 16 steps. It also gives patterns (nod, shake, dance, sing).
   `AUDIO_PROMPT` is appended in `--audio` mode: reply `IGNORE` unless addressed as Reachy.
2. `TOOLS` the three functions it may call:
   - `perform(steps)` a list of poses, each `{pitch, yaw, roll, z, antennas, body_yaw, duration}`.
     The model composes the motion; this is how "dance" becomes 8 to 12 steps it invented.
   - `speak(text)` text to speech on the host (Kokoro), for talking or singing.
   - `play_sound(name)` one of nine built-in sounds (`dance` is the tune it uses to dance to).
3. `to_goto()` + `clamp()` Python's envelope. Every number is clamped to the ranges above and
   converted to the daemon's radians and metres. **The daemon does not reject out-of-range poses**
   (verified: it accepted pitch 1.5 rad), so this clamp is the only thing between the model and the
   motor limits. Total time is scaled to 30 s max.
4. `perform()` sends one `POST /api/move/goto` per step and waits for each; `speak()` posts to
   `/say`, `play_sound()` to `/play`, both on the speaker service at 8042. OpenShell sits in every
   path and answers 403 when the policy forbids it. A denied move also triggers the confused chirp.
   Results go back to the model, which writes the final spoken sentence. Up to 6 tool rounds.

Model: `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` on the Thor's vLLM (`CHAT_BASE_URL`), with
`nvidia/nemotron-3-super-120b-a12b` as fallback when a cloud endpoint is busy. In `--audio` mode
the user turn is an `input_audio` part; Omni's own audio encoder does the listening.
SDK: the plain `openai` Python client, no agent framework.

`Dockerfile` builds the sandbox image: Python, `openai`, `httpx`, and the agent file. Nothing else.

## What changed from the five-word version

The first version had `move_head(directions)` with an enum of five words and a fixed pose table;
the model never saw a number. Now it sees numbers inside a bounded envelope. Safety moved from
"the table has five entries" to "every value is clamped and the sequence is capped". OpenShell's
job is unchanged: it decides whether the sandbox may call `/api/move/goto` at all, not how far.
The trade: expressiveness (dance, sing, improvise) for a vocabulary that is no longer five
auditable words.

## Run it

```sh
../talk.sh "Reachy, do a little dance"
../talk.sh "Reachy, sing me a short song about robots"
```

## After editing minimal_agent.py

Fast path, no rebuild:

```sh
openshell sandbox exec --name reachy-agent --no-tty -- sh -c 'cat > /sandbox/minimal_agent.py' < agent/minimal_agent.py
```

Full path, so the image matches the file:

```sh
cd agent && docker buildx build --load --tag reachy-agent-minimal:latest --file Dockerfile . && cd ..
openshell sandbox delete reachy-agent
openshell sandbox create --name reachy-agent --from reachy-agent-minimal:latest \
  --policy openshell/policy-motion-enabled.yaml --provider local-vllm \
  --env CHAT_BASE_URL=http://host.openshell.internal:8001/v1 --detach --no-tty
```

The full upstream app (native controller, OpenAI Realtime, camera) is in the OpenShell-Research
repo; this folder keeps only the core idea.
