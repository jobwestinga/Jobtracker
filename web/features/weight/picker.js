import { parseWeight, selectMeasurements } from "./math.js";

/** Seven whole-kilogram choices; display state never derives from a draft. */
export function wholeWeightTiles(anchor = 80) {
  const center = Number.isFinite(Number(anchor)) ? Math.round(Number(anchor)) : 80;
  return Array.from({ length: 7 }, (_, index) => center - 3 + index);
}

export function weightTileAnchor(rows, fallback = 80) {
  const latest = selectMeasurements(rows, { historic: true }).filter((row) => parseWeight(row.weight_kg) !== null).at(-1);
  return Math.round(latest ? parseWeight(latest.weight_kg) : parseWeight(fallback) ?? 80);
}

export function splitWeightTiles(value) {
  const parsed = parseWeight(value);
  if (parsed === null) return null;
  const tenths = Math.round(parsed * 10);
  return { whole: Math.floor(tenths / 10), decimal: tenths % 10 };
}

export function selectedTileWeight(whole, decimal) {
  if (!Number.isInteger(whole) || !Number.isInteger(decimal) || decimal < 0 || decimal > 9) return null;
  return parseWeight((whole * 10 + decimal) / 10);
}

export function dailyWeightMoment(day, original = null) {
  return original ? `${day}${original.measured_at.slice(10) || "T00:00:00"}` : day;
}

export function manualDateCollision(rows, original, day) {
  return original?.source === "manual" && original.measured_at.slice(0, 10) !== day &&
    rows.some((row) => row.uid !== original.uid && row.source === "manual" && row.measured_at.slice(0, 10) === day);
}
