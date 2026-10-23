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

// Bold agenda: overlapping sessions keep their true times in side-by-side
// lanes; same-lane blocks separated by at most the tolerance (max of 30 s and
// one device pixel) join into one shape. Joining is drawing only: each block
// keeps its own interval, target and identity, and no gap counts as work.
export function layoutAgendaDay(sessions, toleranceHours) {
  const placed = [];
  let laneEnds = [], cluster = [], clusterEnd = -Infinity;
  const close = () => {
    const lanes = Math.max(1, ...cluster.map((item) => item.lane + 1));
    for (const item of cluster) item.lanes = lanes;
    cluster = []; laneEnds = [];
  };
  for (const session of [...sessions].sort((a, b) => a.start_h - b.start_h || a.end_h - b.end_h)) {
    if (session.start_h >= clusterEnd - 1e-9) close();
    let lane = laneEnds.findIndex((end) => end <= session.start_h + 1e-9);
    if (lane < 0) { lane = laneEnds.length; laneEnds.push(session.end_h); } else laneEnds[lane] = session.end_h;
    const item = { session, lane, lanes: 1, joinsAbove: false, joinsBelow: false };
    placed.push(item); cluster.push(item);
    clusterEnd = Math.max(clusterEnd, session.end_h);
  }
  close();
  const lastInLane = new Map();
  for (const item of placed) {
    const key = `${item.lane}/${item.lanes}`;
    const previous = lastInLane.get(key);
    if (previous && Math.abs(item.session.start_h - previous.session.end_h) <= toleranceHours) {
      previous.joinsBelow = true; item.joinsAbove = true;
    }
    lastInLane.set(key, item);
  }
  return placed;
}
