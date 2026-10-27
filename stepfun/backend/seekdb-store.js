// seekdb data layer for the StepFun mini knowledge base.
// All vector + full-text retrieval happens inside seekdb (server-side).
import { SeekdbClient, Schema, VectorIndexConfig, FulltextIndexConfig } from 'seekdb';

const DB_NAME = process.env.SEEKDB_DATABASE || 'stepfun_kb';

export async function connectSeekdb() {
  // First connect to the built-in database, then ensure our app DB exists.
  const bootstrap = new SeekdbClient({
    host: process.env.SEEKDB_HOST || '127.0.0.1',
    port: Number(process.env.SEEKDB_PORT || 2881),
    user: process.env.SEEKDB_USER || 'root',
    password: process.env.SEEKDB_PASSWORD || '',
    database: 'test',
  });
  try {
    await bootstrap.execute(`CREATE DATABASE IF NOT EXISTS \`${DB_NAME}\``);
  } finally {
    await bootstrap.close();
  }

  const client = new SeekdbClient({
    host: process.env.SEEKDB_HOST || '127.0.0.1',
    port: Number(process.env.SEEKDB_PORT || 2881),
    user: process.env.SEEKDB_USER || 'root',
    password: process.env.SEEKDB_PASSWORD || '',
    database: DB_NAME,
  });
  return client;
}

// Collection schema: HNSW vector index (512-d, cosine) + IK full-text index (Chinese).
// bge-small-zh-v1.5 produces 512-d embeddings. embeddingFunction is explicitly null:
// embeddings are computed in the browser (Transformers.js, WebGPU/WASM) and sent to
// seekdb as plain vectors.
const EMBEDDING_DIM = 512;

function kbSchema() {
  return new Schema({
    vectorIndex: new VectorIndexConfig({
      hnsw: { dimension: EMBEDDING_DIM, distance: 'cosine', type: 'hnsw', lib: 'vsag' },
      embeddingFunction: null,
    }),
    fulltextIndex: new FulltextIndexConfig('ik'),
  });
}

export async function getOrCreateKbCollection(client, name) {
  const exists = await client.hasCollection(name);
  if (exists) return client.getCollection({ name, embeddingFunction: null });
  return client.createCollection({ name, schema: kbSchema(), embeddingFunction: null });
}

// Write documents (texts + pre-computed browser-side embeddings) into seekdb,
// then refresh the async vector index so new rows are searchable immediately.
export async function addDocuments(client, name, items) {
  const collection = await getOrCreateKbCollection(client, name);
  const t0 = performance.now();
  await collection.add({
    ids: items.map((it) => it.id),
    documents: items.map((it) => it.document),
    embeddings: items.map((it) => it.embedding),
    metadatas: items.map((it) => it.metadata || {}),
  });
  const addMs = performance.now() - t0;

  const t1 = performance.now();
  await collection.refresh_index();
  const refreshMs = performance.now() - t1;

  return { addMs, refreshMs, count: items.length };
}

// Hybrid search: full-text (IK) + vector ANN in ONE seekdb query, RRF-ranked.
export async function hybridSearch(client, name, { queryEmbedding, queryText, nResults = 5 }) {
  const collection = await getOrCreateKbCollection(client, name);
  const t0 = performance.now();
  const result = await collection.hybridSearch({
    query: queryText ? { whereDocument: { $contains: queryText } } : undefined,
    knn: { queryEmbeddings: queryEmbedding, nResults: Math.max(nResults * 4, 20) },
    rank: { rrf: {} },
    nResults,
    include: ['documents', 'metadatas', 'distances'],
  });
  const ms = performance.now() - t0;
  const flat = result.ids[0] ?? [];
  const rows = flat.map((id, i) => ({
    id,
    document: result.documents?.[0]?.[i] ?? null,
    metadata: result.metadatas?.[0]?.[i] ?? null,
    distance: result.distances?.[0]?.[i] ?? null,
  }));
  return { rows, ms };
}

// Vector-only search (comparison mode).
export async function vectorSearch(client, name, { queryEmbedding, nResults = 5 }) {
  const collection = await getOrCreateKbCollection(client, name);
  const t0 = performance.now();
  const result = await collection.query({
    queryEmbeddings: queryEmbedding,
    nResults,
    include: ['documents', 'metadatas', 'distances'],
  });
  const ms = performance.now() - t0;
  const flat = result.ids[0] ?? [];
  const rows = flat.map((id, i) => ({
    id,
    document: result.documents?.[0]?.[i] ?? null,
    metadata: result.metadatas?.[0]?.[i] ?? null,
    distance: result.distances?.[0]?.[i] ?? null,
  }));
  return { rows, ms };
}

export async function listCollections(client) {
  const cols = await client.listCollections({ withEmbeddingFunction: false });
  return cols.map((c) => ({ name: c.name, dimension: c.dimension, distance: c.distance }));
}

export async function deleteCollection(client, name) {
  await client.deleteCollection(name);
}
