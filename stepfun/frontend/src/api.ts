// Thin client for the local backend (which talks to seekdb via seekdb-js).
export interface DocItem {
  id: string;
  document: string;
  embedding: number[];
  metadata: Record<string, unknown>;
}

export interface SearchRow {
  id: string;
  document: string | null;
  metadata: Record<string, unknown> | null;
  distance: number | null;
}

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((body as { error?: string }).error || `HTTP ${res.status}`);
  return body as T;
}

export const listCollections = () =>
  json<{ collections: { name: string; dimension: number; distance: string }[] }>('/api/collections');

export const deleteCollection = (name: string) =>
  json<{ ok: boolean }>(`/api/collections/${encodeURIComponent(name)}`, { method: 'DELETE' });

export const addDocuments = (name: string, items: DocItem[]) =>
  json<{ ok: boolean; count: number; addMs: number; refreshMs: number }>(
    `/api/collections/${encodeURIComponent(name)}/documents`,
    { method: 'POST', body: JSON.stringify({ items }) },
  );

export const search = (
  name: string,
  payload: { queryEmbedding: number[]; queryText?: string; nResults?: number; mode?: 'hybrid' | 'vector' },
) =>
  json<{ mode: string; rows: SearchRow[]; ms: number }>(
    `/api/collections/${encodeURIComponent(name)}/search`,
    { method: 'POST', body: JSON.stringify(payload) },
  );

export const health = () => json<{ ok: boolean; version?: string }>('/api/health');
