#!/usr/bin/env bash
set -euo pipefail

# Builder-only Atlas launcher for NVIDIA GB10 / DGX Spark class hardware.
# The rest of the Slack platform is intentionally not routed through this
# endpoint; this is a canary inference service for #builder only.

CHECKPOINT="${BUILDER_MODEL_CHECKPOINT:-AEON-7/Qwen3.8-27B-AEON-ULTIMATE-UNCENSORED-BF16}"
IMAGE="${ATLAS_BUILDER_IMAGE:-avarok/atlas-gb10:latest}"
PORT="${ATLAS_BUILDER_PORT:-8888}"
MODEL_NAME="${ATLAS_BUILDER_MODEL_NAME:-aeon-builder}"
HF_CACHE="${ATLAS_HF_CACHE:-${HOME}/.cache/huggingface}"
ATLAS_STATE="${ATLAS_STATE_DIR:-${HOME}/.atlas}"

mkdir -p "${HF_CACHE}" "${ATLAS_STATE}"

exec docker run --rm \
  --name atlas-builder \
  --network host \
  --gpus all \
  --ipc=host \
  -v "${HF_CACHE}:/root/.cache/huggingface" \
  -v "${ATLAS_STATE}:/root/.atlas" \
  "${IMAGE}" \
  serve "${CHECKPOINT}" \
  --kernel-target qwen3.8-27b \
  --model-name "${MODEL_NAME}" \
  --port "${PORT}" \
  --no-auto-swap
