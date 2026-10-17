import test from 'node:test';
import assert from 'node:assert/strict';
import { importWebModule } from './support/import-web.mjs';

const { trendFor, trendBetween } = await importWebModule('features/weight/math.js');
const DAY = 86400000;

// Two years of daily weighings: a slow wave plus deterministic day-to-day noise.
const points = Array.from({ length: 730 }, (_, index) => ({
  x: Date.UTC(2024, 9, 1) + index * DAY,
  y: 80 + 3 * Math.sin(index / 60) + ((index * 7919) % 13 - 6) / 10,
  row: { source: 'scale' },
}));
const residual = (trend, sample) => sample.reduce((sum, point) => {
  const fitted = trend.find((value) => value.x === point.x);
  return sum + Math.abs(fitted.y - point.y);
}, 0) / sample.length;

test('a 90-day view keeps the whole-history smoothing instead of tracing every weighing', () => {
  const last90 = points.slice(-90);
  // Fitting only the visible 90 points gives the 4% span about three neighbours,
  // so the old trend passed through nearly every measurement.
  const windowFit = trendFor(last90, 'loess', 0.04);
  const historyFit = trendBetween(trendFor(points, 'loess', 0.04), last90[0].x, last90.at(-1).x);
  assert.ok(residual(windowFit, last90) < 0.12, 'the window-only fit nearly interpolates');
  assert.ok(residual(historyFit, last90) > 0.2, 'the whole-history fit leaves visible scatter around a smooth line');
  const steps = historyFit.slice(1).map((point, index) => Math.abs(point.y - historyFit[index].y));
  assert.ok(Math.max(...steps) < 0.15, 'day-to-day trend steps stay small');
});

test('the visible trend stretch includes one point beyond each edge', () => {
  const trend = [1, 2, 3, 4, 5].map((day) => ({ x: day * DAY, y: day }));
  assert.deepEqual(trendBetween(trend, 2.5 * DAY, 3.5 * DAY).map((point) => point.y), [2, 3, 4]);
  assert.deepEqual(trendBetween(trend, 1 * DAY, 5 * DAY).map((point) => point.y), [1, 2, 3, 4, 5]);
  assert.deepEqual(trendBetween(trend, 6 * DAY, 7 * DAY), []);
  assert.deepEqual(trendBetween([], 0, 1), []);
  assert.deepEqual(trendBetween(trend, NaN, 1), []);
});
