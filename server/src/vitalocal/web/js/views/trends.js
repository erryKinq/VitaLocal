import { api } from "../api.js";
import { timeChart } from "../charts.js";
import { h, responsive } from "../dom.js";
import * as f from "../format.js";
import { t } from "../i18n.js";
import { icon } from "../icons.js";
import { card, legend, pageHead, stat, statTile } from "../ui.js";

// The overview: a few values that tell how things are going; everything else by category.
const OVERVIEW = ["daily.sleep_score", "daily.hrv_last_night_avg", "resting_heart_rate", "steps", "stress", "weight", "nutrition.energy", "nutrition.drinks"];
// Which direction is good, for colouring the change (others stay neutral).
const BETTER = { "daily.sleep_score": 1, "daily.hrv_last_night_avg": 1, resting_heart_rate: -1, steps: 1, stress: -1, active_energy: 1,
  body_energy: 1, "daily.sleep_duration_score": 1, mood: 1, energy: 1 };
const GROUP_ICONS = { overview: "layout-grid", sleep: "moon", heart: "heart", body: "scale", activity: "run", nutrition: "salad", journal: "mood-smile" };
const RANGES = [["30", "30 days"], ["90", "90 days"], ["180", "180 days"], ["365", "1 year"], ["all", "All"]];
const GROUPS = { overview: "Overview", sleep: "Sleep", heart: "Heart", body: "Body", activity: "Activity", nutrition: "Nutrition", journal: "Check-ins" };
const RECENT = 7;
const DAY = 86_400_000;
const ROLL = 7;

const dayMs = (iso) => Date.parse(iso + "T00:00:00Z");
const isoOf = (ms) => new Date(ms).toISOString().slice(0, 10);

export async function trendsView(ctx) {
  const catalog = Object.fromEntries(ctx.meta.metrics.map((m) => [m.key, m]));
  const group = GROUPS[ctx.params.get("c")] ? ctx.params.get("c") : "overview";
  const selected = (ctx.params.get("m")?.split(",") ?? []).filter((k) => catalog[k]);
  const range = ctx.params.get("range") || "90";
  const to = ctx.meta.last_day;
  if (!to) return h("div", { class: "card" }, t("No data yet."));
  const from = range === "all" ? ctx.meta.first_day : [f.addDays(to, -Number(range) + 1), ctx.meta.first_day].sort().at(-1);

  const go = (c, m, r) => ctx.navigate(`#/stats?c=${c}${m.length ? `&m=${encodeURIComponent(m.join(","))}` : ""}&range=${r}`);
  const toggle = (k) => go(group, selected.includes(k) ? selected.filter((x) => x !== k) : [...selected, k], range);

  const tileKeys = (group === "overview" ? OVERVIEW : ctx.meta.metrics.filter((m) => m.group === group).map((m) => m.key)).filter((k) => catalog[k]);
  const keys = [...new Set([...tileKeys, ...selected])];
  const data = keys.length ? await api("/api/v1/trends", { params: { metrics: keys.join(","), from, to } }) : { metrics: {} };
  const allDays = [];
  for (let d = from; d <= to; d = f.addDays(d, 1)) allDays.push(d);

  return h("div", { class: "area-stats" },
    pageHead({ icon: "chart-line", title: t("Statistics"),
      sub: t("{from} – {to}", { from: f.shortDate(from), to: f.shortDate(to) }),
      actions: [h("div", { class: "segmented" }, RANGES.map(([r, label]) =>
        h("button", { class: r === range && "active", onclick: () => go(group, selected, r) }, t(label))))] }),
    h("div", { class: "segmented section-nav" }, Object.entries(GROUPS).map(([g, label]) =>
      h("button", { class: g === group && "active", onclick: () => go(g, selected, range) }, icon(GROUP_ICONS[g], { size: 16 }), t(label)))),
    h("div", { class: "tiles stat-tiles" }, tileKeys.map((k) => metricTile(catalog[k], data.metrics[k]?.days ?? [], selected.includes(k), () => toggle(k)))),
    h("div", { class: "muted small", style: { margin: "8px 0 16px" } },
      t("Value: mean of the last {n} measured days · change against the mean of the period. A tile opens its chart.", { n: RECENT })),
    selected.length ? h("div", { class: "grid-2" }, selected.map((k) => metricCard(catalog[k], data.metrics[k]?.days ?? [], allDays)))
      : h("div", { class: "empty" }, icon("chart-line", { size: 28 }), h("span", { class: "small" }, t("Pick a value above to see its course."))));
}

/** One metric as a dense tile: recent mean, change against the period, sparkline. */
function metricTile(def, days, active, onclick) {
  const decimals = def.decimals ?? 0;
  const values = days.map((d) => d.value);
  const recent = values.slice(-RECENT);
  const mean = (v) => (v.length ? v.reduce((a, b) => a + b, 0) / v.length : null);
  const now = mean(recent), all = mean(values);
  const diff = now != null && all != null ? now - all : null;
  const same = diff == null || Math.abs(diff) < 10 ** -decimals / 2;
  const dir = BETTER[def.key];
  return statTile({
    label: t(def.label), value: now == null ? null : f.num(now, decimals), unit: def.unit && !["clock", "score", "steps"].includes(def.unit) ? def.unit : "",
    delta: diff == null ? t("no values") : same ? t("= period mean") : `${diff > 0 ? "▲" : "▼"} ${f.num(Math.abs(diff), decimals)} ${t("vs. period")}`,
    good: same || !dir ? null : (diff > 0) === (dir > 0),
    spark: values.slice(-60), onclick, active,
  });
}

function metricCard(def, days, allDays) {
  const byDay = Object.fromEntries(days.map((d) => [d.day, d.value]));
  const points = allDays.map((d) => [dayMs(d), byDay[d] ?? null]);
  const values = days.map((d) => d.value);
  const decimals = def.decimals ?? 0;
  const fmt = (v) => `${f.num(v, decimals)}${def.unit ? ` ${def.unit}` : ""}`;
  const rolling = rollingMean(points);
  const isSum = def.daily === "sum";
  const label = t(def.label);
  const dailyLabel = { sum: "Daily total", last: "Daily value", avg: "Daily avg", max: "Daily max", min: "Daily min" }[def.daily] || "Daily value";
  const stats = values.length ? {
    mean: values.reduce((a, b) => a + b, 0) / values.length,
    min: Math.min(...values), max: Math.max(...values),
  } : null;

  return card(label,
    legend([[t(dailyLabel), "var(--cat-stats)"], [t("{n}-day mean", { n: ROLL }), "var(--c-trend)"]]),
    stats ? h("div", { class: "sleep-stats" },
      stat(t("Mean"), f.num(stats.mean, decimals)),
      stat(t("Range"), `${f.num(stats.min, decimals)}–${f.num(stats.max, decimals)}`),
      stat(t("Days measured"), `${values.length}/${allDays.length}`)) : null,
    def.note ? h("div", { class: "muted small", style: { marginBottom: "6px" } }, t(def.note)) : null,
    responsive(h("div"), (w) => timeChart({
      width: w, height: 190, xMin: dayMs(allDays[0]), xMax: dayMs(allDays.at(-1)) + (isSum ? DAY : 0),
      xLabel: (x) => f.shortDate(isoOf(x)), tooltipTime: (x) => f.longDate(isoOf(x)), yFormat: (v) => f.num(v, decimals > 1 ? 1 : decimals),
      emptyText: t("No values in this range"),
      series: [
        isSum
          ? { label, color: "var(--cat-stats)", type: "bars", barWidth: DAY, points, format: fmt }
          : { label, color: "var(--cat-stats)", type: "dots", points, format: fmt },
        { label: t("{n}-day mean", { n: ROLL }), color: "var(--c-trend)", points: rolling, gap: 1.5 * DAY, strokeWidth: 2, format: fmt },
      ],
    })));
}

// Trailing mean over the last ROLL calendar days; needs at least 4 measured days, so gaps stay gaps.
function rollingMean(points) {
  return points.map(([t], i) => {
    const win = points.slice(Math.max(0, i - ROLL + 1), i + 1).map((p) => p[1]).filter((v) => v != null);
    return [t, win.length >= 4 ? win.reduce((a, b) => a + b, 0) / win.length : null];
  });
}
