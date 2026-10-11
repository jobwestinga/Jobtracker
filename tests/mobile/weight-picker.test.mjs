import test from 'node:test';
import assert from 'node:assert/strict';
import { importWebModule } from './support/import-web.mjs';
const { wholeWeightTiles, weightTileAnchor, splitWeightTiles, selectedTileWeight, dailyWeightMoment, manualDateCollision } = await importWebModule('features/weight/picker.js');

test('seven whole tiles center on rounded chronologically latest reading', () => {
  const rows = [
    { uid: 'new', measured_at: '2026-10-10T00:00:00', weight_kg: 83.6, source: 'manual' },
    { uid: 'old', measured_at: '2026-10-09T23:59:00', weight_kg: 79.5, source: 'scale' },
    { uid: 'historic', measured_at: '2016-01-01T12:00:00', weight_kg: 92, source: 'historic' },
  ];
  assert.equal(weightTileAnchor(rows), 84);
  assert.deepEqual(wholeWeightTiles(weightTileAnchor(rows)), [81, 82, 83, 84, 85, 86, 87]);
  assert.deepEqual(rows.map((row)=>row.uid), ['new', 'old', 'historic'], 'anchor derivation never sorts the authoritative rows in place');
});

test('empty or malformed history supplies a usable seven-value fallback', () => {
  assert.equal(weightTileAnchor([]), 80);
  assert.equal(weightTileAnchor(null), 80);
  assert.equal(weightTileAnchor([{ measured_at: 'bad', weight_kg: 99 }, null]), 80);
  assert.deepEqual(wholeWeightTiles(NaN), [77, 78, 79, 80, 81, 82, 83]);
  assert.equal(weightTileAnchor([], 72.7), 73);
});

test('both parts are mandatory, .0 is explicit, and decimal arithmetic stays exact', () => {
  assert.equal(selectedTileWeight(81, null), null);
  assert.equal(selectedTileWeight(null, 7), null);
  assert.equal(selectedTileWeight(undefined, 0), null);
  assert.equal(selectedTileWeight(81, 0), 81);
  assert.equal(selectedTileWeight(81, 7), 81.7);
  assert.equal(selectedTileWeight(81.5, 2), null);
  assert.equal(selectedTileWeight(81, 10), null);
  assert.equal(selectedTileWeight(81, -1), null);
  assert.equal(selectedTileWeight(0, 0), null);
  assert.equal(selectedTileWeight(0, 1), 0.1);
  assert.equal(selectedTileWeight(1000, 0), 1000);
  assert.equal(selectedTileWeight(1000, 1), null);
});

test('editing preselection carries tenths and handles hundredths without malformed decimal tiles', () => {
  assert.deepEqual(splitWeightTiles(81.4), { whole: 81, decimal: 4 });
  assert.deepEqual(splitWeightTiles(79.55), { whole: 79, decimal: 6 });
  assert.deepEqual(splitWeightTiles(81.96), { whole: 82, decimal: 0 });
  assert.deepEqual(splitWeightTiles(999.96), { whole: 1000, decimal: 0 });
  assert.equal(splitWeightTiles(NaN), null);
});

test('new daily saves use the calendar date, imported edits preserve the original clock', () => {
  assert.equal(dailyWeightMoment('2026-10-10'), '2026-10-10');
  assert.equal(dailyWeightMoment('2026-10-10', { measured_at: '2026-10-09T09:15:22.123456' }), '2026-10-10T09:15:22.123456');
  assert.equal(dailyWeightMoment('2026-10-10', { measured_at: '2026-10-09' }), '2026-10-10T00:00:00');
});

test('known manual date collisions are prevented without collapsing scale and historic rows', () => {
  const rows = [
    { uid: 'a', measured_at: '2026-10-09T00:00:00', source: 'manual' },
    { uid: 'b', measured_at: '2026-10-10T00:00:00', source: 'manual' },
  ];
  assert.equal(manualDateCollision(rows, rows[0], '2026-10-10'), true);
  assert.equal(manualDateCollision(rows, rows[0], '2026-10-09'), false);
  assert.equal(manualDateCollision(rows, { ...rows[0], source: 'scale' }, '2026-10-10'), false);
  assert.equal(manualDateCollision(rows, { ...rows[0], source: 'historic' }, '2026-10-10'), false);
  assert.equal(manualDateCollision(rows, null, '2026-10-10'), false);
});
