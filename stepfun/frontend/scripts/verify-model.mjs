// Sanity check: load the locally-served model files with Transformers.js (Node/WASM)
// and confirm the q8 (INT8) variant produces 384-d embeddings.
import { pipeline, env } from '@huggingface/transformers';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
env.allowLocalModels = true;
env.localModelPath = path.resolve(__dirname, '../..', 'backend', 'public', 'models');

const t0 = performance.now();
const pipe = await pipeline('feature-extraction', 'Xenova/bge-small-zh-v1.5', {
  dtype: 'q8',
  device: 'cpu',
});
const loadMs = Math.round(performance.now() - t0);

const t1 = performance.now();
const res = await pipe(['今天天气很好，适合去公园散步', '数据库向量检索技术发展迅速'], {
  pooling: 'cls',
  normalize: true,
});
const inferMs = Math.round(performance.now() - t1);

const dim = res.dims[res.dims.length - 1];
const flat = Array.from(res.data);
const rows = [];
for (let r = 0; r < 2; r++) rows.push(flat.slice(r * dim, (r + 1) * dim));
console.log('load(ms):', loadMs, 'infer(ms):', inferMs);
console.log('batch:', rows.length, 'dim:', dim);
console.log('sample[:5]:', rows[0].slice(0, 5).map((x) => x.toFixed(4)));
// quick semantic sanity: cosine between the two sentences should be positive-ish
const dot = rows[0].reduce((s, x, i) => s + x * rows[1][i], 0);
console.log('cosine(two sentences):', dot.toFixed(4));
