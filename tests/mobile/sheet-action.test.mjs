import test from 'node:test';
import assert from 'node:assert/strict';
import { importWebModule } from './support/import-web.mjs';

const { createSheetAction } = await importWebModule('shared/sheet.js');
function world() {
  const classes = new Set();
  const sheet = { classList: { contains: (value) => classes.has(value), add: (value) => classes.add(value), remove: (value) => classes.delete(value) } };
  globalThis.document = { getElementById: (id) => id === 'sheet' ? sheet : null };
  const content = { isConnected: true, draft: { title: 'Keep this draft', date: '2026-10-11' } };
  return { sheet, content, hidden: () => classes.has('hidden') };
}
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };

test('one sheet rejects double submission and competing controls before awaiting the write', async () => {
  const w = world(), held = deferred(); const writes = [];
  const submit = createSheetAction(w.content, { act: (...args) => { writes.push(args); return held.promise; } });
  const first = submit('add_goal', { name: 'One' }, { uid: 'one' });
  await submit('add_goal', { name: 'One' }, { uid: 'two' });
  await submit('delete_goal', { goal_uid: 'other' });
  assert.equal(writes.length, 1); assert.equal(w.hidden(), true); assert.equal(submit.canSubmit(), false);
  held.resolve({ ok: true }); await first;
  await submit('add_goal', {}, { uid: 'three' }); assert.equal(writes.length, 1);
});

test('an initial storage failure restores the same editable draft and allows one retry', async () => {
  const w = world(); let attempts = 0;
  const submit = createSheetAction(w.content, { act: async () => ++attempts === 1 ? { ok: false } : { ok: true } });
  await submit('add_goal', { name: w.content.draft.title });
  assert.equal(w.hidden(), false); assert.equal(submit.canSubmit(), true);
  assert.deepEqual(w.content.draft, { title: 'Keep this draft', date: '2026-10-11' });
  await submit('add_goal', { name: w.content.draft.title });
  assert.equal(attempts, 2); assert.equal(w.hidden(), true);
});

test('durable offline or acknowledgement-storage outcomes never unlock a fresh save', async () => {
  for (const offline of [true, false]) {
    const w = world(); let writes = 0;
    const submit = createSheetAction(w.content, { act: async () => { writes++; return { ok: false, queued: true, offline }; } });
    await submit('add_template', {}, { uid: 'original' });
    await submit('add_template', {}, { uid: 'duplicate' });
    assert.equal(w.hidden(), true); assert.equal(submit.canSubmit(), false); assert.equal(writes, 1);
  }
});

test('a late failed write cannot reopen over a newly opened sheet', async () => {
  const w = world(), held = deferred();
  const submit = createSheetAction(w.content, { act: () => held.promise });
  const writing = submit('update_goal', { goal_uid: 'old' });
  w.content.isConnected = false; w.sheet.classList.remove('hidden');
  held.resolve({ ok: false }); await writing;
  assert.equal(w.hidden(), false); assert.equal(submit.canSubmit(), false);
});

test('unexpected pre-durability exceptions report failure and preserve retry access', async () => {
  const w = world(); const messages = [];
  const submit = createSheetAction(w.content, { act: async () => { throw Error('storage unavailable'); }, onError: (error) => messages.push(error.message) });
  assert.deepEqual(await submit('set_setting', {}), { ok: false });
  assert.deepEqual(messages, ['storage unavailable']); assert.equal(w.hidden(), false); assert.equal(submit.canSubmit(), true);
});

test('a dismissed or disconnected sheet cannot enqueue an operation', async () => {
  const w = world(); const submit = createSheetAction(w.content, { act: () => assert.fail('must not enqueue') });
  w.sheet.classList.add('hidden'); await submit('add_goal', {});
  w.sheet.classList.remove('hidden'); w.content.isConnected = false; await submit('add_goal', {});
});
