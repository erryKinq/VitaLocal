// Hand-rolled SVG charts. Every chart takes an explicit width (see dom.responsive) and
// never interpolates across gaps: missing data stays visibly missing.

import { h, s } from "./dom.js";
import * as f from "./format.js";
import { locale, t } from "./i18n.js";

const M = { l: 44, r: 14, t: 10, b: 24 };

export function niceTicks(min, max, count = 4) {
  if (min === max) { min -= 1; max += 1; }
  const span = max - min;
  const step0 = span / count;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((k) => k * mag).find((k) => span / k <= count) || 10 * mag;
  const lo = Math.floor(min / step) * step;
  const hi = Math.ceil(max / step) * step;
  const ticks = [];
  for (let v = lo; v <= hi + step / 2; v += step) ticks.push(+v.toFixed(10));
  return { lo, hi, ticks, step };
}

function bisect(points, x) {
  let lo = 0, hi = points.length - 1;
  if (hi < 0) return -1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (points[mid][0] < x) lo = mid + 1; else hi = mid;
  }
  if (lo > 0 && Math.abs(points[lo - 1][0] - x) < Math.abs(points[lo][0] - x)) lo--;
  return lo;
}

function pathWithGaps(points, sx, sy, gap) {
  let d = "";
  let prev = null;
  const runs = [];
  let run = [];
  for (const [x, y] of points) {
    if (y == null) { prev = null; continue; }
    const fresh = prev == null || (gap && x - prev > gap);
    if (fresh && run.length) { runs.push(run); run = []; }
    d += `${fresh ? "M" : "L"}${sx(x).toFixed(1)},${sy(y).toFixed(1)}`;
    run.push([x, y]);
    prev = x;
  }
  if (run.length) runs.push(run);
  return { d, runs };
}

/**
 * Time-based chart.
 * series: [{ label, color, points: [[t, y]], type: "line"|"area"|"bars"|"dots", barWidth, gap,
 *            format(y) }]
 * bands:  [{ from, to, color, label }] drawn behind the data (activities, sleep ...)
 */
export function timeChart({
  width, height = 180, series, xMin, xMax, yMin, yMax, xTickEvery, xLabel = f.time,
  tooltipTime = f.time, bands = [], yFormat = (v) => f.num(v), emptyText = null,
}) {
  const all = series.flatMap((se) => se.points.filter((p) => p[1] != null));
  const wrap = h("div", { class: "chart" });
  if (!all.length) {
    wrap.append(h("div", { class: "chart-empty", style: { height: `${height}px` } }, emptyText ?? t("No data")));
    return wrap;
  }
  const W = width, H = height;
  const x0 = xMin ?? Math.min(...all.map((p) => p[0]));
  const x1 = xMax ?? Math.max(...all.map((p) => p[0]));
  const hasBars = series.some((se) => se.type === "bars");
  const ys = all.map((p) => p[1]);
  const yt = niceTicks(yMin ?? (hasBars ? 0 : Math.min(...ys)), yMax ?? Math.max(...ys));
  const sx = (x) => M.l + ((x - x0) / Math.max(1, x1 - x0)) * (W - M.l - M.r);
  const sy = (y) => M.t + (1 - (y - yt.lo) / (yt.hi - yt.lo || 1)) * (H - M.t - M.b);

  const svg = s("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, class: "chart-svg", role: "img" });

  for (const b of bands) {
    const a = Math.max(sx(b.from), M.l), z = Math.min(sx(b.to), W - M.r);
    if (z <= a) continue;
    svg.append(s("rect", { x: a, y: M.t, width: z - a, height: H - M.t - M.b, class: "band",
      style: { fill: b.color || "var(--band)" } }, s("title", {}, b.label || "")));
  }

  for (const v of yt.ticks) {
    svg.append(s("line", { x1: M.l, x2: W - M.r, y1: sy(v), y2: sy(v), class: "grid" }));
    svg.append(s("text", { x: M.l - 6, y: sy(v) + 4, class: "tick", "text-anchor": "end" }, yFormat(v)));
  }

  // x ticks: evenly spaced in time, labels thinned to fit
  const every = xTickEvery || niceTimeStep(x1 - x0);
  const first = Math.ceil(x0 / every) * every;
  const minGapPx = 56;
  let lastPx = -Infinity;
  for (let t = xTickEvery ? x0 : first; t <= x1; t += every) {
    const px = sx(t);
    svg.append(s("line", { x1: px, x2: px, y1: M.t, y2: H - M.b, class: "grid grid-x" }));
    if (px - lastPx >= minGapPx && px <= W - M.r - 10) {
      svg.append(s("text", { x: px, y: H - 6, class: "tick", "text-anchor": "middle" }, xLabel(t)));
      lastPx = px;
    }
  }

  for (const se of series) {
    const color = se.color || "var(--accent)";
    const pts = se.points;
    if (se.type === "bars") {
      const bw = Math.max(1, sx(x0 + (se.barWidth || 3_600_000)) - sx(x0) - 1);
      for (const [x, y] of pts) {
        if (y == null) continue;
        svg.append(s("rect", { x: sx(x), y: sy(Math.max(y, 0)), width: bw, height: Math.max(0.5, sy(0) - sy(Math.max(y, 0))),
          rx: Math.min(2, bw / 3), style: { fill: color }, class: "bar" }));
      }
      continue;
    }
    if (se.type === "dots") {
      for (const [x, y] of pts) if (y != null) svg.append(s("circle", { cx: sx(x), cy: sy(y), r: 2.5, style: { fill: color } }));
      continue;
    }
    const { d, runs } = pathWithGaps(pts, sx, sy, se.gap);
    if (se.type === "area") {
      for (const run of runs) {
        const top = run.map(([x, y], i) => `${i ? "L" : "M"}${sx(x).toFixed(1)},${sy(y).toFixed(1)}`).join("");
        const area = `${top}L${sx(run.at(-1)[0]).toFixed(1)},${sy(yt.lo)}L${sx(run[0][0]).toFixed(1)},${sy(yt.lo)}Z`;
        svg.append(s("path", { d: area, class: "area", style: { fill: color } }));
      }
    }
    svg.append(s("path", { d, class: ["line", se.dashed && "dashed"], style: { stroke: color, strokeWidth: se.strokeWidth || 1.6 } }));
    for (const run of runs) if (run.length === 1) {
      svg.append(s("circle", { cx: sx(run[0][0]), cy: sy(run[0][1]), r: 2, style: { fill: color } }));
    }
  }

  // hover
  const cursor = s("line", { y1: M.t, y2: H - M.b, class: "cursor", visibility: "hidden" });
  const tip = h("div", { class: "tooltip", hidden: true });
  svg.append(cursor);
  const overlay = s("rect", { x: M.l, y: 0, width: W - M.l - M.r, height: H, fill: "transparent" });
  const move = (ev) => {
    const rect = svg.getBoundingClientRect();
    const px = ev.clientX - rect.left;
    const t = x0 + ((px - M.l) / (W - M.l - M.r)) * (x1 - x0);
    const rows = [];
    let snap = null;
    for (const se of series) {
      if (se.hideInTooltip) continue;
      const i = bisect(se.points, t);
      if (i < 0) continue;
      const [px2, py] = se.points[i];
      const tol = se.gap || (x1 - x0) / 40;
      if (Math.abs(px2 - t) > tol || py == null) continue;
      snap ??= px2;
      rows.push(h("div", {}, h("i", { class: "swatch", style: { background: se.color || "var(--accent)" } }),
        `${se.label}: `, h("b", {}, (se.format || yFormat)(py))));
    }
    if (!rows.length) { leave(); return; }
    cursor.setAttribute("x1", sx(snap)); cursor.setAttribute("x2", sx(snap));
    cursor.setAttribute("visibility", "visible");
    tip.replaceChildren(h("div", { class: "tooltip-time" }, tooltipTime(snap)), ...rows);
    tip.hidden = false;
    const left = Math.min(Math.max(sx(snap) + 12, 0), W - 170);
    tip.style.left = `${left}px`;
    tip.style.top = `${M.t}px`;
  };
  const leave = () => { cursor.setAttribute("visibility", "hidden"); tip.hidden = true; };
  overlay.addEventListener("pointermove", move);
  overlay.addEventListener("pointerleave", leave);
  svg.append(overlay);
  wrap.append(svg, tip);
  return wrap;
}

function niceTimeStep(span) {
  const HOUR = 3_600_000, DAY = 24 * HOUR;
  const steps = [HOUR, 2 * HOUR, 3 * HOUR, 6 * HOUR, 12 * HOUR, DAY, 2 * DAY, 7 * DAY, 14 * DAY, 30 * DAY];
  return steps.find((st) => span / st <= 10) || 60 * DAY;
}

const STAGE_ROWS = ["awake", "rem", "light", "deep"];
const stageLabel = (st) => t({ awake: "Awake", rem: "REM", light: "Light sleep stage", deep: "Deep" }[st] || st);

/** Hypnogram from segments [{start, end, stage}] (ISO strings). */
export function hypnogram({ width, height = 150, segments, from, to }) {
  const wrap = h("div", { class: "chart" });
  const closed = segments.filter((sg) => sg.end);
  if (!closed.length) {
    wrap.append(h("div", { class: "chart-empty", style: { height: `${height}px` } }, t("No sleep recorded for this night")));
    return wrap;
  }
  const W = width, H = height;
  const x0 = from ?? Date.parse(closed[0].start), x1 = to ?? Date.parse(closed.at(-1).end);
  const L = 56;
  const sx = (x) => L + ((x - x0) / (x1 - x0)) * (W - L - M.r);
  const rowH = (H - M.t - M.b) / STAGE_ROWS.length;
  const svg = s("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, class: "chart-svg" });
  STAGE_ROWS.forEach((st, i) => {
    svg.append(s("text", { x: L - 8, y: M.t + rowH * i + rowH / 2 + 4, class: "tick", "text-anchor": "end" }, stageLabel(st)));
    svg.append(s("line", { x1: L, x2: W - M.r, y1: M.t + rowH * (i + 1), y2: M.t + rowH * (i + 1), class: "grid" }));
  });
  const step = niceTimeStep(x1 - x0);
  let lastPx = -Infinity;
  for (let t = Math.ceil(x0 / step) * step; t <= x1; t += step) {
    const px = sx(t);
    if (px - lastPx < 50) continue;
    svg.append(s("text", { x: px, y: H - 6, class: "tick", "text-anchor": "middle" }, f.time(t)));
    lastPx = px;
  }
  for (const sg of closed) {
    const row = STAGE_ROWS.indexOf(sg.stage);
    if (row < 0) continue;
    const a = sx(Date.parse(sg.start)), z = sx(Date.parse(sg.end));
    const mins = (Date.parse(sg.end) - Date.parse(sg.start)) / 60000;
    svg.append(s("rect", { x: a, y: M.t + rowH * row + 2, width: Math.max(1, z - a), height: rowH - 4, rx: 2,
      class: `stage stage-${sg.stage}` },
      s("title", {}, `${stageLabel(sg.stage)} ${f.time(sg.start)}–${f.time(sg.end)} (${f.duration(mins)})`)));
  }
  wrap.append(svg);
  return wrap;
}

/** Coverage heatmap: rows [{label, values: {day: 0..1}, raw: {day: n}}], days: ["YYYY-MM-DD"]. */
export function heatmap({ width, rows, days, cell = null }) {
  const L = 150, top = 22;
  const cw = cell || Math.max(4, Math.floor((width - L - 4) / days.length));
  const ch = 16;
  const W = L + cw * days.length + 4, H = top + ch * rows.length + 4;
  const svg = s("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, class: "chart-svg heatmap" });
  const firstMonthStart = days.findIndex((d) => d.endsWith("-01"));
  days.forEach((d, i) => {
    const monthStart = d.endsWith("-01");
    const leading = i === 0 && (firstMonthStart < 0 || firstMonthStart * cw >= 60);
    if (monthStart || leading) svg.append(s("text", { x: L + i * cw, y: 12, class: "tick" }, f.shortDate(d)));
  });
  rows.forEach((r, j) => {
    svg.append(s("text", { x: L - 8, y: top + j * ch + 12, class: "tick", "text-anchor": "end" }, r.label));
    days.forEach((d, i) => {
      const v = r.values[d];
      svg.append(s("rect", { x: L + i * cw + 0.5, y: top + j * ch + 0.5, width: cw - 1, height: ch - 1, rx: 2,
        class: v == null ? "hm-empty" : "hm", style: v == null ? {} : { fillOpacity: 0.15 + 0.85 * Math.min(1, v) } },
        s("title", {}, `${r.label} · ${f.shortDate(d)}: ${v == null ? t("No data") : f.num(r.raw[d])}`)));
    });
  });
  return svg;
}

const addDaysIso = (iso, n) => {
  const d = new Date(iso + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
};

/**
 * Calendar heatmap (weeks as columns, Monday first). values: {"YYYY-MM-DD": number}; a day
 * without a value is shown as empty, never as zero activity. `describe(day, value)` → tooltip.
 */
export function calendarHeatmap({ width, from, to, values, describe, onSelect, levels = 4, classFor }) {
  const startDow = (new Date(from + "T00:00:00Z").getUTCDay() + 6) % 7; // Monday = 0
  const first = addDaysIso(from, -startDow);
  const days = [];
  for (let d = first; d <= to || days.length % 7; d = addDaysIso(d, 1)) days.push(d);
  const weeks = days.length / 7;
  const L = 26, T = 16;
  const cell = Math.max(9, Math.min(15, Math.floor((width - L - 4) / weeks)));
  const W = L + weeks * cell + 2, H = T + 7 * cell + 2;
  const max = Math.max(0, ...Object.values(values).filter((v) => v != null));
  const svg = s("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, class: "chart-svg cal-svg", role: "img" });

  const wd = new Intl.DateTimeFormat(locale(), { weekday: "short", timeZone: "UTC" });
  [0, 2, 4].forEach((row) => {
    const label = wd.format(new Date(addDaysIso("2024-01-01", row) + "T00:00:00Z")); // 2024-01-01 was a Monday
    svg.append(s("text", { x: 0, y: T + row * cell + cell - 3, class: "tick" }, label.replace(".", "")));
  });
  let lastMonth = null, lastLabelX = -Infinity;
  days.forEach((d, i) => {
    const col = Math.floor(i / 7), row = i % 7;
    const x = L + col * cell, y = T + row * cell;
    const month = d.slice(0, 7);
    if (row === 0 && month !== lastMonth && d >= from) {
      if (x - lastLabelX > 30) {
        svg.append(s("text", { x, y: 11, class: "tick" }, f.monthLabel(d)));
        lastLabelX = x;
      }
      lastMonth = month;
    }
    if (d < from) return;
    const v = values[d];
    const future = d > to;
    const level = v > 0 && max > 0 ? Math.max(1, Math.ceil((v / max) * levels)) : 0;
    svg.append(s("rect", {
      x: x + 1, y: y + 1, width: cell - 2, height: cell - 2, rx: 2,
      class: [future ? "cal-future" : level ? "cal-on" : "cal-empty", !future && classFor?.(d)].filter(Boolean).join(" "),
      style: { ...(level ? { fillOpacity: 0.2 + (0.8 * level) / levels } : {}), ...(onSelect && !future ? { cursor: "pointer" } : {}) },
      onclick: onSelect && !future ? () => onSelect(d) : null,
    }, s("title", {}, describe ? describe(d, v) : `${d}: ${v ?? "–"}`)));
  });
  const legend = h("div", { class: "cal-legend" }, h("span", {}, "–"), h("i", { class: "zero" }),
    ...Array.from({ length: levels }, (_, i) => h("i", { style: { opacity: 0.2 + (0.8 * (i + 1)) / levels } })), h("span", {}, "+"));
  return h("div", { class: "cal-wrap" }, svg, legend);
}

/**
 * Lab values over time. points: [{ t (ms), y, status, label }]; the lab reference and a personal target
 * are drawn as horizontal bands. `mini` draws a sparkline without axes.
 */
export function labChart({ width, height = 180, points, refLow, refHigh, targetMin, targetMax, format = (v) => f.num(v, 1), mini = false }) {
  const pts = points.filter((p) => p.y != null);
  if (!pts.length) return h("span", { class: "muted small" }, "–");
  const m = mini ? { l: 3, r: 3, t: 3, b: 3 } : M;
  const ys = pts.map((p) => p.y).concat([refLow, refHigh, targetMin, targetMax].filter((v) => v != null));
  let lo = Math.min(...ys), hi = Math.max(...ys);
  const pad = (hi - lo || Math.abs(hi) || 1) * 0.12;
  lo -= pad; hi += pad;
  if (Math.min(...pts.map((p) => p.y)) >= 0) lo = Math.max(0, lo);
  const yt = mini ? { lo, hi, ticks: [] } : niceTicks(lo, hi);
  const x0 = pts[0].t, x1 = pts.at(-1).t;
  const sx = (x) => (x1 === x0 ? (m.l + width - m.r) / 2 : m.l + ((x - x0) / (x1 - x0)) * (width - m.l - m.r));
  const sy = (y) => m.t + (1 - (y - yt.lo) / (yt.hi - yt.lo || 1)) * (height - m.t - m.b);
  const svg = s("svg", { width, height, viewBox: `0 0 ${width} ${height}`, class: "chart-svg", role: "img" });
  const band = (a, b, cls, label) => {
    if (a == null && b == null) return;
    const top = sy(Math.min(b ?? yt.hi, yt.hi)), bottom = sy(Math.max(a ?? yt.lo, yt.lo));
    if (bottom <= top) return;
    svg.append(s("rect", { x: m.l, y: top, width: width - m.l - m.r, height: bottom - top, class: cls }, s("title", {}, label)));
  };
  band(refLow, refHigh, "lab-ref", t("Lab reference"));
  band(targetMin, targetMax, "lab-target", t("Personal target"));
  for (const v of yt.ticks) {
    svg.append(s("line", { x1: m.l, x2: width - m.r, y1: sy(v), y2: sy(v), class: "grid" }));
    svg.append(s("text", { x: m.l - 6, y: sy(v) + 4, class: "tick", "text-anchor": "end" }, format(v)));
  }
  if (!mini) {
    let lastPx = -Infinity;
    for (const p of pts) {
      const px = sx(p.t);
      if (px - lastPx < 78) continue;
      const anchor = px > width - m.r - 40 ? "end" : px < m.l + 40 ? "start" : "middle";
      svg.append(s("text", { x: px, y: height - 6, class: "tick", "text-anchor": anchor }, f.numDate(p.date)));
      lastPx = px;
    }
  }
  svg.append(s("path", { class: "line", style: { stroke: "var(--accent)", strokeWidth: 1.6 },
    d: pts.map((p, i) => `${i ? "L" : "M"}${sx(p.t).toFixed(1)},${sy(p.y).toFixed(1)}`).join("") }));
  for (const p of pts) {
    svg.append(s("circle", { cx: sx(p.t), cy: sy(p.y), r: mini ? 2.2 : 4, class: ["lab-dot", p.status === "low" || p.status === "high" ? "off" : ""] },
      s("title", {}, p.label || format(p.y))));
  }
  return h("div", { class: ["chart", mini && "lab-spark"] }, svg);
}
