import test from 'node:test';
import assert from 'node:assert/strict';
import { importWebModule } from './support/import-web.mjs';
const { projectPending, applyChanges } = await importWebModule('data/projection.js');
const snapshot = { subjects: [{ uid: 's', name: 'Subject' }], goals: [{ uid: 'g', name: 'Goal', is_focused: 0 }], milestones: [], sessions: [], settings: { day_start_time: '03:00' } };

test('offline start and stop overlay keeps the original time and precise session identity after reload', () => {
  const started = { op: 'start_subject', params: { uid: 'session', subject_uid: 's', start_time: '2026-10-10T09:00:00' } };
  const projected = projectPending(snapshot, null, [started]);
  assert.equal(projected.active.uid, 'session');
  assert.equal(projected.active.timer_origin, new Date(started.params.start_time).getTime());
  const stopped = { op: 'stop_active_subject', params: { session_uid: 'session', end_time: '2026-10-10T10:15:00' } };
  assert.equal(projectPending(snapshot, null, [started, stopped]).active, null);
});

test('a stale queued stop or switch cannot replace a newer timer on the display', () => {
  const active = { uid: 'newer', subject_uid: 's', start_time: '2026-10-10T11:00:00' };
  const ops = [
    { op: 'stop_active_subject', params: { session_uid: 'older' } },
    { op: 'switch_subject', params: { source_session_uid: 'older', uid: 'next', subject_uid: 't', start_time: '2026-10-10T11:05:00' } },
  ];
  assert.equal(projectPending(snapshot, active, ops).active.uid, 'newer');
});

test('queued subjects, edits, focus toggles and deletes survive reload without modifying the authoritative mirror', () => {
  const projected = projectPending(snapshot, null, [
    { op: 'add_subject', params: { uid: 'local', name: 'New', color: '#123456' } },
    { op: 'archive_subject', params: { subject_uid: 'local' } },
    { op: 'toggle_goal_focused', params: { goal_uid: 'g' } },
    { op: 'toggle_goal_focused', params: { goal_uid: 'g' } },
    { op: 'update_goal', params: { goal_uid: 'g', name: 'Changed', notes: '' } },
  ]);
  assert.equal(projected.snapshot.subjects.find((s) => s.uid === 'local').is_archived, 1);
  assert.equal(projected.snapshot.goals[0].is_focused, 0);
  assert.equal(projected.snapshot.goals[0].name, 'Changed');
  assert.equal(snapshot.goals[0].name, 'Goal');
  assert.equal(snapshot.subjects.length, 1);
});

test('incremental upserts, deletions and shared settings update a mirror without disturbing untouched lists', () => {
  const updated = applyChanges(snapshot, [
    { table: 'subjects', uid: 's', op: 'delete' },
    { table: 'goals', uid: 'g', op: 'upsert', row: { uid: 'g', name: 'Renamed' } },
    { table: 'weights', uid: 'weight', op: 'upsert', row: { uid: 'weight', weight_kg: 80 } },
  ], { day_start_time: '04:00' });
  assert.deepEqual(updated.subjects, []);
  assert.equal(updated.goals[0].name, 'Renamed');
  assert.equal(updated.settings.day_start_time, '04:00');
  assert.equal(updated.sessions, snapshot.sessions);
  assert.equal(updated.weights, undefined);
  assert.equal(snapshot.subjects.length, 1);
});
