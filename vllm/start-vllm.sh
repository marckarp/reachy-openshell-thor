#!/bin/sh
# Serve Nemotron 3 Nano Omni (NVFP4) locally on the Thor with vLLM.
# OpenAI-compatible API on http://127.0.0.1:8001/v1  (8000 belongs to the robot daemon).
#
# First run downloads ~21 GB of weights into ~/.cache/huggingface and pulls the image.
# Startup after that is ~2-4 minutes (model load + CUDA graph capture).
# Log: vllm/logs/vllm.log    Stop: vllm/stop-vllm.sh
set -e
cd "$(dirname "$0")"
mkdir -p logs "$HOME/.cache/huggingface"

IMAGE="vllm/vllm-openai:v0.31.0"            # arm64 build on CUDA 13.0 = this Thor's driver. Fallback: v0.22.0
MODEL="nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4"
SERVED_NAME="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"   # same id the agent already uses on build.nvidia.com
PORT=8001

if curl -s -m 2 "http://127.0.0.1:$PORT/v1/models" >/dev/null 2>&1; then
  echo "vLLM already serving on :$PORT"; exit 0
fi
docker rm -f reachy-vllm >/dev/null 2>&1 || true

docker run -d --name reachy-vllm \
  --runtime nvidia --ipc host --network host \
  -e NVIDIA_VISIBLE_DEVICES=all -e NVIDIA_DRIVER_CAPABILITIES=compute,utility \
  -v "$HOME/.cache/huggingface:/root/.cache/huggingface" \
  "$IMAGE" \
  "$MODEL" \
  --served-model-name "$SERVED_NAME" \
  --host 127.0.0.1 --port "$PORT" \
  --trust-remote-code \
  --max-model-len 32768 \
  --max-num-seqs 8 \
  --gpu-memory-utilization 0.5 \
  --kv-cache-dtype fp8 \
  --reasoning-parser nemotron_v3 \
  --enable-auto-tool-choice --tool-call-parser qwen3_coder \
  >/dev/null

echo "container reachy-vllm started; following log until the server is ready (Ctrl-C only stops the follow)"
( docker logs -f reachy-vllm >> logs/vllm.log 2>&1 & )
for i in $(seq 1 240); do
  if curl -s -m 2 "http://127.0.0.1:$PORT/v1/models" | grep -q "$SERVED_NAME"; then
    echo "vLLM ready on http://127.0.0.1:$PORT/v1 after ~$((i*5))s"; exit 0
  fi
  if ! docker ps -q -f name=reachy-vllm | grep -q .; then
    echo "container exited; last log lines:" >&2; tail -30 logs/vllm.log >&2; exit 1
  fi
  sleep 5
done
echo "not ready after 20 min; see logs/vllm.log" >&2; exit 1
