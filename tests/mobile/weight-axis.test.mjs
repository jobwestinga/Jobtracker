import test from 'node:test';
import assert from 'node:assert/strict';
import { importWebModule } from './support/import-web.mjs';

const { weightYearTicks, weightTimestamp } = await importWebModule('features/weight/math.js');

test('year lines use January 1 across leap years and inclusive endpoints', () => {
  const ticks = weightYearTicks(weightTimestamp('2023-12-03'), weightTimestamp('2026-01-01'));
  assert.deepEqual(ticks, [2024, 2025, 2026].map((year) => ({ year, x: weightTimestamp(`${year}-01-01`) })));
  assert.equal((ticks[1].x - ticks[0].x) / 86400000, 366);
  assert.deepEqual(weightYearTicks(weightTimestamp('2026-02-01'), weightTimestamp('2026-12-31')), []);
});

test('year lines preserve small ISO years and reject invalid/reversed ranges', () => {
  assert.deepEqual(weightYearTicks(weightTimestamp('0098-12-31'), weightTimestamp('0100-01-01')),
    [{ year: 99, x: weightTimestamp('0099-01-01') }, { year: 100, x: weightTimestamp('0100-01-01') }]);
  assert.deepEqual(weightYearTicks(NaN, 10), []);
  assert.deepEqual(weightYearTicks(10, Infinity), []);
  assert.deepEqual(weightYearTicks(10, 1), []);
});

test('canvas keeps each year gridline while thinning crowded year labels', async () => {
  const { drawWeightChart } = await importWebModule('features/weight/chart.js');
  globalThis.devicePixelRatio = 1;
  const paths = [], labels = [];
  let path;
  const context = {
    scale() {}, fillRect() {}, save() {}, restore() {}, rect() {}, clip() {}, arc() {}, fill() {},
    beginPath() { path = []; }, moveTo(x, y) { path.push([x, y]); }, lineTo(x, y) { path.push([x, y]); },
    stroke() { paths.push({ color: this.strokeStyle, path }); },
    fillText(text, x, y) { labels.push({ text, x, y }); },
    measureText(text) { return { width: String(text).length * 7 }; },
  };
  const canvas = { clientWidth: 320, clientHeight: 270, getContext: () => context };
  const points = [{ x: weightTimestamp('2000-01-01'), y: 80 }, { x: weightTimestamp('2026-01-01'), y: 82 }];
  const { left, right } = drawWeightChart(canvas, points, [], { zero: false, points: true });
  const lines = paths.filter((item) => item.color === '#c6c6c6');
  assert.equal(lines.length, 27);
  assert.equal(lines[0].path[0][0], left);
  assert.equal(lines.at(-1).path[0][0], right);
  const years = labels.filter((label) => /^\d{4}$/.test(label.text));
  assert.ok(years.length >= 2 && years.length < 27);
  for (let i = 1; i < years.length; i++) assert.ok(years[i].x - years[i - 1].x >= 38);
});
