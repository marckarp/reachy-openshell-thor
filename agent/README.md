# agent/ — one file: sentence in, tool call out, robot moves

`minimal_agent.py` is the entire agent. Read it top to bottom, it is numbered 1 to 5:

1. `SYSTEM_PROMPT` what the model is told.
2. `TOOLS` the two functions it may call: `move_head(directions)` with an enum of five words,
   and `play_sound(name)` with an enum of six built-in sounds.
3. `POSES` Python's table from word to head angles. The model never sees numbers.
4. `ask()` one chat-completions call to Nemotron on build.nvidia.com with the prompt, your
   sentence, and the tool. The reply is either text or a tool call like `{"directions": ["left"]}`.
5. `move_head()` sends one `POST /api/move/goto` per word to the Reachy daemon; `play_sound()`
   posts a name to the speaker service on 8042. OpenShell sits in both paths and answers 403 when
   the policy forbids it. A denied move also triggers the confused chirp. Results go back to the
   model, which writes the final sentence. Several tool calls per sentence are handled in a loop.

Model: `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`, with `nvidia/nemotron-3-super-120b-a12b`
as fallback when the free endpoint is busy. SDK: the plain `openai` Python client, no agent framework.

`Dockerfile` builds the sandbox image: Python, `openai`, `httpx`, and the agent file. Nothing else.

## Run it

```sh
../talk.sh "Reachy, look left"
```

## After editing minimal_agent.py

Fast path, no rebuild:

```sh
openshell sandbox exec --name reachy-agent --no-tty -- sh -c 'cat > /sandbox/minimal_agent.py' < agent/minimal_agent.py
```

Full path, so the image matches the file:

```sh
cd agent && docker buildx build --load --tag reachy-agent-minimal:latest --file Dockerfile .
openshell sandbox delete reachy-agent
openshell sandbox create --name reachy-agent --from reachy-agent-minimal:latest \
  --policy ../openshell/policy-motion-enabled.yaml --provider reachy-nvidia --detach --no-tty
```

`docs/` holds the NVIDIA blog post and setup guide this was built from. The full upstream app
(voice, camera, simulator) is in the OpenShell-Research repo; this folder keeps only the core idea.
