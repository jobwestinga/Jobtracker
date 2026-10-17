import test from 'node:test';
import assert from 'node:assert/strict';
import { importWebModule } from './support/import-web.mjs';

const { orderSubjects } = await importWebModule('shared/order-controls.js');

test('subjects follow the Mac order: sort_order, then newest first', () => {
  // Snapshots arrive in database order, which put an old subject first on the phone.
  const snapshot = [
    { uid: 'website', sort_order: 0, created_at: '2026-08-01 10:00:00' },
    { uid: 'course', sort_order: 2, created_at: '2026-09-01 10:00:00' },
    { uid: 'maps', sort_order: 0, created_at: '2026-09-20 10:00:00' },
    { uid: 'notes', sort_order: 1, created_at: '2026-08-15 10:00:00' },
    { uid: 'unsynced', sort_order: 0 },
  ];
  assert.deepEqual(orderSubjects(snapshot).map((row) => row.uid), ['maps', 'website', 'unsynced', 'notes', 'course']);
  assert.deepEqual(snapshot.map((row) => row.uid), ['website', 'course', 'maps', 'notes', 'unsynced'], 'the snapshot itself is not reordered');
  assert.deepEqual(orderSubjects(undefined), []);
});
