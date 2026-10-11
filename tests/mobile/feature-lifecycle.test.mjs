import test from 'node:test';
import assert from 'node:assert/strict';
import { importWebModule } from './support/import-web.mjs';
const { createSessionsFeature } = await importWebModule('features/sessions/index.js');
const { createGraphsFeature } = await importWebModule('features/graphs/index.js');
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };
const store = () => ({ snapshot: { subjects: [] }, active: null, context: { today: '2026-10-11' } });

test('session navigation commits only the latest day response and keeps its own offline cache', async () => {
  const first = deferred(), second = deferred(); let saves = 0;
  const feature = createSessionsFeature({ store: store(), api: { day: (day) => day === '2026-10-10' ? first.promise : second.promise }, saveMirror: () => saves++ });
  feature.prepare('2026-10-10'); const loadingFirst = feature.load();
  feature.prepare('2026-10-11'); const loadingSecond = feature.load();
  second.resolve({ sessions: [{ uid: 'latest' }] }); await loadingSecond;
  first.resolve({ sessions: [{ uid: 'stale' }] }); await loadingFirst;
  assert.equal(feature.day, '2026-10-11'); assert.equal(feature.status, 'ready');
  assert.deepEqual(feature.cache, { '2026-10-11': [{ uid: 'latest' }] }); assert.equal(saves, 1);
  feature.prepare('2026-10-11'); await feature.load(); assert.equal(saves, 1, 'cached day needs no extra request/write');
});

test('changing credentials/reset invalidates an outstanding sessions request', async () => {
  const response = deferred();
  const feature = createSessionsFeature({ store: store(), api: { day: () => response.promise }, saveMirror: () => assert.fail('old response cannot be cached') });
  feature.prepare('2026-10-11'); const loading = feature.load(); feature.reset();
  response.resolve({ sessions: [{ uid: 'private' }] }); await loading;
  assert.equal(feature.day, null); assert.deepEqual(feature.cache, {});
});

test('graphs ignore older responses and reset-owned requests; an active timer bypasses totals cache', async () => {
  globalThis.localStorage = { getItem: () => null };
  const requests = [deferred(), deferred(), deferred()]; let call = 0; const canonical = store();
  const feature = createGraphsFeature({ store: canonical, api: { breakdown: () => requests[call++].promise }, getView: () => 'weight' });
  const first = feature.load(), second = feature.load();
  requests[1].resolve({ buckets: [{ total_seconds: 20 }] }); await second;
  requests[0].resolve({ buckets: [{ total_seconds: 10 }] }); await first;
  assert.equal(feature.data.data.buckets[0].total_seconds, 20);
  await feature.load(); assert.equal(call, 2, 'idle cached totals avoid another fetch');
  canonical.active = { uid: 'timer' }; const live = feature.load(); assert.equal(call, 3);
  feature.reset(); requests[2].resolve({ buckets: [{ total_seconds: 30 }] }); await live;
  assert.equal(feature.data, null, 'reset must prevent old graph data from appearing');
});
