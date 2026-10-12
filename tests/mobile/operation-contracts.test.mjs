import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { importWebModule } from './support/import-web.mjs';
const fixture = JSON.parse(await readFile(new URL('../contracts/operation_outcomes.json', import.meta.url), 'utf8'));
assert.equal(fixture.schema_version, 1);
const { projectWeightOperation } = await importWebModule('features/weight/projection.js');

for (const scenario of fixture.cases) test(`shared outbox/server contract: ${scenario.id}`, async () => {
  const values = new Map([['jobtracker.token', 'contract-account']]);
  globalThis.localStorage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
  Object.defineProperty(globalThis, 'navigator', { value: {}, configurable: true });
  let requestIndex = 0;
  globalThis.fetch = async (path, request) => {
    assert.equal(path, '/ops'); assert.equal(request.method, 'POST');
    assert.equal(request.cache, 'no-store'); assert.equal(request.redirect, 'error');
    const expected = scenario.requests[requestIndex];
    assert.deepEqual(JSON.parse(request.body), expected);
    assert.deepEqual(JSON.parse(values.get('jobtracker.outbox')), expected.ops,
      'the exact transmitted identities must already be durable');
    const response = scenario.responses[requestIndex++];
    if (scenario.id === 'lost-ack-replay' && requestIndex === 1) throw Error('accepted response lost before acknowledgement');
    return new Response(JSON.stringify({ seq: 0, detail: 'invalid parameters', ...response.body }), { status: response.status });
  };
  const client = await importWebModule('api.js');
  let lastOutcome;
  for (let index = 0; index < scenario.requests.length; index++) {
    // Prescribed IDs allow the exact same fixtures to exercise receipt replay
    // in Python and acknowledgement handling in the real browser outbox.
    const pending = client.pendingOperations();
    for (const entry of scenario.requests[index].ops) {
      const previous = pending.find((queued) => queued.op_id === entry.op_id);
      if (previous) assert.deepEqual(previous, entry, 'replay keeps immutable params and identity');
      else pending.push(structuredClone(entry));
    }
    localStorage.setItem('jobtracker.outbox', JSON.stringify(pending));
    lastOutcome = await client.flush();
    if (scenario.id === 'lost-ack-replay' && index === 0) {
      assert.equal(lastOutcome.offline, true);
      assert.deepEqual(client.pendingOperations(), scenario.requests[0].ops, 'missing acknowledgement cannot discard accepted work');
    } else {
      assert.deepEqual(lastOutcome.applied, scenario.responses[index].body.applied);
      assert.equal(lastOutcome.ok, scenario.responses[index].status < 400);
      if (scenario.responses[index].status >= 400) {
        assert.equal(lastOutcome.failed_op_id, scenario.responses[index].body.failed_op_id);
      }
    }
  }
  assert.equal(requestIndex, scenario.requests.length);
  assert.deepEqual(client.pendingOperations().map((entry) => entry.op_id), scenario.queue_remaining);
  if (scenario.id === 'milestone-refusal') {
    assert.equal(lastOutcome.applied[0].result.completed, false);
    assert.equal(client.pendingCount(), 0, 'an acknowledged business refusal is not an unsent operation');
  }
  if (scenario.id === 'deleted-target-skip') assert.equal(lastOutcome.applied[0].result.skipped, true);
  if (scenario.id === 'daily-weight-canonical-identity') {
    const preview = scenario.requests[0].ops.reduce(projectWeightOperation, []);
    assert.equal(preview.length, 1, 'same-day queued manual saves project one weighing');
    const authoritative = lastOutcome.applied.at(-1).result.measurement;
    assert.equal(preview[0].uid, authoritative.uid);
    assert.equal(preview[0].weight_kg, authoritative.weight_kg);
    assert.equal(preview[0].measured_at, authoritative.measured_at);
  }
});
