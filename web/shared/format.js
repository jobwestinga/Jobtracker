import { pad, addDays } from "./time.js";

export function hms(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 3600)}:${pad(Math.floor((s % 3600) / 60))}:${pad(s % 60)}`;
}

export function hm(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h ? `${h}h ${pad(m)}m` : `${m}m`;
}

export function clockOf(iso) {
  return iso ? iso.slice(11, 16) : "--:--";
}

export function prettyDay(iso, today) {
  if (!iso) return "—";
  const d = new Date(`${iso}T12:00:00`);
  if (iso === today) return "Today";
  if (today && iso === addDays(today, -1)) return "Yesterday";
  return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
}

export function prettyDayShort(iso) {
  const d = new Date(`${iso}T12:00:00`);
  return `${d.toLocaleDateString(undefined, { weekday: "short" }).slice(0, 2)} ${d.getDate()}`;
}

export function shortLabel(iso, grouping) {
  const d = new Date(`${iso}T12:00:00`);
  if (grouping === "monthly") return d.toLocaleDateString(undefined, { month: "short" });
  if (grouping === "weekly") return `${d.getDate()}/${d.getMonth() + 1}`;
  return d.toLocaleDateString(undefined, { weekday: "narrow" });
}

export const weightDay = (value) => new Date(`${value.slice(0, 10)}T12:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
