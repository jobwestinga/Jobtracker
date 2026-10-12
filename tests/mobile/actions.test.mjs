import test from 'node:test';
import assert from 'node:assert/strict';
import { importWebModule } from './support/import-web.mjs';

const { createActionRunner } = await importWebModule('data/actions.js');
function setup(overrides = {}) {
  const events = [];
  const outcome = { ok: true, queued: false, result: { session: { uid: 'saved' } } };
  const dependencies = {
    onMutation: () => events.push('mutation'),
    send: async (...args) => { events.push(['send', ...args]); return outcome; },
    project: () => events.push('project'), render: () => events.push('render'),
    refresh: async (options) => events.push(['refresh', options]),
    banner: (...args) => events.push(['banner', ...args]),
    ...overrides,
  };
  return { act: createActionRunner(dependencies), events, outcome };
}

test('an acknowledged action refreshes after its callback and preserves the captured UID', async () => {
  const { act, events, outcome } = setup();
  const params = { subject_uid: 'subject', start_time: '2026-10-11T08:05:27', end_time: '2026-10-11T09:35:27' };
  const actual = await act('add_session', params, { uid: 'captured',
    optimistic: () => events.push('optimistic'), afterAcknowledged: async () => events.push('acknowledged') });
  assert.equal(actual, outcome);
  assert.deepEqual(events, ['mutation', 'optimistic', 'render',
    ['send', 'add_session', params, { uid: 'captured' }], 'acknowledged', ['refresh', { quiet: true }]]);
});

test('a failure before enqueue restores the canonical display and allows a real retry', async () => {
  const { act, events } = setup({ send: async () => { throw Error('storage quota'); } });
  assert.deepEqual(await act('add_session', {}), { ok: false });
  assert.deepEqual(events, ['mutation', 'project', 'render', ['banner', 'storage quota', 'error', 0]]);
});

for (const failedStep of ['project', 'render', 'banner']) {
  test(`a durable offline action stays queued when ${failedStep} fails after saving`, async () => {
    const saved = { ok: false, queued: true, offline: true };
    const { act } = setup({ send: async () => saved, [failedStep]: () => { throw Error('display failed'); } });
    assert.equal(await act('add_session', {}, { uid: 'original' }), saved,
      'session sheets must stay latched after durability, rather than enqueue another identity');
  });
}

test('an acknowledged action remains saved if its completion callback or refresh fails', async () => {
  for (const failCallback of [true, false]) {
    const { act, outcome } = setup({ refresh: async () => { if (!failCallback) throw Error('render failed'); } });
    const options = failCallback ? { afterAcknowledged: async () => { throw Error('cache failed'); } } : {};
    assert.equal(await act('add_session', {}, options), outcome);
  }
});
