import test from 'node:test';
import assert from 'node:assert/strict';
import { importWebModule } from './support/import-web.mjs';
const { createMirror } = await importWebModule('data/mirror.js');

const canonical = () => ({ head: 10, subjects: [{ uid: 's', name: 'Work' }], goals: [], milestones: [], sessions: [], templates: [], settings: { day_start_time: '03:00' } });
function setup(api = {}, pending = [], days = {}) {
  const values = new Map([['jobtracker.token', 'mirror-test-account']]);
  globalThis.localStorage = { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
  const mirror = createMirror({ api, pendingOperations: () => pending, getDayCache: () => days });
  mirror.store.baseSnapshot = canonical(); mirror.store.head = 10;
  return { mirror, values };
}

test('saving while offline persists canonical rows and reconstructs the queued display after restart', () => {
  const queued = [{ op: 'add_subject', params: { uid: 'queued', name: 'Reading' } },
    { op: 'start_subject', params: { uid: 'timer', subject_uid: 'queued', start_time: '2026-10-11T09:30:00' } }];
  const { mirror, values } = setup({}, queued, { '2026-10-10': [{ uid: 'session' }] });
  mirror.store.context = { today: '2026-10-11', active: null };
  mirror.project(); mirror.save();
  const saved = JSON.parse(values.get('jobtracker.mirror'));
  assert.equal(saved.snapshot.subjects.length, 1, 'provisional rows must never become authority');
  assert.equal(saved.snapshot.subjects[0].pending, undefined);
  mirror.reset();
  const cached = mirror.restore();
  assert.equal(cached.dayCache['2026-10-10'][0].uid, 'session');
  assert.equal(mirror.store.snapshot.subjects[1].pending, true);
  assert.equal(mirror.store.active.uid, 'timer');
  assert.equal(mirror.store.active.timer_origin, new Date(queued[1].params.start_time).getTime());
});

test('failed or non-advancing multi-page pulls do not partially replace the canonical mirror', async () => {
  for (const stall of [false, true]) {
    let calls = 0;
    const { mirror } = setup({ pull: async (since) => {
      if (!calls++) return { seq: 11, more: true, changes: [{ table: 'subjects', op: 'upsert', uid: 's', row: { uid: 's', name: 'Changed' } }] };
      if (stall) return { seq: since, more: true, changes: [] };
      throw Error('connection lost');
    } });
    const before = mirror.store.baseSnapshot;
    await assert.rejects(mirror.update(false, { head: 12 }), stall ? /advance/ : /connection lost/);
    assert.equal(mirror.store.baseSnapshot, before);
    assert.equal(before.subjects[0].name, 'Work');
    assert.equal(mirror.store.head, 10);
  }
});

test('a complete feed applies settings and rows together; force or rewound head uses snapshot', async () => {
  const calls = [];
  const { mirror } = setup({ pull: async (since) => {
    calls.push(since);
    return since === 10 ? { seq: 11, more: true, changes: [{ table: 'subjects', op: 'delete', uid: 's' }] } :
      { seq: 12, more: false, changes: [], settings: { day_start_time: '04:00' } };
  }, snapshot: async () => ({ ...canonical(), head: 2 }) });
  await mirror.update(false, { head: 12 });
  assert.deepEqual(calls, [10, 11]);
  assert.deepEqual(mirror.store.baseSnapshot.subjects, []);
  assert.equal(mirror.store.baseSnapshot.settings.day_start_time, '04:00');
  assert.equal(mirror.store.head, 12);
  await mirror.update(false, { head: 2 });
  assert.equal(mirror.store.head, 2);
  assert.equal(mirror.store.baseSnapshot.subjects[0].name, 'Work');
  await mirror.update(true, { head: 20 });
  assert.equal(mirror.store.head, 2, 'snapshot head is authoritative');
});

test('cache write failure leaves durable operations and the visible projected data intact', () => {
  const queue = [{ op: 'add_goal', params: { uid: 'g', name: 'Finish' } }];
  const { mirror } = setup({}, queue);
  mirror.project();
  localStorage.setItem = () => { throw Error('quota'); };
  assert.doesNotThrow(mirror.save);
  assert.equal(queue.length, 1);
  assert.equal(mirror.store.snapshot.goals[0].name, 'Finish');
});

test('account-tagged mirrors cannot expose another account or an unverified legacy cache', () => {
  const pending = [{ op: 'add_goal', params: { uid: 'saved', name: 'Queued work' } }];
  const { mirror, values } = setup({}, pending, { '2026-10-10': [{ uid: 'private-history' }] });
  mirror.save();
  const raw = values.get('jobtracker.mirror');
  assert.ok(JSON.parse(raw).owner);
  assert.equal(raw.includes('mirror-test-account'), false, 'cache does not duplicate the credential');
  mirror.reset();
  values.set('jobtracker.token', 'another-account');
  assert.equal(mirror.restore(), null);
  assert.equal(mirror.store.baseSnapshot, null);
  assert.equal(pending.length, 1, 'cache rejection never deletes queued changes');
  assert.equal(values.get('jobtracker.mirror'), raw, 'a failed cleanup can leave the cache safely tagged');
  values.set('jobtracker.token', 'mirror-test-account');
  assert.ok(mirror.restore(), 'the original account still has its offline history');
  mirror.reset();
  const legacy = JSON.parse(raw); delete legacy.owner;
  values.set('jobtracker.mirror', JSON.stringify(legacy));
  assert.equal(mirror.restore(), null);
  values.delete('jobtracker.token');
  values.set('jobtracker.mirror', raw);
  assert.equal(mirror.restore(), null);
});
