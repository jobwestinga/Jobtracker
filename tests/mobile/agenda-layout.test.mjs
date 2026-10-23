import test from "node:test";
import assert from "node:assert/strict";
import { layoutAgendaDay } from "../../web/features/graphs/agenda-time.js";

const block = (uid, start, end) => ({ uid, start_h: start, end_h: end });
const tolerance = 30 / 3600;

test("adjacent sessions join only within the tolerance and keep their identities", () => {
  const placed = layoutAgendaDay([block("a", 9, 10), block("b", 10 + 20 / 3600, 11), block("c", 11.5, 12)], tolerance);
  assert.deepEqual(placed.map((item) => item.session.uid), ["a", "b", "c"]);
  assert.deepEqual(placed.map((item) => [item.joinsAbove, item.joinsBelow]), [[false, true], [true, false], [false, false]]);
  assert.ok(placed.every((item) => item.lanes === 1));
});

test("overlaps arrange vertically without changing any recorded session", () => {
  const placed = layoutAgendaDay([block("long", 9, 12), block("inside", 10, 11), block("after", 12, 13)], tolerance);
  const byUid = Object.fromEntries(placed.map((item) => [item.session.uid, item]));
  assert.equal(byUid.long.lanes, 1);
  assert.equal(byUid.inside.lanes, 1);
  assert.equal(byUid.inside.startH, 12);
  assert.equal(byUid.inside.endH, 13);
  assert.equal(byUid.after.startH, 13);
  assert.ok(byUid.inside.arranged);
  assert.equal(byUid.inside.session.start_h, 10, "no timestamp is shifted");
  assert.equal(byUid.after.lanes, 1, "a later cluster returns to one lane");
});

test("a gap larger than the tolerance stays visible", () => {
  const placed = layoutAgendaDay([block("a", 9, 10), block("b", 10 + 31 / 60, 11)], tolerance);
  assert.ok(!placed[0].joinsBelow && !placed[1].joinsAbove);
});

test("more than eight overlapping sessions retain distinct targets", () => {
  const sessions = Array.from({ length: 12 }, (_, index) => block(`s${index}`, 9, 10));
  const placed = layoutAgendaDay(sessions, tolerance);
  assert.ok(placed.every((item) => item.lanes === 1));
  assert.deepEqual(new Set(placed.map((item) => item.session.uid)), new Set(sessions.map((item) => item.uid)));
  placed.slice(1).forEach((item, i) => assert.ok(item.startH >= placed[i].endH - 1e-9));
  assert.ok(placed.every((item) => item.session.start_h === 9 && item.session.end_h === 10));
});

test("overflow pulls display blocks earlier, preserving their duration and source identity", () => {
  const sessions = [block("a", 22, 23), block("b", 22, 23)];
  const placed = layoutAgendaDay(sessions, tolerance, { hourStart: 6, hourEnd: 23 });
  assert.deepEqual(placed.map((item) => [item.startH, item.endH]), [[21, 22], [22, 23]]);
  assert.ok(placed.every((item, i) => item.session === sessions[i]));
});

test("a full window keeps every session distinct and recorded times intact", () => {
  const sessions = Array.from({ length: 50 }, (_, i) => Object.freeze(block(`s${i}`, 22, 23)));
  const placed = layoutAgendaDay(sessions, tolerance, { hourStart: 6, hourEnd: 23 });
  assert.equal(placed.length, sessions.length);
  assert.ok(placed[0].startH >= 6 && placed.at(-1).endH <= 23 + 1e-9);
  placed.slice(1).forEach((item, i) => assert.ok(item.startH >= placed[i].endH - 1e-9));
  assert.ok(placed.every((item) => item.endH > item.startH && item.session.start_h === 22 && item.session.end_h === 23));
});

test("layout order is stable for equal timestamps and input rows remain untouched", () => {
  const sessions = [block("b", 9, 10), block("a", 9, 10)];
  const original = structuredClone(sessions);
  const forward = layoutAgendaDay(sessions, tolerance);
  const reverse = layoutAgendaDay([...sessions].reverse(), tolerance);
  assert.deepEqual(forward, reverse);
  assert.deepEqual(sessions, original);
});
