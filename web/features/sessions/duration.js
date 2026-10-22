import { localIso } from "../../shared/time.js";

export const QUICK_DURATIONS = Object.freeze([
  ["15m", 15], ["30m", 30], ["1h", 60], ["1h 15m", 75], ["1h 30m", 90], ["2h", 120],
]);

export const CUSTOM_DURATION_CHUNKS = Object.freeze([
  ["4h", 240], ["2h", 120], ["1h", 60], ["30m", 30], ["15m", 15], ["10m", 10], ["5m", 5],
]);

/** Capture one local end time and subtract wall-clock minutes, matching the
 * server's naive datetime contract even when a DST clock change lies between
 * start and end. UTC is only a calendar arithmetic surrogate; no Z is sent.
 */
export function endingNowRange(minutes, now = new Date()) {
  if (!Number.isInteger(minutes) || minutes <= 0) throw new RangeError("Choose a positive duration.");
  const end_time = localIso(now);
  const start = new Date(`${end_time}Z`);
  start.setUTCMinutes(start.getUTCMinutes() - minutes);
  return { start_time: start.toISOString().slice(0, 19), end_time };
}

/** Mirror the API's integer wall-clock seconds for basic pre-enqueue validity.
 * Preserve microseconds at the 30-second boundary; Date.parse alone loses them.
 */
export function manualDurationSeconds(start_time, end_time) {
  const micros = (value) => {
    const match = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?$/.exec(value);
    if (!match) return null;
    const millis = Date.parse(`${match[1]}Z`);
    if (!Number.isFinite(millis)) return null;
    return BigInt(millis) * 1000n + BigInt((match[2] || "").padEnd(6, "0"));
  };
  const start = micros(start_time), end = micros(end_time);
  return start === null || end === null ? NaN : Number((end - start) / 1000000n);
}
