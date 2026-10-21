// Canvas presentation only. Data selection and smoothing are cached by index.js;
// calendar coordinates and fitting belong to math.js. See docs/features/weight.md.
import { weightYearTicks } from "./math.js";

// The screen palette follows the app's dark tokens with an accent trend, like the
// native chart. Saved PNGs keep the original light figure.
export const WEIGHT_CHART_PALETTES = {
  screen: {
    background: "#0F1522", panel: "#0F1522", grid: "#1F2B42", text: "#94A3B8", tick: "#94A3B8",
    yearLine: "#2C3B59", points: "rgba(238, 242, 248, 0.96)", trend: "#38BDF8",
  },
  print: {
    background: "#fff", panel: "#ebebeb", grid: "#fff", text: "#555", tick: "#4b4b4b",
    yearLine: "#c6c6c6", points: "#888", trend: "#111",
  },
};

const POINT_RADIUS = 2.5;

// `domain` ({x0, x1}) fixes the time axis for a zoomed view; otherwise it spans the points.
export function drawWeightChart(canvas, points, trend, preferences, invalidRange = false,
  { palette = WEIGHT_CHART_PALETTES.screen, width: fixedWidth, height: fixedHeight, ratio: fixedRatio, domain = null } = {}) {
  const width = fixedWidth ?? canvas.clientWidth, height = Math.max(1, Math.round(fixedHeight ?? canvas.clientHeight));
  const ratio = fixedRatio ?? Math.min(2, devicePixelRatio || 1);
  canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio);
  const ctx = canvas.getContext("2d"); ctx.scale(ratio, ratio);
  ctx.fillStyle = palette.background; ctx.fillRect(0, 0, width, height);
  ctx.font = "12px -apple-system, sans-serif";
  if (!points.length && !(domain && trend.length)) {
    ctx.fillStyle = palette.text; ctx.textAlign = "center";
    ctx.fillText(invalidRange ? "Choose an end date after the start date." : "No weighings in this date range", width / 2, height / 2);
    return null;
  }
  const left = 48, right = width - 15, top = 18, bottom = height - 48;
  const first = domain ? domain.x0 : points[0].x, last = domain ? domain.x1 : points.at(-1).x;
  const x0 = first === last ? first - 86400000 : first, x1 = first === last ? last + 86400000 : last;
  let min = Infinity, max = -Infinity;
  for (const point of [...points, ...trend]) { min = Math.min(min, point.y); max = Math.max(max, point.y); }
  const step = max - min > 25 ? 5 : max - min > 10 ? 2 : 1;
  min = preferences.zero ? 0 : Math.floor((min - 0.5) / step) * step;
  max = Math.ceil((max + 0.5) / step) * step;
  if (max <= min) max = min + 1;
  const px = (x) => left + (x - x0) / (x1 - x0) * (right - left);
  const py = (y) => bottom - (y - min) / (max - min) * (bottom - top);
  ctx.fillStyle = palette.panel; ctx.fillRect(left, top, right - left, bottom - top);
  ctx.textAlign = "right"; ctx.textBaseline = "middle";
  const tick = preferences.zero ? Math.max(step, Math.ceil((max - min) / 8 / 5) * 5) : step;
  for (let value = min; value <= max + 1e-8; value += tick) {
    const y = py(value); ctx.strokeStyle = palette.grid; ctx.lineWidth = value % 5 === 0 ? 1.8 : 0.8;
    ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(right, y); ctx.stroke();
    ctx.fillStyle = palette.tick; ctx.fillText(String(Math.round(value * 10) / 10), left - 8, y);
  }
  ctx.fillStyle = palette.text; ctx.textAlign = "left"; ctx.fillText("kg", 8, 12);
  const labels = width < 450 ? 3 : 5;
  for (let i = 0; i < labels; i++) {
    const x = x0 + (x1 - x0) * i / (labels - 1);
    ctx.textAlign = i === 0 ? "left" : i === labels - 1 ? "right" : "center";
    ctx.fillText(new Date(x).toLocaleDateString(undefined, { timeZone: "UTC", month: "short", day: "numeric" }), px(x), bottom + 18);
  }
  // Every year gets a thin line. Thin labels only when space requires it, and
  // align them to the same January 1 coordinates on a separate year row.
  const years = weightYearTicks(x0, x1);
  let labelRight = -Infinity;
  for (const { x, year } of years) {
    const position = px(x);
    ctx.strokeStyle = palette.yearLine; ctx.lineWidth = 0.7;
    ctx.beginPath(); ctx.moveTo(position, top); ctx.lineTo(position, bottom); ctx.stroke();
    const text = String(year), labelWidth = ctx.measureText(text).width;
    const center = Math.max(left + labelWidth / 2, Math.min(right - labelWidth / 2, position));
    if (center - labelWidth / 2 < labelRight + 10) continue;
    ctx.fillStyle = palette.text; ctx.textAlign = "center";
    ctx.fillText(text, center, bottom + 35); labelRight = center + labelWidth / 2;
  }
  if (!years.length) {
    ctx.fillStyle = palette.text; ctx.textAlign = "left";
    ctx.fillText(String(new Date(first).getUTCFullYear()), left, bottom + 35);
  }
  ctx.save(); ctx.beginPath(); ctx.rect(left - 3, top - 3, right - left + 6, bottom - top + 6); ctx.clip();
  if (trend.length > 1 && palette === WEIGHT_CHART_PALETTES.screen) {
    if (typeof ctx.createLinearGradient === "function") {
      const grad = ctx.createLinearGradient(0, top, 0, bottom);
      grad.addColorStop(0, "rgba(56, 189, 248, 0.24)");
      grad.addColorStop(1, "rgba(56, 189, 248, 0.0)");
      ctx.fillStyle = grad;
    } else {
      ctx.fillStyle = "rgba(56, 189, 248, 0.12)";
    }
    ctx.beginPath();
    ctx.moveTo(px(trend[0].x), bottom);
    for (const point of trend) ctx.lineTo(px(point.x), py(point.y));
    ctx.lineTo(px(trend.at(-1).x), bottom);
    ctx.fill();
  }
  if (trend.length) {
    ctx.strokeStyle = palette.trend; ctx.lineWidth = 2.4; ctx.lineJoin = "round"; ctx.beginPath();
    trend.forEach((point, index) => index ? ctx.lineTo(px(point.x), py(point.y)) : ctx.moveTo(px(point.x), py(point.y))); ctx.stroke();
  }
  if (preferences.points) {
    if (palette === WEIGHT_CHART_PALETTES.screen) {
      ctx.fillStyle = palette.panel; ctx.beginPath();
      const ringRadius = POINT_RADIUS + 1.1;
      for (const point of points) { ctx.moveTo(px(point.x) + ringRadius, py(point.y)); ctx.arc(px(point.x), py(point.y), ringRadius, 0, Math.PI * 2); }
      ctx.fill();
    }
    ctx.fillStyle = palette.points; ctx.beginPath();
    for (const point of points) { ctx.moveTo(px(point.x) + POINT_RADIUS, py(point.y)); ctx.arc(px(point.x), py(point.y), POINT_RADIUS, 0, Math.PI * 2); }
    ctx.fill();
  }
  ctx.restore();
  return { x0, x1, left, right };
}
