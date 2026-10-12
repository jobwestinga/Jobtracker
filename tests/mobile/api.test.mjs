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
async function setup(fetch, module = 'api.js') {
  globalThis.localStorage = new Storage();
  Object.defineProperty(globalThis, 'navigator', { value: {}, configurable: true });
  globalThis.fetch = fetch;
  return importWebModule(module);
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

test('failed acknowledgement storage reports the durable save and retries its original identity', async () => {
  const transmissions = [], committed = new Set();
  let failAcknowledgement = true;
  const client = await setup((_url, request) => {
    const entry = JSON.parse(request.body).ops[0];
    transmissions.push(entry);
    committed.add(entry.op_id);
    if (failAcknowledgement) { failAcknowledgement = false; localStorage.fail = true; }
    return accepted(request);
  });
  const outcome = await client.send('add_session', {
    subject_uid: 's', start_time: '2026-10-10T06:40:00', end_time: '2026-10-10T09:30:00',
  }, { uid: 'session-once' });
  assert.equal(outcome.ok, false);
  assert.equal(outcome.queued, true, 'the form must not offer a new save after a durable enqueue');
  assert.match(outcome.error, /cannot save changes/);
  assert.equal(client.pendingCount(), 1);
  assert.deepEqual(client.pendingOperations()[0], transmissions[0]);
  localStorage.fail = false;
  assert.equal((await client.flush()).ok, true);
  assert.equal(client.pendingCount(), 0);
  assert.equal(committed.size, 1, 'replayed receipt prevents another server mutation');
  assert.deepEqual(transmissions[1], transmissions[0]);
  assert.equal(transmissions[1].params.uid, 'session-once');
});

test('malformed success or missing acknowledgement cannot discard unsent work', async () => {
  let response = new Response('invalid json', { status: 200 });
  const client = await setup(() => response);
  assert.equal((await client.send('add_goal', { name: 'A' })).queued, true);
  response = reply({ applied: [] });
  assert.equal((await client.flush()).ok, false);
  assert.equal(client.pendingCount(), 1);
});

for (const [label, applied] of [
  ['object instead of list', {}], ['null row', [null]],
  ['unknown receipt', [{ op_id: 'foreign' }]], ['later receipt only', [{ op_id: 'b' }]],
  ['reordered receipts', [{ op_id: 'b' }, { op_id: 'a' }]],
  ['duplicate receipts', [{ op_id: 'a' }, { op_id: 'a' }]],
  ['extra receipt', [{ op_id: 'a' }, { op_id: 'b' }, { op_id: 'c' }]],
]) {
  for (const status of [200, 400]) test(`${label} on HTTP ${status} preserves the entire ordered queue`, async () => {
    const client = await setup(() => reply({ detail: 'refused', applied }, status));
    const queue = [{ op_id: 'a', op: 'add_goal', params: { name: 'A' } },
      { op_id: 'b', op: 'delete_goal', params: { goal_uid: 'a' } }];
    localStorage.setItem('jobtracker.outbox', JSON.stringify(queue));
    const outcome = await client.flush();
    assert.equal(outcome.ok, false);
    assert.equal(outcome.sent, 0);
    assert.match(outcome.error, /invalid acknowledgements/);
    assert.deepEqual(client.pendingOperations(), queue);
  });
}

test('failed token removal reports that the phone is still connected', async () => {
  const client = await setup(() => reply({ head: 1 }));
  localStorage.removeItem = () => { throw Error('storage blocked'); };
  assert.throws(() => client.clearToken(), /still connected/);
  assert.equal(client.getToken(), 'test-token');
});

test('a stale refusal cannot acknowledge work after credentials change', async () => {
  let release;
  const client = await setup((_url, request) => new Promise((resolve) => {
    const [first] = JSON.parse(request.body).ops;
    release = () => resolve(reply({ detail: 'refused', applied: [{ op_id: first.op_id }] }, 400));
  }));
  const queue = [{ op_id: 'a', op: 'add_goal', params: { name: 'A' } },
    { op_id: 'b', op: 'delete_goal', params: { goal_uid: 'a' } }];
  localStorage.setItem('jobtracker.outbox', JSON.stringify(queue));
  const flushing = client.flush();
  await until(() => Boolean(release));
  client.setToken('new-credential');
  release();
  assert.equal((await flushing).sent, 0);
  assert.deepEqual(client.pendingOperations(), queue);
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

test('another tab changing credentials cannot replay a retained draft under its token', async () => {
  let release;
  const requests = [];
  const client = await setup((_url, request) => {
    requests.push({ token: request.headers.Authorization, ops: JSON.parse(request.body).ops });
    if (requests.length === 1) return new Promise((resolve) => { release = () => resolve(accepted(request)); });
    return accepted(request);
  });
  const saving = client.send('add_goal', { name: 'Original private draft' }, { uid: 'original-goal' });
  await until(() => Boolean(release));
  const saved = localStorage.getItem('jobtracker.outbox');
  const owner = localStorage.getItem('jobtracker.outbox-owner');
  assert.ok(owner); assert.equal(owner.includes('test-token'), false);
  client.setToken('different-account');
  assert.deepEqual(client.pendingForProjection(), [], 'foreign pending drafts never enter this account view');
  assert.equal(client.pendingCount(), 1, 'raw recovery count remains visible');
  assert.equal(client.pendingTokenMatches('test-token'), true, 'setup permits reconnecting the owner');
  assert.equal(client.pendingTokenMatches('different-account'), false);
  release();
  assert.equal((await saving).queued, true);
  await assert.rejects(client.flush(), /Reconnect the original token/);
  await assert.rejects(client.send('add_goal', { name: 'Another account draft' }), /another connection/);
  assert.equal(requests.length, 1);
  assert.equal(localStorage.getItem('jobtracker.outbox'), saved);
  assert.equal(localStorage.getItem('jobtracker.outbox-owner'), owner);
  client.setToken('test-token');
  assert.deepEqual(client.pendingForProjection(), JSON.parse(saved));
  assert.equal((await client.flush()).ok, true);
  assert.deepEqual(requests[1], requests[0], 'original credential, identity and parameters replay together');
  assert.equal(client.pendingCount(), 0);
  client.setToken('different-account');
  assert.equal((await client.send('add_goal', { name: 'New account after drain' })).ok, true);
  assert.equal(requests[2].token, 'Bearer different-account');
  assert.notEqual(localStorage.getItem('jobtracker.outbox-owner'), owner);
});

test('a credential change while waiting for the queue lock rejects before persistence', async () => {
  let unlock;
  let requests = 0;
  const client = await setup(() => { requests++; throw Error('must not fetch'); });
  navigator.locks = { request: (_name, callback) => new Promise((resolve) => {
    unlock = () => { try { resolve(callback()); } catch (error) { resolve(Promise.reject(error)); } };
  }) };
  const saving = client.send('add_goal', { name: 'Keep the unsaved form' });
  client.setToken('different-account');
  unlock();
  await assert.rejects(saving, /changed before saving/);
  assert.equal(client.pendingCount(), 0);
  assert.equal(localStorage.getItem('jobtracker.outbox-owner'), null);
  assert.equal(requests, 0);
});

test('legacy queue ownership is persisted before replay without changing operation JSON', async () => {
  const queue = [{ op_id: 'legacy', op: 'add_goal', params: { uid: 'legacy-goal', name: 'Saved offline' } }];
  const client = await setup((_url, request) => {
    assert.ok(localStorage.getItem('jobtracker.outbox-owner'));
    assert.deepEqual(JSON.parse(localStorage.getItem('jobtracker.outbox')), queue);
    assert.deepEqual(JSON.parse(request.body).ops, queue);
    return accepted(request);
  });
  localStorage.setItem('jobtracker.outbox', JSON.stringify(queue));
  assert.equal((await client.flush()).ok, true);
  assert.equal(client.pendingCount(), 0);
});

test('failed ownership persistence keeps a legacy queue and sends nothing', async () => {
  let requests = 0;
  const client = await setup(() => { requests++; throw Error('must not fetch'); });
  const queue = [{ op_id: 'legacy', op: 'add_goal', params: { name: 'Saved offline' } }];
  const saved = JSON.stringify(queue);
  localStorage.setItem('jobtracker.outbox', saved);
  localStorage.fail = true;
  await assert.rejects(client.flush(), /cannot save changes/);
  assert.equal(localStorage.getItem('jobtracker.outbox'), saved);
  assert.equal(requests, 0);
});

test('a captured POST credential is rejected before fetch if the connection changed', async () => {
  let requests = 0;
  const client = await setup(() => { requests++; throw Error('must not fetch'); });
  client.setToken('different-account');
  await assert.rejects(client.api.sendOps([{ op_id: 'original', op: 'add_goal', params: {} }], 'test-token'),
    (error) => error.status === 409);
  assert.equal(requests, 0);
});

test('status observers report enqueue, send and acknowledgement without changing persistence', async () => {
  const client = await setup((_url, request) => accepted(request), 'data/outbox.js');
  const events = [];
  client.subscribeOutbox(() => { throw Error('broken view'); });
  const unsubscribe = client.subscribeOutbox((event) => events.push(event));
  const result = await client.send('add_goal', { name: 'Saved despite a broken status view' });
  assert.equal(result.ok, true); assert.equal(client.pendingCount(), 0);
  assert.deepEqual(events.map((event) => event.type), ['queue', 'sending', 'queue', 'result']);
  assert.equal(events[0].pending, 1); assert.equal(events[2].pending, 0);
  assert.equal(events[3].ok, true); assert.equal(events[3].sent, 1);
  unsubscribe();
  await client.flush(); assert.equal(events.length, 4);
});

test('status observers distinguish a failed unsaved draft from a saved queued operation', async () => {
  const client = await setup(() => { throw Error('must not fetch'); }, 'data/outbox.js');
  const events = []; client.subscribeOutbox((event) => events.push(event));
  localStorage.fail = true;
  await assert.rejects(client.send('add_goal', { name: 'Keep this form' }), /cannot save changes/);
  assert.equal(client.pendingCount(), 0);
  assert.equal(events.length, 1); assert.equal(events[0].ok, false);
  assert.match(events[0].error, /cannot save changes/);
});
