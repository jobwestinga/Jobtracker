import test from 'node:test';
import assert from 'node:assert/strict';
import { importWebModule } from './support/import-web.mjs';

const { endingNowRange, manualDurationSeconds, QUICK_DURATIONS, CUSTOM_DURATION_CHUNKS } = await importWebModule('features/sessions/duration.js');
const naiveDate = (year, month, day, hour, minute, second = 0) => ({
  getFullYear: () => year, getMonth: () => month - 1, getDate: () => day,
  getHours: () => hour, getMinutes: () => minute, getSeconds: () => second,
});
const duration = ({ start_time, end_time }) => (Date.parse(`${end_time}Z`) - Date.parse(`${start_time}Z`)) / 60000;

test('quick presets include 90 minutes and custom chunks support the requested 170-minute combination', () => {
  assert.ok(QUICK_DURATIONS.some(([, minutes]) => minutes === 90));
  assert.deepEqual(CUSTOM_DURATION_CHUNKS.map(([, minutes]) => minutes), [240, 120, 60, 30, 15, 10, 5]);
  const chosen = [120, 30, 15, 5];
  assert.equal(duration(endingNowRange(chosen.reduce((sum, minutes) => sum + minutes), naiveDate(2026, 10, 10, 9, 35, 27))), 170);
});

test('ending-now captures seconds and rolls across calendar, month, leap and year boundaries', () => {
  const cases = [
    [90, naiveDate(2026, 10, 10, 9, 35, 27), '2026-10-10T08:05:27', '2026-10-10T09:35:27'],
    [170, naiveDate(2026, 1, 1, 0, 30), '2025-12-31T21:40:00', '2026-01-01T00:30:00'],
    [90, naiveDate(2024, 3, 1, 0, 30), '2024-02-29T23:00:00', '2024-03-01T00:30:00'],
  ];
  for (const [minutes, now, start_time, end_time] of cases) {
    assert.deepEqual(endingNowRange(minutes, now), { start_time, end_time });
  }
});

test('spring and fall DST dates preserve the server naive wall-clock duration without timezone normalization', () => {
  for (const [month, day] of [[3, 29], [10, 25]]) {
    const end = naiveDate(2026, month, day, 3, 30);
    assert.equal(endingNowRange(90, end).start_time.slice(11), '02:00:00');
    assert.equal(endingNowRange(170, end).start_time.slice(11), '00:40:00');
    for (const [, minutes] of [...QUICK_DURATIONS, ...CUSTOM_DURATION_CHUNKS]) {
      const range = endingNowRange(minutes, end);
      assert.equal(duration(range), minutes);
      assert.equal(range.end_time.endsWith('Z'), false);
    }
  }
});

test('an empty or invalid custom duration cannot create a session', () => {
  for (const minutes of [0, -5, 1.5, NaN, Infinity, '90']) {
    assert.throws(() => endingNowRange(minutes, naiveDate(2026, 10, 10, 9, 30)), RangeError);
  }
});

test('manual input validity uses exact naive endpoints at the 30-second boundary', () => {
  const start = '2026-10-10T09:00:00.900000';
  assert.equal(manualDurationSeconds(start, '2026-10-10T09:00:30.899999'), 29);
  assert.equal(manualDurationSeconds(start, '2026-10-10T09:00:30.900000'), 30);
  assert.equal(manualDurationSeconds(start, '2026-10-10T09:00:30.900001'), 30);
  assert.equal(manualDurationSeconds(start, start), 0);
  assert.equal(manualDurationSeconds('2026-03-29T00:40:00', '2026-03-29T03:30:00'), 170 * 60);
  assert.equal(manualDurationSeconds('2026-10-10T10:00:00', '2026-10-10T09:00:00'), -3600);
  assert.ok(Number.isNaN(manualDurationSeconds('invalid', start)));
});
