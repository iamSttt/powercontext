#!/usr/bin/env bash
# Download bge-small-zh-v1.5 ONNX model files (fp32 + INT8/q8) into backend/public/models.
# Primary source: Hugging Face; CN networks may use hf-mirror.com (uncomment the second block).
set -euo pipefail

MODEL_DIR="$(cd "$(dirname "$0")/.." && pwd)/backend/public/models/Xenova/bge-small-zh-v1.5"
mkdir -p "$MODEL_DIR/onnx"

BASE="${HF_ENDPOINT:-https://huggingface.co}/Xenova/bge-small-zh-v1.5/resolve/main"

for f in config.json tokenizer.json tokenizer_config.json special_tokens_map.json; do
  echo "fetching $f ..."
  curl -sL --retry 3 "$BASE/$f" -o "$MODEL_DIR/$f"
done

echo "fetching onnx/model_quantized.onnx (q8, ~24MB) ..."
curl -sL --retry 3 "$BASE/onnx/model_quantized.onnx" -o "$MODEL_DIR/onnx/model_quantized.onnx"

echo "fetching onnx/model.onnx (fp32, ~95MB) ..."
curl -sL --retry 3 "$BASE/onnx/model.onnx" -o "$MODEL_DIR/onnx/model.onnx"

echo "done. model files:"
ls -la "$MODEL_DIR" "$MODEL_DIR/onnx"

# CN mirror alternative:
# HF_ENDPOINT=https://hf-mirror.com bash scripts/download-models.sh
