import test from 'node:test';
import assert from 'node:assert/strict';
import { trendValueAt, trendTangent, tangentEnergy, tangentLabel, KCAL_PER_KG_LOSS, KCAL_PER_KG_GAIN } from '../../web/features/weight/math.js';

const DAY = 86400000;
const line = (start, perDay, days) => Array.from({ length: days }, (_, i) => ({ x: i * DAY, y: start + perDay * i }));

test('trend values interpolate between samples and stay inside the trend', () => {
  const trend = line(80, -0.1, 10);
  assert.equal(trendValueAt(trend, 2.5 * DAY), 80 - 0.25);
  assert.equal(trendValueAt(trend, -1), null);
  assert.equal(trendValueAt(trend, 10 * DAY), null);
  assert.equal(trendValueAt([], 0), null);
});

test('a straight trend has its own slope everywhere, including the one-sided ends', () => {
  const trend = line(80, -0.1, 30);
  for (const day of [0, 1, 15, 29]) {
    const tangent = trendTangent(trend, day * DAY);
    assert.ok(Math.abs(tangent.perDay + 0.1) < 1e-9, `day ${day}`);
    assert.ok(Math.abs(tangent.y - (80 - 0.1 * day)) < 1e-9);
  }
});

test('the tangent of a curve follows its local derivative', () => {
  // y = 80 + 0.001 x² (x in days): derivative 0.002 x.
  const trend = Array.from({ length: 121 }, (_, i) => ({ x: i * DAY, y: 80 + 0.001 * i * i }));
  const tangent = trendTangent(trend, 60 * DAY, DAY);
  assert.ok(Math.abs(tangent.perDay - 0.12) < 1e-9);
});

test('losses count as fat and gains as a trained mix', () => {
  assert.equal(tangentEnergy(-0.1), -0.1 * KCAL_PER_KG_LOSS);
  assert.equal(tangentEnergy(0.05), 0.05 * KCAL_PER_KG_GAIN);
  assert.equal(tangentEnergy(0), 0);
  assert.equal(tangentEnergy(NaN), 0);
  assert.equal(KCAL_PER_KG_LOSS, 7700);
});

test('labels show a signed daily kcal figure and weekly change', () => {
  assert.equal(tangentLabel({ perDay: -0.06, kcal: -462 }), '−462 kcal/day · −0.42 kg/wk');
  assert.equal(tangentLabel({ perDay: 0.05, kcal: 280 }), '+280 kcal/day · +0.35 kg/wk');
  assert.equal(tangentLabel({ perDay: 0, kcal: 0.2 }), '0 kcal/day · 0.00 kg/wk');
});
