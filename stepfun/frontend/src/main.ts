// UI wiring: import -> local embedding -> seekdb write; query -> local embedding -> seekdb hybrid search.
import { initEmbedder, embedTexts, state as embedderState } from './embedder';
import { addDocuments, deleteCollection, health, listCollections, search } from './api';

const $ = <T extends HTMLElement>(sel: string) => document.querySelector(sel) as T;

const backendBadge = $('#backend-badge');
const dbBadge = $('#db-badge');
const quantBadge = $('#quant-badge');
const collectionSelect = $('#collection-select') as HTMLSelectElement;
const importText = $('#import-text') as HTMLTextAreaElement;
const importFile = $('#import-file') as HTMLInputElement;
const importProgress = $('#import-progress') as HTMLElement;
const importMsg = $('#import-msg');
const queryText = $('#query-text') as HTMLInputElement;
const searchMode = $('#search-mode') as HTMLSelectElement;
const resultsEl = $('#results');
const searchStats = $('#search-stats');

let embedderReady = false;

function renderBackendBadge() {
  if (!embedderReady) {
    backendBadge.textContent = '推理后端：模型加载中…';
    backendBadge.className = 'badge warn';
    return;
  }
  const label = embedderState.backend === 'webgpu' ? 'WebGPU' : 'WASM';
  backendBadge.textContent = `推理后端：${label}`;
  backendBadge.className = 'badge';
  quantBadge.textContent = `模型：${embedderState.dtype === 'q8' ? 'INT8 (q8)' : 'FP32'} · 加载 ${embedderState.modelLoadMs}ms`;
}

async function refreshCollections(selectName?: string) {
  const { collections } = await listCollections();
  collectionSelect.innerHTML = '';
  for (const c of collections) {
    const opt = document.createElement('option');
    opt.value = c.name;
    opt.textContent = `${c.name}（${c.dimension}维 ${c.distance}）`;
    collectionSelect.appendChild(opt);
  }
  if (selectName) collectionSelect.value = selectName;
}

function chunkText(text: string, maxLen = 480): string[] {
  const paragraphs = text
    .split(/\n\s*\n|\r\n\s*\r\n/)
    .map((p) => p.trim())
    .filter(Boolean);
  const chunks: string[] = [];
  for (const p of paragraphs) {
    if (p.length <= maxLen) {
      chunks.push(p);
    } else {
      for (let i = 0; i < p.length; i += maxLen) chunks.push(p.slice(i, i + maxLen));
    }
  }
  return chunks;
}

async function ensureEmbedder() {
  if (!embedderReady) {
    renderBackendBadge();
    await initEmbedder((s) => renderBackendBadge());
    embedderReady = true;
    renderBackendBadge();
  }
}

async function doImport() {
  if (!collectionSelect.value) {
    importMsg.textContent = '请先选择或新建知识库';
    return;
  }
  const name = collectionSelect.value;
  const docs: { text: string; source: string }[] = [];
  if (importText.value.trim()) {
    for (const t of chunkText(importText.value)) docs.push({ text: t, source: '手动输入' });
  }
  for (const f of Array.from(importFile.files ?? [])) {
    const content = await f.text();
    for (const t of chunkText(content)) docs.push({ text: t, source: f.name });
  }
  if (docs.length === 0) {
    importMsg.textContent = '没有可导入的内容';
    return;
  }

  const btn = $('#btn-import') as HTMLButtonElement;
  btn.disabled = true;
  importMsg.textContent = '';
  try {
    await ensureEmbedder();
    const t0 = performance.now();
    const { embeddings, ms: embedMs } = await embedTexts(
      docs.map((d) => d.text),
      (done, total) => {
        importProgress.style.width = `${(done / total) * 80}%`;
        importMsg.textContent = `本地向量化中… ${done}/${total}`;
      },
    );
    importProgress.style.width = '85%';
    const items = docs.map((d, i) => ({
      id: `${Date.now()}-${i}-${crypto.randomUUID().slice(0, 8)}`,
      document: d.text,
      embedding: embeddings[i],
      metadata: { source: d.source, chars: d.text.length, importedAt: new Date().toISOString() },
    }));
    const r = await addDocuments(name, items);
    importProgress.style.width = '100%';
    const totalMs = Math.round(performance.now() - t0);
    importMsg.textContent =
      `✅ 已导入 ${r.count} 条：本地向量化 ${Math.round(embedMs)}ms（${items.length} 条，批量），` +
      `seekdb 写入 ${r.addMs}ms + 索引刷新 ${r.refreshMs}ms，总计 ${totalMs}ms`;
    importText.value = '';
    importFile.value = '';
    setTimeout(() => (importProgress.style.width = '0%'), 1500);
  } catch (e) {
    importMsg.textContent = `导入失败：${(e as Error).message}`;
  } finally {
    btn.disabled = false;
  }
}

async function doSearch() {
  const name = collectionSelect.value;
  const q = queryText.value.trim();
  if (!name) {
    resultsEl.innerHTML = '<div class="msg">请先选择或新建知识库</div>';
    return;
  }
  if (!q) {
    resultsEl.innerHTML = '<div class="msg">请输入查询内容</div>';
    return;
  }
  const btn = $('#btn-search') as HTMLButtonElement;
  btn.disabled = true;
  try {
    await ensureEmbedder();
    const t0 = performance.now();
    const { embeddings, ms: embedMs } = await embedTexts([q]);
    const mode = searchMode.value as 'hybrid' | 'vector';
    const r = await search(name, { queryEmbedding: embeddings[0], queryText: q, nResults: 5, mode });
    const totalMs = Math.round(performance.now() - t0);

    if (r.rows.length === 0) {
      resultsEl.innerHTML = '<div class="msg">没有检索到相关结果（若刚导入，索引刷新后即可检索）</div>';
    } else {
      resultsEl.innerHTML = r.rows
        .map(
          (row, i) => `
          <div class="result-item">
            <div class="meta">#${i + 1} · 距离 ${row.distance == null ? '—' : Number(row.distance).toFixed(4)}
              · 来源 ${row.metadata && row.metadata.source ? row.metadata.source : '—'}</div>
            <div class="doc">${escapeHtml(row.document ?? '')}</div>
          </div>`,
        )
        .join('');
    }
    searchStats.textContent =
      `查询向量化（本地 ${embedderState.backend === 'webgpu' ? 'WebGPU' : 'WASM'}）${Math.round(embedMs)}ms · ` +
      `seekdb 检索 ${r.ms}ms · 端到端 ${totalMs}ms`;
  } catch (e) {
    resultsEl.innerHTML = `<div class="msg">搜索失败：${(e as Error).message}</div>`;
  } finally {
    btn.disabled = false;
  }
}

function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

$('#btn-import').addEventListener('click', doImport);
$('#btn-search').addEventListener('click', doSearch);
queryText.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') doSearch();
});
$('#btn-refresh-cols').addEventListener('click', () => refreshCollections().catch(() => {}));
$('#btn-create').addEventListener('click', async () => {
  const name = ($('#new-collection') as HTMLInputElement).value.trim();
  if (!name) return;
  try {
    const res = await fetch('/api/collections', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.error || `HTTP ${res.status}`);
    }
    ($('#new-collection') as HTMLInputElement).value = '';
    await refreshCollections(name);
  } catch (e) {
    importMsg.textContent = `创建失败：${(e as Error).message}`;
  }
});
$('#btn-delete').addEventListener('click', async () => {
  if (!collectionSelect.value) return;
  if (!confirm(`删除知识库「${collectionSelect.value}」？`)) return;
  await deleteCollection(collectionSelect.value);
  await refreshCollections();
});

(async () => {
  try {
    const h = await health();
    dbBadge.textContent = `数据库：seekdb ${h.version ?? ''}`;
    dbBadge.className = 'badge';
    await refreshCollections();
  } catch {
    dbBadge.textContent = '数据库：连接失败（后端未启动？）';
    dbBadge.className = 'badge warn';
  }
  ensureEmbedder().catch((e) => {
    backendBadge.textContent = `推理后端：加载失败（${(e as Error).message}）`;
    backendBadge.className = 'badge warn';
  });
})();
