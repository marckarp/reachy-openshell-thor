# vllm/ — host the model on the Thor

Runs the same model the agent uses today, Nemotron 3 Nano Omni, locally in vLLM instead of
on build.nvidia.com. The agent code does not change; only the address it talks to.

## Why these choices

- **Runtime: vLLM.** NVIDIA's Jetson AI Lab page for this model lists vLLM as the tested path on
  Thor, with the exact reasoning and tool-call parser flags. NIM containers do not run on Thor's
  GPU today. Ollama and llama.cpp run the model but do not document tool calling for it.
- **Image: `vllm/vllm-openai:v0.31.0`.** Pinned, has an ARM64 build, and is built on CUDA 13.0.2,
  which matches this Thor's driver (CUDA 13.0, GPU compute capability 11.0). NVIDIA's own NGC
  vLLM containers are on CUDA 13.3 / 13.4, newer than the driver here. Fallback if 0.31 misbehaves:
  `v0.22.0` (same CUDA, closest to the version the model card asks for, 0.20).
- **Weights: NVFP4, about 21 GB.** The quantization the model card recommends for Thor.
- **Port 8001.** The robot daemon owns 8000.
- **`--served-model-name`** keeps the model id identical to the hosted one, so `agent/minimal_agent.py`
  needs no model-name change.

## Steps

```sh
vllm/start-vllm.sh        # first run: image pull + 21 GB download, then ~3 min to load
vllm/test-tools.sh        # expect tool_calls: [('move_head', '{"directions": ["left"]}')]
```

Watch progress: `tail -f vllm/logs/vllm.log`. GPU use: `nvidia-smi`.
Stop: `vllm/stop-vllm.sh`. Weights stay cached in `~/.cache/huggingface`.

## Flags in start-vllm.sh

| Flag | Why |
|---|---|
| `--runtime nvidia --ipc host --network host` | GPU access, shared memory for the engine, bind directly on the host's loopback |
| `--trust-remote-code` | the model ships custom Python for its hybrid Mamba/Transformer architecture |
| `--max-model-len 32768` | plenty for one sentence plus a tool schema; smaller than the 131k max to save memory |
| `--max-num-seqs 8` | one robot, one user; no need for a large batch |
| `--gpu-memory-utilization 0.5` | Thor has 128 GB unified memory shared with the CPU; take half |
| `--kv-cache-dtype fp8` | model card recommendation for the NVFP4 build |
| `--reasoning-parser nemotron_v3` | splits the model's thinking from its answer |
| `--enable-auto-tool-choice --tool-call-parser qwen3_coder` | lets vLLM turn the model's output into structured `tool_calls` |

## Measured on this Thor (2026-10-12)

| | |
|---|---|
| First start (image pull + 21 GB weights + load) | ~12 min |
| Later starts (load + warmup) | ~5 min |
| Tool call with thinking on | ~4 s, ~130 output tokens |
| Tool call with thinking off | ~1 s, ~26 output tokens |

`minimal_agent.py` turns thinking off automatically when `CHAT_BASE_URL` points at the local
server (`CHAT_THINKING=1` to override). Generation runs at roughly 14 tokens/s here, so short
answers matter more than raw speed.

## Point the agent at it (after test-tools.sh passes)

The sandbox reaches host services as `host.openshell.internal`. Two changes, both small:

1. A provider profile with no credential that allows `host.openshell.internal:8001`
   (`openshell/local-vllm.yaml`), imported and created like the NVIDIA one.
2. Recreate the sandbox with `--provider local-vllm --env CHAT_BASE_URL=http://host.openshell.internal:8001/v1`.

`minimal_agent.py` reads `CHAT_BASE_URL` and falls back to build.nvidia.com when it is unset, so
switching back is recreating the sandbox with the old provider.
