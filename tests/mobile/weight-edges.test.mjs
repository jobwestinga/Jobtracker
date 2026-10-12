import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { parseWeight, weightTimestamp, weightDayOffset, pointsFor, selectMeasurements } from '../../web/features/weight/math.js';
import { findWeightMeasurement, projectWeightOperation, withWeightAliases } from '../../web/features/weight/projection.js';
import { splitWeightTiles, weightTileAnchor } from '../../web/features/weight/picker.js';

const row = (uid, day, weight = 80, source = 'manual') => ({ uid, measured_at: `${day}T00:00:00`, weight_kg: weight, source });
const save = (uid, day, weight) => ({ op: 'upsert_weight', params: { uid, measured_at: day, weight_kg: weight } });
const edit = (uid, day, weight) => ({ op: 'upsert_weight', params: { weight_uid: uid, measured_at: day, weight_kg: weight } });
const remove = (uid) => ({ op: 'delete_weight', params: { weight_uid: uid } });

test('same-day queued creates retain aliases for later edits and deletion after a canonical GET', () => {
  const canonical = [row('canonical', '2026-10-10')];
  const pending = [save('offline-a', '2026-10-10', 81), save('offline-b', '2026-10-10', 82), edit('offline-a', '2026-10-10', 83)];
  const before = structuredClone(canonical);
  const projected = pending.reduce(projectWeightOperation, canonical);
  assert.equal(projected.length, 1);
  assert.equal(projected[0].uid, 'canonical');
  assert.equal(projected[0].weight_kg, 83);
  assert.equal(findWeightMeasurement(projected, 'offline-b'), projected[0]);
  assert.deepEqual(projectWeightOperation(projected, remove('offline-a')), []);
  assert.deepEqual(canonical, before, 'projection never mutates the server mirror');
});

test('partial acknowledgement aliases apply the remaining edit/delete without its removed create', () => {
  const aliases = [{ alias_uid: 'acknowledged-create', weight_uid: 'canonical' }];
  const rows = withWeightAliases([row('canonical', '2026-10-10')], aliases);
  assert.equal(projectWeightOperation(rows, edit('acknowledged-create', '2026-10-10', 82))[0].weight_kg, 82);
  assert.deepEqual(projectWeightOperation(rows, remove('acknowledged-create')), []);
  const restored = JSON.parse(JSON.stringify({ rows, aliases }));
  assert.deepEqual(projectWeightOperation(withWeightAliases(restored.rows, restored.aliases), remove('acknowledged-create')), []);
});

test('manual date correction keeps destination canonical UID and every source/destination alias', () => {
  const initial = [row('source', '2026-10-09'), row('destination', '2026-10-10', 81)];
  const pending = [save('source-alias', '2026-10-09', 82), save('destination-alias', '2026-10-10', 83), edit('source-alias', '2026-10-10', 84)];
  const projected = pending.reduce(projectWeightOperation, initial);
  assert.equal(projected.length, 1);
  assert.equal(projected[0].uid, 'destination');
  for (const uid of ['source', 'source-alias', 'destination', 'destination-alias']) {
    assert.equal(findWeightMeasurement(projected, uid), projected[0]);
    assert.equal(projectWeightOperation(projected, edit(uid, '2026-10-10', 85))[0].weight_kg, 85);
    assert.deepEqual(projectWeightOperation(projected, remove(uid)), []);
  }
});

test('scale readings and missing aliases never coalesce or infer identity from calendar date', () => {
  const rows = [row('removed-replacement', '2026-10-10'), row('scale', '2026-10-09', 82, 'scale')];
  assert.deepEqual(projectWeightOperation(rows, edit('removed', '2026-10-10', 83)), rows);
  const moved = projectWeightOperation(rows, edit('scale', '2026-10-10', 84));
  assert.equal(moved.length, 2);
  assert.equal(moved.find((item) => item.uid === 'scale').source, 'scale');
  assert.equal(moved.find((item) => item.uid === 'removed-replacement').weight_kg, 80);
  assert.doesNotThrow(() => withWeightAliases(rows, [null, {}, { alias_uid: false, weight_uid: 'scale' }]));
});

test('measurement selection rejects invalid calendar dates, offsets and invalid numeric types/ranges', () => {
  const valid = row('valid', '2026-02-28', 1e-7);
  const corrupt = [null, {}, row('bad-day', '2026-02-30'), row('bool', '2026-10-10', true),
    row('array', '2026-10-10', [80]), row('large', '2026-10-10', 1001),
    { ...valid, measured_at: '2026-10-10T12:00:00+02:00' }, { ...valid, measured_at: '0000-01-01' }];
  assert.deepEqual(selectMeasurements([...corrupt, valid], { historic: true }), [valid]);
  assert.equal(pointsFor([...corrupt, valid]).length, 1);
  assert.equal(parseWeight(1e-7), 1e-7);
  assert.deepEqual(splitWeightTiles(1e-7), { whole: 0, decimal: 0 });
  assert.equal(weightTileAnchor([row('comma', '2026-10-10', '80,5')]), 81);
});

test('calendar coordinates preserve ISO year limits and fractional seconds', () => {
  assert.equal(new Date(weightTimestamp('0001-01-01')).toISOString(), '0001-01-01T00:00:00.000Z');
  assert.equal(new Date(weightTimestamp('9999-12-31')).toISOString(), '9999-12-31T00:00:00.000Z');
  assert.ok(Math.abs(weightTimestamp('2026-10-10T07:35:22.123456') - weightTimestamp('2026-10-10T07:35:22') - 123.456) < 0.001);
  assert.equal(weightDayOffset('2026-03-30', -30), '2026-02-28');
  assert.equal(weightDayOffset('0001-01-01', -30), '');
  assert.equal(weightDayOffset('9999-12-31', 1), '');
  assert.equal(weightTimestamp('2026-10-10T25:00:00'), null);
});

test('naive plot spacing and calendar windows do not normalize nonexistent DST clock times', () => {
  const moduleUrl = new URL('../../web/features/weight/math.js', import.meta.url).href;
  const script = `import { pointsFor, rollingMean } from ${JSON.stringify(moduleUrl)};
    const rows = ['2026-03-29T01:30:00','2026-03-29T02:30:00','2026-03-29T03:30:00'].map((measured_at,index) =>
      ({measured_at,weight_kg:[80,82,84][index],source:'scale'}));
    const points=pointsFor(rows);
    const dates=['2026-03-22T23:59:59','2026-03-23T00:01:00','2026-03-28T12:00:00','2026-03-29T20:00:00'];
    const rolling=rollingMean(pointsFor(dates.map((measured_at,index)=>({measured_at,weight_kg:[100,80,82,84][index]}))));
    process.stdout.write(JSON.stringify({gaps:points.slice(1).map((point,index)=>point.x-points[index].x),mean:rolling.at(-1).y}));`;
  const actual = JSON.parse(execFileSync(process.execPath, ['--input-type=module', '-e', script], {
    encoding: 'utf8', env: { ...process.env, TZ: 'Europe/Amsterdam' },
  }));
  assert.deepEqual(actual, { gaps: [3600000, 3600000], mean: 82 });
});
