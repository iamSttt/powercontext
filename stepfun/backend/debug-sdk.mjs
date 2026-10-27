import { connectSeekdb, getOrCreateKbCollection } from './seekdb-store.js';

const client = await connectSeekdb();
console.log('has kb:', await client.hasCollection('kb'));
const cols = await client.listCollections();
console.log('collections:', cols.map((c) => ({ name: c.name, id: c.collectionId, dim: c.dimension })));
const c = await getOrCreateKbCollection(client, 'kb');
console.log('got collection:', c.name, 'id=', c.collectionId, 'dim=', c.dimension);

const EQ = Array.from({ length: 384 }, (_, i) => 0.01 * i + 0.001);
try {
  const r = await c.hybridSearch({
    query: { whereDocument: { $contains: '公园' } },
    knn: { queryEmbeddings: EQ, nResults: 20 },
    rank: { rrf: {} },
    nResults: 3,
    include: ['documents', 'metadatas', 'distances'],
  });
  console.log('hybrid ok:', JSON.stringify(r.ids), JSON.stringify(r.documents));
} catch (e) {
  console.log('hybrid err:', e.message);
}
try {
  const r = await c.query({ queryEmbeddings: EQ, nResults: 3, include: ['documents', 'distances'] });
  console.log('query ok:', JSON.stringify(r.ids), JSON.stringify(r.documents));
} catch (e) {
  console.log('query err:', e.message);
}
await client.close();
