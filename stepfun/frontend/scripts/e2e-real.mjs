// Real end-to-end: model embeddings -> backend -> seekdb write -> hybrid search.
import { pipeline, env } from '@huggingface/transformers';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
env.allowLocalModels = true;
env.localModelPath = path.resolve(__dirname, '../..', 'backend', 'public', 'models');

const pipe = await pipeline('feature-extraction', 'Xenova/bge-small-zh-v1.5', {
  dtype: 'q8',
  device: 'cpu',
});

async function embed(texts) {
  const res = await pipe(texts, { pooling: 'cls', normalize: true });
  const dim = res.dims[res.dims.length - 1];
  const flat = Array.from(res.data);
  const rows = [];
  for (let r = 0; r < texts.length; r++) rows.push(flat.slice(r * dim, (r + 1) * dim));
  return rows;
}

const API = 'http://127.0.0.1:3001';
async function req(method, url, data) {
  const r = await fetch(API + url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: data ? JSON.stringify(data) : undefined,
  });
  return r.json();
}

const docs = [
  '今天天气很好，适合去公园散步',
  '数据库向量检索技术发展迅速',
  'HNSW 索引支持近似最近邻查询',
  '全文检索使用 IK 分词器处理中文',
];
const [embeddings] = [await embed(docs)];

const ids = docs.map((_, i) => `e2e-${i}`);
const add = await req('POST', '/api/collections/kb/documents', {
  items: docs.map((doc, i) => ({
    id: ids[i],
    document: doc,
    embedding: embeddings[i],
    metadata: { source: 'e2e-test', chars: doc.length },
  })),
});
console.log('add:', add);

for (const [query, expected] of [
  ['想出去走走', '公园'],
  ['怎么查文档', '全文检索/向量检索'],
  ['近似最近邻算法', 'HNSW'],
]) {
  const [qv] = [await embed([query])];
  const r = await req('POST', '/api/collections/kb/search', {
    queryEmbedding: qv[0],
    queryText: query,
    nResults: 3,
    mode: 'hybrid',
  });
  console.log(`\nquery: ${query} (期望含: ${expected})  [${r.ms}ms]`);
  for (const row of r.rows) {
    console.log(`  - ${row.document}  (dist ${row.distance?.toFixed(4) ?? '—'})`);
  }
}
