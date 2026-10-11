import test from 'node:test';
import assert from 'node:assert/strict';
import { importWebModule } from './support/import-web.mjs';

class Storage {
  values = new Map([['jobtracker.token', 'test-token']]);
  fail = false;
  getItem(key) { return this.values.get(key) ?? null; }
  setItem(key, value) { if (this.fail) throw Error('quota'); this.values.set(key, value); }
  removeItem(key) { this.values.delete(key); }
}
async function setup(fetch) {
  globalThis.localStorage = new Storage();
  Object.defineProperty(globalThis, 'navigator', { value: {}, configurable: true });
  globalThis.fetch = fetch;
  return importWebModule('api.js');
}
const reply = (body, status = 200) => new Response(JSON.stringify(body), { status });
const accepted = (request) => reply({ applied: JSON.parse(request.body).ops.map((entry) => ({ op_id: entry.op_id, result: { ok: true } })) });
const until = async (predicate) => {
  for (let i = 0; i < 100 && !predicate(); i++) await new Promise((resolve) => setImmediate(resolve));
  assert.ok(predicate(), 'condition should settle');
};

test('write is durable before the request and survives a page closing mid-request', async () => {
  let release;
  let transmitted;
  const client = await setup((_url, request) => {
    transmitted = JSON.parse(request.body).ops[0];
    assert.equal(JSON.parse(localStorage.getItem('jobtracker.outbox'))[0].op_id, transmitted.op_id);
    return new Promise((resolve) => { release = () => resolve(accepted(request)); });
  });
  const sending = client.send('start_subject', { subject_uid: 's', start_time: '2026-10-10T10:00:00' }, { uid: 'timer' });
  await until(() => Boolean(release));
  const saved = localStorage.getItem('jobtracker.outbox');
  const reloaded = await importWebModule('api.js');
  assert.equal(reloaded.pendingCount(), 1);
  assert.equal(reloaded.pendingOperations()[0].op_id, transmitted.op_id);
  assert.equal(JSON.parse(saved)[0].params.uid, 'timer');
  release();
  assert.equal((await sending).queued, false);
});

test('concurrent writes and flushes stay ordered and do not erase a newer append', async () => {
  let release;
  const seen = [];
  const client = await setup((_url, request) => {
    const ops = JSON.parse(request.body).ops;
    seen.push(...ops.map((entry) => entry.op));
    if (seen.length === 1) return new Promise((resolve) => { release = () => resolve(accepted(request)); });
    return Promise.resolve(accepted(request));
  });
  const first = client.send('add_subject', { name: 'One' });
  await until(() => Boolean(release));
  const second = client.send('archive_subject', { subject_uid: 's' });
  const flush = client.flush();
  await until(() => client.pendingCount() === 2);
  release();
  const results = await Promise.all([first, second, flush]);
  assert.deepEqual(seen, ['add_subject', 'archive_subject']);
  assert.equal(client.pendingCount(), 0);
  assert.ok(results.every((result) => result.ok));
});

test('acknowledged prefix is removed on refusal and later operations remain ordered', async () => {
  const client = await setup((_url, request) => {
    const [first, failed] = JSON.parse(request.body).ops;
    return reply({ detail: 'invalid operation', failed_index: 1, failed_op_id: failed.op_id,
      applied: [{ op_id: first.op_id, result: { ok: true } }] }, 400);
  });
  localStorage.setItem('jobtracker.outbox', JSON.stringify([
    { op_id: 'a', op: 'add_goal', params: { name: 'A' } },
    { op_id: 'b', op: 'unknown', params: {} },
    { op_id: 'c', op: 'delete_goal', params: { goal_uid: 'g' } },
  ]));
  const result = await client.flush();
  assert.equal(result.ok, false);
  assert.equal(result.sent, 1);
  assert.equal(result.failed_op_id, 'b');
  assert.deepEqual(client.pendingOperations().map((entry) => entry.op_id), ['b', 'c']);
});

test('500 and a lost response keep the same operation id for idempotent retry', async () => {
  let attempts = 0;
  const ids = [];
  const client = await setup((_url, request) => {
    ids.push(JSON.parse(request.body).ops[0].op_id);
    attempts++;
    if (attempts === 1) return reply({ detail: 'server restarting' }, 503);
    if (attempts === 2) throw Error('connection dropped after commit');
    return accepted(request);
  });
  assert.equal((await client.send('add_session', { subject_uid: 's' })).queued, true);
  assert.equal(client.pendingCount(), 1);
  assert.equal((await client.flush()).offline, true);
  assert.equal(client.pendingCount(), 1);
  assert.equal((await client.flush()).ok, true);
  assert.equal(new Set(ids).size, 1);
});

test('large backlog is split at the server limit without reordering', async () => {
  const batches = [];
  const client = await setup((_url, request) => {
    batches.push(JSON.parse(request.body).ops.map((entry) => entry.op_id));
    return accepted(request);
  });
  const entries = Array.from({ length: 405 }, (_, i) => ({ op_id: `id-${i}`, op: 'add_goal', params: { name: `Goal ${i}` } }));
  localStorage.setItem('jobtracker.outbox', JSON.stringify(entries));
  const result = await client.flush();
  assert.equal(result.sent, 405);
  assert.deepEqual(batches.map((batch) => batch.length), [200, 200, 5]);
  assert.deepEqual(batches.flat(), entries.map((entry) => entry.op_id));
});

test('storage full and corrupt queue fail visibly without sending or overwriting data', async () => {
  let requests = 0;
  const client = await setup(() => { requests++; throw Error('must not fetch'); });
  localStorage.fail = true;
  await assert.rejects(client.send('add_goal', { name: 'A' }), /cannot save changes/);
  localStorage.fail = false;
  localStorage.setItem('jobtracker.outbox', '{broken');
  await assert.rejects(client.send('add_goal', { name: 'B' }), /Cannot read saved changes/);
  assert.equal(localStorage.getItem('jobtracker.outbox'), '{broken');
  assert.equal(requests, 0);
});

test('malformed success or missing acknowledgement cannot discard unsent work', async () => {
  let response = new Response('invalid json', { status: 200 });
  const client = await setup(() => response);
  assert.equal((await client.send('add_goal', { name: 'A' })).queued, true);
  response = reply({ applied: [] });
  assert.equal((await client.flush()).ok, false);
  assert.equal(client.pendingCount(), 1);
});

test('authenticated requests never follow redirects or use browser HTTP cache', async () => {
  const client = await setup((_url, request) => {
    assert.equal(request.redirect, 'error');
    assert.equal(request.cache, 'no-store');
    return reply({ head: 1 });
  });
  assert.equal((await client.api.context()).head, 1);
});

test('a stale authenticated response is rejected after the token changes', async () => {
  let release;
  const client = await setup(() => new Promise((resolve) => { release = () => resolve(reply({ private: 'old data' })); }));
  const loading = client.api.context();
  client.setToken('new-credential');
  release();
  await assert.rejects(loading, (error) => error.status === 409);
});

test('a connection that hangs is aborted and its durable operation remains queued', async () => {
  let aborted = false;
  const client = await setup((_url, request) => new Promise((_resolve, reject) => {
    request.signal.addEventListener('abort', () => { aborted = true; reject(Error('aborted')); });
  }));
  const realTimeout = globalThis.setTimeout;
  globalThis.setTimeout = (callback, ms, ...args) => ms === 12000
    ? setImmediate(callback, ...args) : realTimeout(callback, ms, ...args);
  try {
    const outcome = await client.send('add_goal', { name: 'Kept during timeout' });
    assert.equal(outcome.queued, true);
    assert.equal(outcome.offline, true);
    assert.equal(aborted, true);
    assert.equal(client.pendingCount(), 1);
  } finally { globalThis.setTimeout = realTimeout; }
});

test('Web Locks allow new taps to persist while a network drain is pending', async () => {
  let release;
  const client = await setup((_url, request) => new Promise((resolve) => { release = () => resolve(accepted(request)); }));
  const pending = new Map();
  navigator.locks = { request: async (name, callback) => {
    const earlier = pending.get(name) || Promise.resolve();
    let done;
    const tail = new Promise((resolve) => { done = resolve; });
    pending.set(name, tail);
    await earlier;
    try { return await callback(); } finally { done(); }
  } };
  const first = client.send('add_goal', { name: 'First' });
  await until(() => Boolean(release));
  const second = client.send('add_goal', { name: 'Second' });
  await until(() => client.pendingCount() === 2);
  const firstRelease = release;
  firstRelease();
  await until(() => client.pendingCount() === 1);
  release();
  await Promise.all([first, second]);
  assert.equal(client.pendingCount(), 0);
});
