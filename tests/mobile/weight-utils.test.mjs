import test from "node:test";
import assert from "node:assert/strict";
import { performance } from "node:perf_hooks";
import { parseWeight, selectMeasurements, pointsFor, loess, pchip, trendFor, rollingMean } from "../../web/weight-utils.js";
const day = 86400000, epoch = new Date("2026-01-01T12:00:00").getTime();

test("weight input accepts decimal commas and rejects missing/nonfinite/negative values", () => {
  assert.equal(parseWeight("80,25"), 80.25);
  for (const value of ["", null, undefined, "NaN", "Infinity", "-1", "0", "1001", "80kg", "1,2,3"]) assert.equal(parseWeight(value), null);
});
test("LOESS preserves constant and linear trends, including endpoints", () => {
  for (const slope of [0, 0.3, -0.2]) {
    const points = Array.from({length: 80}, (_, i) => ({x: epoch + i * day, y: 80 + slope * i}));
    const fitted = loess(points, 0.15);
    for (let i = 0; i < points.length; i++) assert.ok(Math.abs(fitted[i].y - points[i].y) < 1e-9);
  }
});
test("zero/one/two/duplicate-time data remain finite", () => {
  assert.deepEqual(loess([]), []);
  assert.deepEqual(loess([{x: epoch, y: 80}]), [{x: epoch, y: 80}]);
  assert.deepEqual(loess([{x: epoch, y: 80}, {x: epoch, y: 82}]), [{x: epoch, y: 81}]);
  assert.equal(pchip([{x: epoch, y: 80}])[0].y, 80);
});
test("PCHIP stays monotone and within original extrema", () => {
  const points = [{x:epoch,y:60},{x:epoch+100*day,y:65},{x:epoch+400*day,y:80}];
  const fitted = pchip(points);
  assert.equal(fitted[0].y, 60); assert.equal(fitted.at(-1).y, 80);
  assert.ok(fitted.every((p,i) => p.y >= 60 && p.y <= 80 && (!i || p.y >= fitted[i-1].y)));
});
test("historic interpolation leaves dense original trend unchanged", () => {
  const dense = Array.from({length:100},(_,i)=>({x:epoch+i*day,y:80+Math.sin(i/4),row:{source:"scale"}}));
  const history = [{x:epoch-1000*day,y:60,row:{source:"historic"}}];
  const regular = trendFor(dense), combined = trendFor([...history,...dense]);
  assert.deepEqual(combined.slice(-regular.length), regular);
  assert.ok(combined.every((p)=>Number.isFinite(p.y)));
});
test("rolling average uses elapsed days, never fills missing days with zero", () => {
  const points=[{x:epoch,y:80},{x:epoch+day,y:82},{x:epoch+30*day,y:90}];
  assert.deepEqual(rollingMean(points).map(p=>p.y),[80,81,90]);
});
test("calendar custom boundaries include the entire last day and omit historic by default", () => {
  const rows = [{measured_at:"2026-10-10T23:59:59",weight_kg:80,source:"manual"},{measured_at:"2010-01-01T00:00:00",weight_kg:60,source:"historic"}];
  assert.equal(selectMeasurements(rows,{start:"2026-10-10",end:"2026-10-10"}).length,1);
  assert.equal(selectMeasurements(rows).length,1);
  assert.equal(pointsFor(selectMeasurements(rows,{historic:true})).length,2);
});
test("full-history LOESS remains cheap on a phone-sized dataset", () => {
  const points=Array.from({length:3000},(_,i)=>({x:epoch+i*day,y:80+Math.sin(i/10)}));
  const start=performance.now(); const fitted=loess(points);
  assert.equal(fitted.length,3000); assert.ok(fitted.every(p=>Number.isFinite(p.y)));
  assert.ok(performance.now()-start<1000);
});
