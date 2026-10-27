#!/usr/bin/env bash
# Download bge-small-zh-v1.5 ONNX model files (fp32 + INT8/q8) into the backend's static dir.
# Usage:
#   bash scripts/download-models.sh                 # official HF
#   HF_ENDPOINT=https://hf-mirror.com bash scripts/download-models.sh   # CN mirror
#   MODEL_DIR=/app/models bash scripts/download-models.sh                # custom target
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODEL_DIR="${MODEL_DIR:-$SCRIPT_DIR/../backend/public/models/Xenova/bge-small-zh-v1.5}"
mkdir -p "$MODEL_DIR/onnx"

BASE="${HF_ENDPOINT:-https://huggingface.co}/Xenova/bge-small-zh-v1.5/resolve/main"

for f in config.json tokenizer.json tokenizer_config.json special_tokens_map.json; do
  echo "fetching $f ..."
  curl -fsSL --retry 3 "$BASE/$f" -o "$MODEL_DIR/$f"
done

echo "fetching onnx/model_quantized.onnx (q8, ~24MB) ..."
curl -fsSL --retry 3 "$BASE/onnx/model_quantized.onnx" -o "$MODEL_DIR/onnx/model_quantized.onnx"

echo "fetching onnx/model.onnx (fp32, ~95MB) ..."
curl -fsSL --retry 3 "$BASE/onnx/model.onnx" -o "$MODEL_DIR/onnx/model.onnx"

echo "done. model files:"
ls -la "$MODEL_DIR" "$MODEL_DIR/onnx"
