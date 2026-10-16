import test from 'node:test';
import assert from 'node:assert/strict';
import { importWebModule } from './support/import-web.mjs';

test('explicit routes select products; viewport and shared last tab never choose a product', async () => {
  const { profileForPath, PROFILES } = await importWebModule('shared/profile.js');
  assert.equal(profileForPath('/'), 'full');
  assert.equal(profileForPath('/index.html'), 'full');
  for (const name of ['weight', 'jobtracker']) {
    const profile = PROFILES[name];
    assert.equal(profileForPath(profile.path), name);
    assert.equal(profileForPath(`${profile.path}index.html`), name);
    assert.equal(profileForPath(`/unsupported${profile.path}`), 'full');
  }
  assert.equal(PROFILES.weight.defaultView, 'weight');
  assert.equal(PROFILES.jobtracker.defaultView, 'today');
  assert.deepEqual(PROFILES.weight.views, ['weight']);
  assert.ok(!PROFILES.jobtracker.views.includes('weight'));
});

test('Weight retains every legacy key; JobTracker isolates credentials, queues, locks and preferences', async () => {
  const previous = globalThis.location;
  try {
    globalThis.location = { pathname: '/' };
    const legacy = (await importWebModule('shared/storage.js')).STORAGE_KEYS;
    globalThis.location = { pathname: '/phone/weight/' };
    const weight = (await importWebModule('shared/storage.js')).STORAGE_KEYS;
    globalThis.location = { pathname: '/phone/jobtracker/' };
    const tracker = (await importWebModule('shared/storage.js')).STORAGE_KEYS;
    assert.deepEqual(weight, legacy);
    assert.equal(weight.outbox, 'jobtracker.outbox');
    assert.equal(weight.token, 'jobtracker.token');
    for (const key of Object.keys(legacy)) assert.notEqual(tracker[key], weight[key], key);
    assert.equal(tracker.outbox, 'jobtracker.phone.jobtracker.outbox');
    assert.equal(tracker.token, 'jobtracker.phone.jobtracker.token');
  } finally {
    if (previous === undefined) delete globalThis.location;
    else globalThis.location = previous;
  }
});
