import test from 'node:test';
import assert from 'node:assert/strict';
import { importWebModule } from './support/import-web.mjs';
const { createSyncStatus } = await importWebModule('data/sync-status.js');

function setup() {
  const values = new Map();
  const storage = { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
  const state = { token: 'status-account', pending: 0, now: Date.parse('2026-10-12T08:30:00Z') };
  const model = createSyncStatus({ getToken: () => state.token, pendingCount: () => state.pending,
    storage, now: () => state.now });
  return { state, storage, values, model };
}

test('only a confirmed successful pass with no pending work records a sync time', () => {
  const { model, state } = setup();
  assert.equal(model.snapshot().lastSuccessAt, null);
  assert.equal(model.snapshot().label, 'Checking sync');
  state.pending = 2;
  assert.equal(model.success().lastSuccessAt, null);
  assert.equal(model.snapshot().label, 'Saved here · 2 waiting');
  state.pending = 0;
  assert.equal(model.success().lastSuccessAt, state.now);
  assert.match(model.snapshot().label, /^Synced /);
});

test('offline, blocked and later queue additions preserve the last success without pretending to sync', () => {
  const { model, state } = setup();
  model.success(); const confirmed = state.now;
  state.now += 3600000; state.pending = 1;
  assert.equal(model.failure({ offline: true, message: 'No connection' }).label, 'Offline · 1 waiting');
  assert.equal(model.snapshot().lastSuccessAt, confirmed);
  state.pending = 2; model.outbox({ type: 'queue', pending: 2 });
  assert.equal(model.snapshot().label, 'Offline · 2 waiting');
  assert.equal(model.failure({ error: 'Invalid operation' }).label, 'Sync attention · 2 waiting');
  assert.match(model.snapshot().detail, /saved on this phone/);
  assert.match(model.snapshot().detail, /Invalid operation/);
  assert.equal(model.outbox({ type: 'sending' }).label, 'Sending · 2 waiting');
  assert.equal(model.snapshot().lastSuccessAt, confirmed);
});

test('lost replies and acknowledgement storage failure remain visibly saved on the phone', () => {
  for (const offline of [false, true]) {
    const { model, state } = setup();
    state.pending = 1; model.outbox({ type: 'sending' });
    assert.equal(model.snapshot().label, 'Sending · 1 waiting');
    model.outbox({ type: 'result', ok: false, offline, error: 'Reply not saved' });
    assert.equal(model.snapshot().pending, 1);
    assert.equal(model.snapshot().lastSuccessAt, null);
    assert.match(model.snapshot().detail, /saved on this phone/);
  }
});

test('an empty queue flush or quiet refresh alone never updates the last successful timestamp', () => {
  const { model, state } = setup();
  model.outbox({ type: 'result', ok: true, sent: 0 });
  assert.equal(model.snapshot().lastSuccessAt, null);
  model.success(); const before = model.snapshot();
  state.now += 60000;
  assert.deepEqual(model.begin({ quiet: true }), before);
  assert.deepEqual(model.outbox({ type: 'result', ok: true, sent: 0 }), before);
});

test('an older batch completing cannot mark a newer queued operation synced', () => {
  const { model, state } = setup();
  model.success(); const confirmed = state.now;
  state.now += 60000; state.pending = 1;
  model.outbox({ type: 'result', ok: true, sent: 1 });
  assert.equal(model.snapshot().lastSuccessAt, confirmed);
  assert.equal(model.snapshot().label, 'Saved here · 1 waiting');
  state.pending = 0;
  assert.equal(model.outbox({ type: 'result', ok: true, sent: 1 }).lastSuccessAt, state.now);
});

test('last sync survives restart only for its original credential without storing a token', () => {
  const { model, state, storage, values } = setup();
  model.success();
  const saved = values.get('jobtracker.sync-status');
  assert.equal(saved.includes(state.token), false);
  const restarted = createSyncStatus({ getToken: () => state.token, pendingCount: () => state.pending, storage });
  assert.equal(restarted.snapshot().lastSuccessAt, state.now);
  state.token = 'another-account';
  assert.equal(restarted.snapshot().lastSuccessAt, null);
  assert.equal(restarted.snapshot().error, '');
  state.token = '';
  assert.equal(restarted.snapshot().label, 'Disconnected');
});

test('optional status storage or rendering failure cannot interrupt an acknowledged save', () => {
  const { state } = setup();
  const broken = createSyncStatus({ getToken: () => state.token, pendingCount: () => state.pending,
    storage: { getItem: () => { throw Error('blocked'); }, setItem: () => { throw Error('quota'); } },
    now: () => state.now, onChange: () => { throw Error('view destroyed'); } });
  assert.doesNotThrow(() => broken.success());
  assert.equal(broken.snapshot().lastSuccessAt, state.now);
});

test('a damaged pending queue is shown as attention rather than zero waiting or synced', () => {
  const model = createSyncStatus({ getToken: () => 'account', pendingCount: () => { throw Error('Saved changes are damaged'); },
    storage: { getItem: () => null, setItem: () => {} } });
  const value = model.success();
  assert.equal(value.phase, 'blocked'); assert.equal(value.pending, null);
  assert.equal(value.lastSuccessAt, null); assert.match(value.detail, /damaged/);
});

test('a confirmed sync from an earlier day names its date instead of only a clock time', () => {
  const { model, state } = setup();
  model.success();
  const sameDay = model.snapshot().label;
  assert.match(sameDay, /^Synced \d{1,2}[:.]\d{2}/);
  state.now += 2 * 86400000;
  const later = model.snapshot().label;
  assert.notEqual(later, sameDay);
  assert.match(later, /12/);
  assert.match(later, /Oct/);
});
