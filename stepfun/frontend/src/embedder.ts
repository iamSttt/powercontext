// Local (in-browser) embedding with Transformers.js.
// Model: bge-small-zh-v1.5 (384-d, Chinese-friendly), INT8 (q8) quantized ONNX.
// Backend preference: WebGPU -> WASM (automatic fallback), shown in the UI.
import { pipeline, env } from '@huggingface/transformers';
import type { FeatureExtractionPipeline } from '@huggingface/transformers';

const MODEL_ID = 'Xenova/bge-small-zh-v1.5';
const BATCH_SIZE = 16;

// Serve the ONNX model files from our local backend (no remote inference).
env.allowLocalModels = true;
env.localModelPath = '/models/';

export type BackendName = 'webgpu' | 'wasm';
export interface EmbedderState {
  backend: BackendName | null;
  dtype: 'q8' | 'fp32';
  ready: boolean;
  modelLoadMs: number | null;
}

export const state: EmbedderState = { backend: null, dtype: 'q8', ready: false, modelLoadMs: null };

let pipe: FeatureExtractionPipeline | null = null;

async function tryLoad(
  device: 'webgpu' | 'wasm',
  dtype: 'q8' | 'fp32',
): Promise<{ pipe: FeatureExtractionPipeline; ms: number } | null> {
  const t0 = performance.now();
  try {
    const p = await pipeline('feature-extraction', MODEL_ID, { device, dtype });
    return { pipe: p, ms: performance.now() - t0 };
  } catch (e) {
    console.warn(`[embedder] ${device}/${dtype} load failed:`, e);
    return null;
  }
}

export async function initEmbedder(onStatus?: (s: EmbedderState) => void): Promise<void> {
  if (pipe) return;
  onStatus?.({ ...state });

  if (typeof navigator !== 'undefined' && 'gpu' in navigator) {
    const r = await tryLoad('webgpu', 'q8');
    if (r) {
      pipe = r.pipe;
      state.backend = 'webgpu';
      state.dtype = 'q8';
      state.modelLoadMs = Math.round(r.ms);
      state.ready = true;
      onStatus?.({ ...state });
      return;
    }
    console.warn('[embedder] WebGPU unavailable or failed, falling back to WASM');
  }

  // WASM fallback path (q8 first, fp32 as last resort).
  const wasmQ8 = await tryLoad('wasm', 'q8');
  if (wasmQ8) {
    pipe = wasmQ8.pipe;
    state.backend = 'wasm';
    state.dtype = 'q8';
    state.modelLoadMs = Math.round(wasmQ8.ms);
    state.ready = true;
    onStatus?.({ ...state });
    return;
  }

  const wasmFp32 = await tryLoad('wasm', 'fp32');
  if (wasmFp32) {
    pipe = wasmFp32.pipe;
    state.backend = 'wasm';
    state.dtype = 'fp32';
    state.modelLoadMs = Math.round(wasmFp32.ms);
    state.ready = true;
    onStatus?.({ ...state });
    return;
  }

  throw new Error('无法加载 embedding 模型（WebGPU 与 WASM 均失败）');
}

function l2Normalize(v: number[]): number[] {
  const norm = Math.sqrt(v.reduce((s, x) => s + x * x, 0)) || 1;
  return v.map((x) => x / norm);
}

// Embed a batch of texts (CLS pooled by the model, L2-normalized here).
export async function embedTexts(
  texts: string[],
  onProgress?: (done: number, total: number) => void,
): Promise<{ embeddings: number[][]; ms: number }> {
  if (!pipe) throw new Error('embedder not initialized');
  const t0 = performance.now();
  const out: number[][] = [];
  let done = 0;
  for (let i = 0; i < texts.length; i += BATCH_SIZE) {
    const batch = texts.slice(i, i + BATCH_SIZE);
    const res = await pipe(batch, { pooling: 'cls', normalize: true });
    const tensor = res as unknown as { data: Float32Array | number[]; dims: number[] };
    const dim = tensor.dims[tensor.dims.length - 1];
    const flat = Array.from(tensor.data);
    for (let r = 0; r < batch.length; r++) {
      out.push(l2Normalize(flat.slice(r * dim, (r + 1) * dim)));
    }
    done += batch.length;
    onProgress?.(done, texts.length);
  }
  return { embeddings: out, ms: performance.now() - t0 };
}
