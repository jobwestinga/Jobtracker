// The tracking agenda uses local wall-clock time and the same logical-day
// boundary as the server/native app. Calendar subtraction avoids DST offsets.
const pad = (value) => String(value).padStart(2, "0");
const dayOf = (date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;

export function dayStartHour(data = {}, fallback = "03:00") {
  if (typeof data.day_start_hour === "number" && Number.isFinite(data.day_start_hour) &&
      data.day_start_hour >= 0 && data.day_start_hour < 24) return data.day_start_hour;
  const match = /^(\d{2}):(\d{2})(?::\d{2})?$/.exec(String(fallback));
  if (!match || Number(match[1]) > 23 || Number(match[2]) > 59) return 3;
  return Number(match[1]) + Number(match[2]) / 60;
}

export function logicalAgendaNow(now = new Date(), boundary = 3) {
  if (!(now instanceof Date) || !Number.isFinite(now.getTime())) return null;
  const start = typeof boundary === "number" && Number.isFinite(boundary) && boundary >= 0 && boundary < 24 ? boundary : 3;
  let hour = now.getHours() + now.getMinutes() / 60 + now.getSeconds() / 3600;
  const day = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 12);
  if (hour < start) { day.setDate(day.getDate() - 1); hour += 24; }
  return { day: dayOf(day), hour, label: `${pad(now.getHours())}:${pad(now.getMinutes())}` };
}

export function agendaNowMarker(days, hourStart, hourEnd, { now = new Date(), boundary = 3 } = {}) {
  const position = logicalAgendaNow(now, boundary);
  if (!position || !Array.isArray(days) || !days.includes(position.day) ||
      !Number.isFinite(hourStart) || !Number.isFinite(hourEnd) || hourEnd <= hourStart ||
      position.hour < hourStart || position.hour > hourEnd) return null;
  return { ...position, fraction: (position.hour - hourStart) / (hourEnd - hourStart) };
}

export function agendaHourLabel(hour) {
  return `${pad(Math.floor(hour) % 24)}:00${hour >= 24 ? " +1" : ""}`;
}
