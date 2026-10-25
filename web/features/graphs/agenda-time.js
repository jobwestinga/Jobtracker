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
  return `${pad(Math.floor(hour) % 24)}:00${hour >= 24 ? ` +${Math.floor(hour / 24)}` : ""}`;
}

export function agendaHourWindow(sessions, { hourStart = 6, hourEnd = 23, autofit = true } = {}) {
  // A day beginning as late as 23:59 can end near 48 in agenda-hour space.
  const lo = Math.max(0, Math.min(47, autofit && sessions.length ? Math.min(hourStart, Math.floor(Math.min(...sessions.map((s) => s.start_h)))) : hourStart));
  const hi = Math.max(lo + 1, Math.min(48, autofit && sessions.length ? Math.max(hourEnd, Math.ceil(Math.max(...sessions.map((s) => s.end_h)))) : hourEnd));
  return { lo, hi };
}

// Arrange overlapping blocks vertically for readability. Only display endpoints
// change: session objects, recorded times, duration and click targets stay exact.
export function layoutAgendaDay(sessions, toleranceHours, { hourStart = 0, hourEnd = 27 } = {}) {
  const placed = [...sessions].sort((a, b) => a.start_h - b.start_h || a.end_h - b.end_h ||
    String(a.uid || "").localeCompare(String(b.uid || ""))).map((session) => {
    const startH = Math.max(hourStart, session.start_h), endH = Math.min(hourEnd, session.end_h);
    return { session, startH, endH, duration: endH - startH, lane: 0, lanes: 1, joinsAbove: false, joinsBelow: false };
  }).filter((item) => item.duration > 0);
  let cursor = hourStart;
  for (const item of placed) {
    item.startH = Math.max(item.startH, cursor);
    cursor = item.endH = item.startH + item.duration;
  }
  if (cursor > hourEnd) {
    const total = placed.reduce((sum, item) => sum + item.duration, 0);
    if (total <= hourEnd - hourStart) {
      cursor = hourEnd;
      for (const item of [...placed].reverse()) {
        item.endH = Math.min(item.endH, cursor);
        cursor = item.startH = item.endH - item.duration;
      }
    } else {
      // Too much recorded time for this visible window: compress the display
      // consistently instead of hiding, overlapping or changing any session.
      const scale = (hourEnd - hourStart) / total;
      cursor = hourStart;
      for (const item of placed) {
        item.startH = cursor;
        cursor = item.endH = cursor + item.duration * scale;
      }
    }
  }
  placed.forEach((item, index) => {
    item.arranged = Math.abs(item.startH - Math.max(hourStart, item.session.start_h)) > 1e-9 ||
      Math.abs(item.endH - Math.min(hourEnd, item.session.end_h)) > 1e-9;
    const previous = placed[index - 1];
    if (previous && Math.abs(item.startH - previous.endH) <= toleranceHours) {
      previous.joinsBelow = true; item.joinsAbove = true;
    }
  });
  return placed;
}
