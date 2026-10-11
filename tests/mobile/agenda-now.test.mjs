import test from "node:test";
import assert from "node:assert/strict";
import { agendaNowMarker, dayStartHour, logicalAgendaNow, agendaHourLabel } from "../../web/features/graphs/agenda-time.js";

process.env.TZ = "Europe/Amsterdam";
const now = (value) => new Date(value);

test("the agenda clock follows logical days before midnight and at03:00", () => {
  assert.deepEqual(logicalAgendaNow(now("2026-10-10T23:30:00")), { day: "2026-10-10", hour: 23.5, label: "23:30" });
  assert.deepEqual(logicalAgendaNow(now("2026-10-11T01:30:00")), { day: "2026-10-10", hour: 25.5, label: "01:30" });
  assert.equal(logicalAgendaNow(now("2026-10-11T02:59:00")).day, "2026-10-10");
  assert.deepEqual(logicalAgendaNow(now("2026-10-11T03:00:00")), { day: "2026-10-11", hour: 3, label: "03:00" });
});

test("now line only appears in the correct visible day and hour window", () => {
  const options = { now: now("2026-10-11T01:30:00") };
  assert.equal(agendaNowMarker(["2026-10-10"], 6, 23, options), null);
  assert.equal(agendaNowMarker(["2026-10-11"], 0, 27, options), null);
  assert.equal(agendaNowMarker([], 0, 27, options), null);
  const marker = agendaNowMarker(["2026-10-09", "2026-10-10", "2026-10-11"], 24, 27, options);
  assert.equal(marker.day, "2026-10-10"); assert.equal(marker.fraction, .5);
});

test("API fractional boundary wins and context fallback retains minutes", () => {
  assert.equal(dayStartHour({ day_start_hour: 6.5 }, "03:00"), 6.5);
  assert.equal(dayStartHour({}, "06:30"), 6.5);
  assert.equal(dayStartHour({}, "23:45"), 23.75);
  assert.equal(logicalAgendaNow(now("2026-10-11T06:29:00"), 6.5).day, "2026-10-10");
  assert.equal(logicalAgendaNow(now("2026-10-11T06:30:00"), 6.5).day, "2026-10-11");
  assert.equal(logicalAgendaNow(now("2026-10-11T23:44:00"), 23.75).hour, 47 + 44 / 60);
});

test("midnight boundary uses current calendar day without wrapping", () => {
  assert.equal(logicalAgendaNow(now("2026-10-11T00:30:00"), 0).day, "2026-10-11");
  assert.equal(logicalAgendaNow(now("2026-10-11T00:30:00"), 0).hour, .5);
});

test("DST calendar subtraction handles spring jump and both repeated autumn clocks", () => {
  assert.equal(logicalAgendaNow(now("2026-03-29T01:30:00")).day, "2026-03-28");
  assert.equal(logicalAgendaNow(now("2026-03-29T03:30:00")).day, "2026-03-29");
  const first = logicalAgendaNow(now("2026-10-25T00:30:00Z"));
  const second = logicalAgendaNow(now("2026-10-25T01:30:00Z"));
  assert.deepEqual(first, second);
  assert.equal(first.hour, 26.5); assert.equal(first.day, "2026-10-24");
});

test("invalid clock, preferences and absent day never create a misleading line", () => {
  for (const boundary of [NaN, Infinity, -1, 24, "6"]) assert.equal(dayStartHour({ day_start_hour: boundary }, "invalid"), 3);
  assert.equal(dayStartHour({}, "03:99"), 3);
  assert.equal(logicalAgendaNow(new Date("invalid")), null);
  for (const [lo, hi] of [[3, 3], [NaN, 27], [0, Infinity], [27, 3]]) {
    assert.equal(agendaNowMarker(["2026-10-10"], lo, hi, { now: now("2026-10-10T12:00:00") }), null);
  }
  assert.equal(agendaHourLabel(25), "01:00 +1");
});

test("now-marker fraction moves by one minute without depending on session blocks", () => {
  const days = ["2026-10-10"];
  const a = agendaNowMarker(days, 6, 23, { now: now("2026-10-10T12:00:00") });
  const b = agendaNowMarker(days, 6, 23, { now: now("2026-10-10T12:01:00") });
  assert.ok(Math.abs((b.fraction - a.fraction) - 1 / (60 * 17)) < 1e-12);
  assert.equal(agendaNowMarker(days, 6, 23, { now: now("2026-10-11T12:00:00") }), null);
});

// Both runtimes consume these fixed local-time examples.
test("agenda follows the shared Python/JavaScript boundary contract", async () => {
  const { readFile } = await import("node:fs/promises");
  const fixture = JSON.parse(await readFile(new URL("../contracts/time_boundaries.json", import.meta.url), "utf8"));
  assert.equal(fixture.schema_version, 1);
  for (const entry of fixture.cases) {
    const position = logicalAgendaNow(new Date(entry.now), dayStartHour({}, entry.boundary));
    assert.equal(position.day, entry.logical_day, `${entry.now} / ${entry.boundary}`);
    assert.ok(Math.abs(position.hour - entry.hour) < 1e-10, `${entry.now} hour`);
  }
});
