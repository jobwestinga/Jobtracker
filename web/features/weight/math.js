// Pure plotting math. No DOM, network, or third-party chart library.
const DAY = 86400000;

export function parseWeight(value) {
  if (typeof value === "number") return Number.isFinite(value) && value > 0 && value <= 1000 ? value : null;
  if (typeof value !== "string") return null;
  const text = String(value ?? "").trim().replace(",", ".");
  if (!/^\d+(?:\.\d+)?$/.test(text)) return null;
  const number = Number(text);
  return Number.isFinite(number) && number > 0 && number <= 1000 ? number : null;
}

/** A naive calendar coordinate, using UTC fields solely to avoid DST coercion. */
export function weightTimestamp(value) {
  if (typeof value !== "string") return null;
  const parts = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?)?$/.exec(value);
  if (!parts) return null;
  const [, year, month, day, hour = "0", minute = "0", second = "0", fraction = ""] = parts;
  if (Number(year) < 1 || Number(month) < 1 || Number(month) > 12 || Number(day) < 1 ||
      Number(hour) > 23 || Number(minute) > 59 || Number(second) > 59) return null;
  const date = new Date(0);
  date.setUTCFullYear(Number(year), Number(month) - 1, Number(day));
  date.setUTCHours(Number(hour), Number(minute), Number(second), 0);
  if (date.getUTCFullYear() !== Number(year) || date.getUTCMonth() + 1 !== Number(month) ||
      date.getUTCDate() !== Number(day)) return null;
  return date.getTime() + (fraction ? Number(`0.${fraction}`) * 1000 : 0);
}

export function weightDayOffset(day, offset) {
  const timestamp = weightTimestamp(day);
  if (timestamp === null || !Number.isInteger(offset)) return "";
  const date = new Date(timestamp + offset * DAY), year = date.getUTCFullYear();
  return year >= 1 && year <= 9999 ? date.toISOString().slice(0, 10) : "";
}

/** January 1 boundaries in naive calendar coordinates, including ISO years 1–99. */
export function weightYearTicks(first, last) {
  if (!Number.isFinite(first) || !Number.isFinite(last) || first > last) return [];
  const start = Math.max(1, new Date(first).getUTCFullYear());
  const end = Math.min(9999, new Date(last).getUTCFullYear());
  const ticks = [];
  for (let year = start; year <= end; year++) {
    const x = weightTimestamp(`${String(year).padStart(4, "0")}-01-01`);
    if (x >= first && x <= last) ticks.push({ x, year });
  }
  return ticks;
}

export function selectMeasurements(rows, { start = "", end = "", historic = false } = {}) {
  return (Array.isArray(rows) ? rows : []).filter((row) => {
    if (!row || weightTimestamp(row.measured_at) === null || parseWeight(row.weight_kg) === null) return false;
    const day = row.measured_at.slice(0, 10);
    return (historic || row.source !== "historic") && (!start || day >= start) &&
      (!end || day <= end);
  }).sort((a, b) => a.measured_at.localeCompare(b.measured_at));
}

export function pointsFor(rows) {
  return (Array.isArray(rows) ? rows : []).filter((row) => row && typeof row.measured_at === "string")
    .map((row) => ({ x: weightTimestamp(row.measured_at), y: parseWeight(row.weight_kg), row }))
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
    } else result.push({ x: point.x, y: point.y, count: 1, row: point.row });
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
  const civilDay = (point) => {
    const naiveTimestamp = weightTimestamp(point.row?.measured_at);
    if (naiveTimestamp !== null) return Math.floor(naiveTimestamp / DAY);
    const date = new Date(point.x);
    return Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()) / DAY;
  };
  let left = 0, sum = 0;
  return values.map((point, right) => {
    sum += point.y;
    const cutoff = civilDay(point) - calendarDays + 1;
    while (left < right && civilDay(values[left]) < cutoff) sum -= values[left++].y;
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

/** The stretch of a whole-history trend that a [x0, x1] view shows, plus one
 * point beyond each edge so the line reaches the clipped plot border. */
export function trendBetween(trend, x0, x1) {
  if (!Array.isArray(trend) || !trend.length || !Number.isFinite(x0) || !Number.isFinite(x1)) return [];
  const first = trend.findIndex((point) => point.x >= x0);
  if (first < 0) return [];
  let last = trend.length - 1;
  while (last > first && trend[last].x > x1) last--;
  return trend.slice(Math.max(0, first - 1), Math.min(trend.length, last + 2));
}

// Energy behind a kilogram of trend change. Losses during hypertrophy training
// are treated as adipose tissue (~7,700 kcal/kg). Gains are assumed to be about
// half lean tissue (~3,500 kcal/kg including synthesis) and half fat, ~5,600.
export const KCAL_PER_KG_LOSS = 7700;
export const KCAL_PER_KG_GAIN = 5600;

/** The trend's height at x, linearly between its samples; null outside it. */
export function trendValueAt(trend, x) {
  if (!Array.isArray(trend) || !trend.length || !Number.isFinite(x)) return null;
  if (x < trend[0].x || x > trend.at(-1).x) return null;
  let low = 0, high = trend.length - 1;
  while (high - low > 1) {
    const middle = (low + high) >> 1;
    if (trend[middle].x <= x) low = middle; else high = middle;
  }
  const a = trend[low], b = trend[high];
  return b.x === a.x ? a.y : a.y + (b.y - a.y) * (x - a.x) / (b.x - a.x);
}

/** The tangent touching the trend at x. The slope is a central difference
 * across `reach` on each side (one-sided at the ends), in kg per day. */
export function trendTangent(trend, x, reach = 3 * DAY) {
  const y = trendValueAt(trend, x);
  if (y === null || trend.length < 2) return null;
  const a = Math.max(trend[0].x, x - reach), b = Math.min(trend.at(-1).x, x + reach);
  if (!(b > a)) return null;
  const perDay = (trendValueAt(trend, b) - trendValueAt(trend, a)) / ((b - a) / DAY);
  return { x, y, perDay, kcal: tangentEnergy(perDay) };
}

/** Daily energy surplus (+) or deficit (-) implied by a trend slope in kg/day. */
export function tangentEnergy(perDay) {
  if (!Number.isFinite(perDay)) return 0;
  return perDay * (perDay < 0 ? KCAL_PER_KG_LOSS : KCAL_PER_KG_GAIN);
}

/** "−462 kcal/day · −0.42 kg/wk" for a trendTangent result. */
export function tangentLabel({ perDay, kcal }) {
  const signed = (value, digits) => {
    const text = Math.abs(value).toFixed(digits);
    return Number(text) === 0 ? text : `${value < 0 ? "\u2212" : "+"}${text}`;
  };
  return `${signed(kcal, 0)} kcal/day \u00b7 ${signed(perDay * 7, 2)} kg/wk`;
}
