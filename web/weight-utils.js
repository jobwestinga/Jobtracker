// Pure plotting math. No DOM, network, or third-party chart library.
const DAY = 86400000;

export function parseWeight(value) {
  const text = String(value ?? "").trim().replace(",", ".");
  if (!/^\d+(?:\.\d+)?$/.test(text)) return null;
  const number = Number(text);
  return Number.isFinite(number) && number > 0 && number <= 1000 ? number : null;
}

/** Seven whole-kilogram choices; display state never derives from a draft. */
export function wholeWeightTiles(anchor = 80) {
  const center = Number.isFinite(Number(anchor)) ? Math.round(Number(anchor)) : 80;
  return Array.from({ length: 7 }, (_, index) => center - 3 + index);
}

export function weightTileAnchor(rows, fallback = 80) {
  const latest = selectMeasurements(rows, { historic: true }).filter((row) => parseWeight(row.weight_kg) !== null).at(-1);
  return Math.round(latest ? Number(latest.weight_kg) : parseWeight(fallback) ?? 80);
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

export function selectMeasurements(rows, { start = "", end = "", historic = false } = {}) {
  return (Array.isArray(rows) ? rows : []).filter((row) => {
    if (!row || typeof row.measured_at !== "string" || !Number.isFinite(new Date(row.measured_at).getTime())) return false;
    const day = row.measured_at.slice(0, 10);
    return (historic || row.source !== "historic") && (!start || day >= start) &&
      (!end || day <= end) && Number.isFinite(Number(row.weight_kg)) && Number(row.weight_kg) > 0;
  }).sort((a, b) => a.measured_at.localeCompare(b.measured_at));
}

export function pointsFor(rows) {
  return (Array.isArray(rows) ? rows : []).filter((row) => row && typeof row.measured_at === "string")
    .map((row) => ({ x: new Date(row.measured_at).getTime(), y: Number(row.weight_kg), row }))
    .filter((p) => Number.isFinite(p.x) && Number.isFinite(p.y))
    .sort((a, b) => a.x - b.x);
}

function uniquePoints(points) {
  const result = [];
  const finite = (Array.isArray(points) ? points : []).filter(
    (point) => Number.isFinite(point?.x) && Number.isFinite(point?.y)
  );
  for (const point of finite.sort((a, b) => a.x - b.x)) {
    const previous = result.at(-1);
    if (previous?.x === point.x) {
      previous.count++;
      previous.y += (point.y - previous.y) / previous.count;
    } else result.push({ x: point.x, y: point.y, count: 1 });
  }
  return result;
}

/** Local linear LOESS with tricube weights and no robustness iterations.
 * Default span matches Weight_graph's 0.04. Fits use days relative to each
 * center to avoid subtracting nearly equal, enormous epoch-time products.
 */
export function loess(points, span = 0.04) {
  const values = uniquePoints(points);
  if (values.length < 3) return values.map(({ x, y }) => ({ x, y }));
  const numericSpan = Number(span);
  const fraction = Number.isFinite(numericSpan) ? Math.max(0, Math.min(1, numericSpan)) : 0.04;
  const count = Math.max(2, Math.min(values.length, Math.floor(values.length * fraction + 1e-10)));
  // Nearest neighbours depend on distance, not the point's array index. Keep
  // the left edge from the previous fit; as centers increase it only moves
  // forward, including across large gaps in irregular measurement dates.
  let left = 0;
  return values.map((point) => {
    while (left + count < values.length && point.x - values[left].x > values[left + count].x - point.x) left++;
    const radius = Math.max(point.x - values[left].x, values[left + count - 1].x - point.x) / DAY;
    if (!radius) return { x: point.x, y: point.y };
    let sw = 0, sx = 0, sy = 0, sxx = 0, sxy = 0;
    for (let j = left; j < left + count; j++) {
      const x = (values[j].x - point.x) / DAY;
      const weight = (1 - Math.min(1, Math.abs(x) / radius) ** 3) ** 3;
      sw += weight; sx += weight * x; sy += weight * values[j].y;
      sxx += weight * x * x; sxy += weight * x * values[j].y;
    }
    const determinant = sw * sxx - sx * sx;
    const y = Math.abs(determinant) > 1e-12 ? (sy * sxx - sx * sxy) / determinant : (sw ? sy / sw : point.y);
    return { x: point.x, y };
  });
}

/** A calendar-day rolling mean; missing days are never filled with zero. */
export function rollingMean(points, days = 7) {
  const values = uniquePoints(points);
  const numericDays = Number(days);
  const calendarDays = Number.isFinite(numericDays) ? Math.max(1, Math.floor(numericDays)) : 7;
  const civilDay = (timestamp) => {
    const date = new Date(timestamp);
    return Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()) / DAY;
  };
  let left = 0, sum = 0;
  return values.map((point, right) => {
    sum += point.y;
    const cutoff = civilDay(point.x) - calendarDays + 1;
    while (left < right && civilDay(values[left].x) < cutoff) sum -= values[left++].y;
    return { x: point.x, y: sum / (right - left + 1) };
  });
}

/** Shape-preserving cubic interpolation for the sparse historic segment. */
export function pchip(points, until, samples = 120) {
  const knots = uniquePoints(points);
  if (knots.length < 2) return knots.map(({ x, y }) => ({ x, y }));
  const x = knots.map((p) => (p.x - knots[0].x) / DAY);
  const h = x.slice(1).map((value, i) => value - x[i]);
  const slopes = h.map((width, i) => (knots[i + 1].y - knots[i].y) / width);
  const derivatives = Array(knots.length).fill(0);
  function edge(h0, h1, d0, d1) {
    let value = ((2 * h0 + h1) * d0 - h0 * d1) / (h0 + h1);
    if (Math.sign(value) !== Math.sign(d0)) value = 0;
    else if (Math.sign(d0) !== Math.sign(d1) && Math.abs(value) > Math.abs(3 * d0)) value = 3 * d0;
    return value;
  }
  derivatives[0] = knots.length === 2 ? slopes[0] : edge(h[0], h[1], slopes[0], slopes[1]);
  const last = slopes.length - 1;
  derivatives[knots.length - 1] = knots.length === 2 ? slopes[0] : edge(h[last], h[last - 1], slopes[last], slopes[last - 1]);
  for (let i = 1; i < knots.length - 1; i++) {
    if (slopes[i - 1] && slopes[i] && Math.sign(slopes[i - 1]) === Math.sign(slopes[i])) {
      const w1 = 2 * h[i] + h[i - 1], w2 = h[i] + 2 * h[i - 1];
      derivatives[i] = (w1 + w2) / (w1 / slopes[i - 1] + w2 / slopes[i]);
    }
  }
  const requestedEnd = Number(until ?? knots.at(-1).x);
  const end = Math.min(Number.isFinite(requestedEnd) ? requestedEnd : knots.at(-1).x, knots.at(-1).x);
  if (end <= knots[0].x) return [{ x: knots[0].x, y: knots[0].y }];
  const requestedSamples = Number(samples);
  const sampleCount = Number.isFinite(requestedSamples) ? Math.max(2, Math.min(10000, Math.floor(requestedSamples))) : 120;
  let interval = 0;
  return Array.from({ length: sampleCount }, (_, i) => {
    const timestamp = knots[0].x + (end - knots[0].x) * i / (sampleCount - 1);
    const location = (timestamp - knots[0].x) / DAY;
    while (interval < knots.length - 2 && location > x[interval + 1]) interval++;
    const t = (location - x[interval]) / h[interval], t2 = t * t, t3 = t2 * t;
    const y = (2 * t3 - 3 * t2 + 1) * knots[interval].y +
      (t3 - 2 * t2 + t) * h[interval] * derivatives[interval] +
      (-2 * t3 + 3 * t2) * knots[interval + 1].y +
      (t3 - t2) * h[interval] * derivatives[interval + 1];
    return { x: timestamp, y };
  });
}

export function trendFor(points, mode = "loess", span = 0.04) {
  if (mode === "none") return [];
  points = (Array.isArray(points) ? points : []).filter((point) => Number.isFinite(point?.x) && Number.isFinite(point?.y));
  const dense = points.filter((p) => p.row?.source !== "historic");
  const historic = points.filter((p) => p.row?.source === "historic");
  if (mode === "mean7") return rollingMean(points, 7);
  if (!dense.length) return pchip(historic);
  const fitted = loess(dense, span);
  const old = historic.filter((p) => p.x < fitted[0].x);
  if (!old.length) return fitted;
  const sparse = pchip([...old, ...fitted.slice(0, 20)], fitted[0].x);
  return [...sparse, ...fitted];
}
