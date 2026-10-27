// Backend for the StepFun mini knowledge base.
// The browser does all embedding inference locally; this server only moves
// data between the browser and seekdb (via seekdb-js) and serves model files.
import express from 'express';
import cors from 'cors';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  connectSeekdb,
  addDocuments,
  hybridSearch,
  vectorSearch,
  listCollections,
  deleteCollection,
  getOrCreateKbCollection,
} from './seekdb-store.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const app = express();
app.use(cors());
app.use(express.json({ limit: '64mb' }));

// Model files (ONNX) are served statically so the browser loads them locally.
app.use('/models', express.static(path.join(__dirname, 'public', 'models')));

// Retry: in docker-compose the seekdb service may still be starting when we boot.
let client = null;
for (let attempt = 1; attempt <= 20; attempt++) {
  try {
    client = await connectSeekdb();
    console.log(`[seekdb] connected (attempt ${attempt})`);
    break;
  } catch (e) {
    if (attempt === 20) {
      console.error('[seekdb] connection failed after 20 attempts:', e.message || e);
      process.exit(1);
    }
    console.warn(`[seekdb] not ready (attempt ${attempt}): ${e.message || e}; retrying in 3s`);
    await new Promise((r) => setTimeout(r, 3000));
  }
}

app.get('/api/health', async (_req, res) => {
  try {
    const rows = await client.execute('SELECT VERSION() AS v');
    res.json({ ok: true, version: rows?.[0]?.v ?? 'unknown' });
  } catch (e) {
    res.status(503).json({ ok: false, error: String(e.message || e) });
  }
});

app.get('/api/collections', async (_req, res) => {
  try {
    res.json({ collections: await listCollections(client) });
  } catch (e) {
    res.status(500).json({ error: String(e.message || e) });
  }
});

app.post('/api/collections', async (req, res) => {
  const { name } = req.body || {};
  if (!name || !/^[a-zA-Z0-9_-]{1,64}$/.test(name)) {
    return res.status(400).json({ error: 'name must be 1-64 chars of [a-zA-Z0-9_-]' });
  }
  try {
    const collection = await getOrCreateKbCollection(client, name);
    res.json({ ok: true, name: collection.name, dimension: collection.dimension });
  } catch (e) {
    res.status(500).json({ error: String(e.message || e) });
  }
});

app.delete('/api/collections/:name', async (req, res) => {
  try {
    await deleteCollection(client, req.params.name);
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: String(e.message || e) });
  }
});

app.post('/api/collections/:name/documents', async (req, res) => {
  const { items } = req.body || {};
  if (!Array.isArray(items) || items.length === 0) {
    return res.status(400).json({ error: 'items must be a non-empty array' });
  }
  for (const it of items) {
    if (!it.id || typeof it.document !== 'string' || !Array.isArray(it.embedding)) {
      return res.status(400).json({ error: 'each item needs id/document/embedding' });
    }
  }
  try {
    const { addMs, refreshMs, count } = await addDocuments(client, req.params.name, items);
    res.json({ ok: true, count, addMs: Math.round(addMs), refreshMs: Math.round(refreshMs) });
  } catch (e) {
    res.status(500).json({ error: String(e.message || e) });
  }
});

app.post('/api/collections/:name/search', async (req, res) => {
  const { queryEmbedding, queryText, nResults = 5, mode = 'hybrid' } = req.body || {};
  if (!Array.isArray(queryEmbedding)) {
    return res.status(400).json({ error: 'queryEmbedding is required' });
  }
  try {
    if (mode === 'vector') {
      const r = await vectorSearch(client, req.params.name, { queryEmbedding, nResults });
      return res.json({ mode, rows: r.rows, ms: Math.round(r.ms) });
    }
    const r = await hybridSearch(client, req.params.name, { queryEmbedding, queryText, nResults });
    return res.json({ mode, rows: r.rows, ms: Math.round(r.ms) });
  } catch (e) {
    res.status(500).json({ error: String(e.message || e) });
  }
});

// Serve the built frontend (single-process deployment: one URL serves app + API + models).
import fs from 'node:fs';
const distDir = process.env.FRONTEND_DIST || path.join(__dirname, '..', 'frontend', 'dist');
if (fs.existsSync(path.join(distDir, 'index.html'))) {
  app.use(express.static(distDir));
  app.get(/^\/(?!api\/|models\/).*/, (_req, res) => {
    res.sendFile(path.join(distDir, 'index.html'));
  });
  console.log(`[static] serving frontend from ${distDir}`);
} else {
  console.log(`[static] no frontend build at ${distDir} (dev mode: use the Vite dev server)`);
}

const PORT = Number(process.env.PORT || 3001);
app.listen(PORT, () => {
  console.log(`[http] listening on http://127.0.0.1:${PORT}`);
});
