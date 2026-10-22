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

test("overlaps stay at their true times in separate lanes", () => {
  const placed = layoutAgendaDay([block("long", 9, 12), block("inside", 10, 11), block("after", 12, 13)], tolerance);
  const byUid = Object.fromEntries(placed.map((item) => [item.session.uid, item]));
  assert.equal(byUid.long.lanes, 2);
  assert.equal(byUid.inside.lanes, 2);
  assert.notEqual(byUid.long.lane, byUid.inside.lane);
  assert.equal(byUid.inside.session.start_h, 10, "no timestamp is shifted");
  assert.equal(byUid.after.lanes, 1, "a later cluster returns to one lane");
});

test("a gap larger than the tolerance stays visible", () => {
  const placed = layoutAgendaDay([block("a", 9, 10), block("b", 10 + 31 / 60, 11)], tolerance);
  assert.ok(!placed[0].joinsBelow && !placed[1].joinsAbove);
});
