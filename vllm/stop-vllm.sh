#!/bin/sh
# Stop the local vLLM server. Weights stay cached in ~/.cache/huggingface.
docker rm -f reachy-vllm >/dev/null 2>&1 && echo "vLLM stopped" || echo "vLLM was not running"
