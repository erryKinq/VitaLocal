import { api } from "../api.js";
import { calendarHeatmap, timeChart } from "../charts.js";
import { drinksPanel, todayIso } from "../checkin.js";
import { burnedTitle, diaryCard, weightCard } from "../food.js";
import { goalSettings, goalsCard } from "../goals.js";
import { h, responsive } from "../dom.js";
import * as f from "../format.js";
import { t } from "../i18n.js";
import { icon } from "../icons.js";
import { card, dayNav, legend, menu, pageHead, stat, streak } from "../ui.js";

const DAY_MS = 86_400_000;
const dayRun = (n) => t(n === 1 ? "{n} day in a row" : "{n} days in a row", { n });
const HEATMAP_DAYS = 365;
const dayMs = (iso) => Date.parse(iso + "T00:00:00Z");
const isoOf = (ms) => new Date(ms).toISOString().slice(0, 10);

export async function nutritionView(ctx, requested) {
  const today = todayIso();
  const day = requested || today;
  const from30 = f.addDays(day, -29);
  const reload = () => ctx.refresh();
  const [d, diary, last30, year, burn30] = await Promise.all([
    api(`/api/v1/days/${day}`),
    api(`/api/v1/nutrition/${day}`),
    api("/api/v1/trends", { params: { metrics: "nutrition.energy,nutrition.drinks", from: from30, to: day } }),
    api("/api/v1/trends", { params: { metrics: "nutrition.energy,nutrition.drinks", from: f.addDays(today, -HEATMAP_DAYS + 1), to: today } }),
    api("/api/v1/energy", { params: { from: from30, to: day } }),
  ]);
  const drinkGoal = (d.goals || []).find((g) => g.metric === "nutrition.drinks");
  const weight = await weightCard(day, reload);

  return h("div", {},
    pageHead({ icon: "salad", title: t("Nutrition"), sub: day === today ? f.longDate(day) : null, actions: [
      dayNav({ day, today, go: (x) => ctx.navigate(x === today ? "#/nutrition" : `#/nutrition/${x}`), addDays: f.addDays, t, mediumDate: f.mediumDate }),
      menu([
        { label: t("Foods & templates"), icon: "tools-kitchen-2", href: "#/nutrition/foods" },
        { label: t("Edit goals"), icon: "target", onclick: () => goalSettings(d.goals, () => ctx.rerender()) },
      ]),
    ] }),
    h("div", { class: "grid-main" },
      diaryCard(day, diary, reload, d.goals, d.energy),
      h("div", { class: "stack" },
        card({ title: t("Drinks"), icon: "droplet" }, null, drinksPanel(day, d.drinks, drinkGoal)),
        goalsCard(d.goals, { settings: true, onChange: () => ctx.rerender() }),
        weight)),
    card({ title: t("History"), icon: "chart-line", collapsible: true, id: "nutrition.history", cls: "history-card",
      summary: t("Energy balance, drinking and logging") }, null,
      h("div", { class: "grid-2" },
        balanceCard(last30.metrics, burn30, from30, day),
        drinksChart(last30.metrics["nutrition.drinks"].days, from30, day, drinkGoal)),
      h("div", { style: { marginTop: "16px" } }, loggingCard(ctx, year.metrics, today))));
}

/**
 * Food of one day (day widget, nutrition page): totals, macro split and, with `details`, the meals.
 * Entries come from the food diary; days with imported daily totals only (Waistline CSV) show those.
 */
export function nutritionCard(d, { open = false, details = true } = {}) {
  const n = d.nutrition;
  const sm = d.summary;
  const burned = d.energy?.total ?? null;
  const edit = h("a", { href: d.date === todayIso() ? "#/nutrition" : `#/nutrition/${d.date}` }, n ? t("Edit") : t("Log food"));
  if (!n && !sm["nutrition.energy"]) {
    return card(t("Nutrition"), edit, h("div", { class: "muted" }, t("No food logged for this day.")));
  }
  // Totals: entries when present (with per-nutrient completeness), else the imported daily totals.
  const totals = n ? {
    kcal: n.totals.calories.value, protein: n.totals.proteins, carbs: n.totals.carbohydrates, fat: n.totals.fat,
    fiber: n.totals.fiber, sugars: n.totals.sugars, satFat: n.totals["saturated-fat"], salt: n.totals.salt,
  } : {
    kcal: sm["nutrition.energy"]?.value,
    protein: { value: sm["nutrition.protein"]?.value, missing: 0 }, carbs: { value: sm["nutrition.carbohydrates"]?.value, missing: 0 },
    fat: { value: sm["nutrition.fat"]?.value, missing: 0 }, fiber: { value: sm["nutrition.fiber"]?.value, missing: 0 },
    sugars: { value: sm["nutrition.sugars"]?.value, missing: 0 },
    satFat: { value: sm["nutrition.saturated_fat"]?.value, missing: 0 },
    salt: { value: sm["nutrition.salt"]?.value, missing: 0 },
  };
  const macroKcal = { protein: (totals.protein.value || 0) * 4, carbs: (totals.carbs.value || 0) * 4, fat: (totals.fat.value || 0) * 9 };
  const macroSum = macroKcal.protein + macroKcal.carbs + macroKcal.fat || 1;
  const pct = (k) => Math.round((macroKcal[k] / macroSum) * 100);
  const g = (x, label, decimals = 0) => h("span", {},
    h("span", { class: "muted" }, `${t(label)} `),
    h("b", {}, x.value == null ? "–" : `${f.num(x.value, decimals)} g`));

  return card(t("Nutrition"),
    h("span", { class: "toolbar small" }, h("span", { class: "muted" }, n ? t(n.entries === 1 ? "1 entry" : "{n} entries", { n: n.entries }) : t("Daily totals (Waistline CSV)")), edit),
    h("div", { class: "sleep-stats" },
      h("span", {}, h("span", { class: "muted" }, `${t("Intake")} `), h("b", {}, `${f.num(totals.kcal)} kcal`)),
      burned != null ? h("span", { title: burnedTitle(d.energy) },
        h("span", { class: "muted" }, t("Burned ≈ ")), h("b", {}, `${f.num(burned)} kcal`),
        h("span", { class: "muted" }, ` (${totals.kcal - burned >= 0 ? "+" : "−"}${f.num(Math.abs(totals.kcal - burned))})`)) : null),
    h("div", { class: "macro-bar", title: t("Share of energy from protein / carbohydrates / fat") },
      h("span", { style: { width: `${pct("protein")}%`, background: "var(--c-protein)" } }),
      h("span", { style: { width: `${pct("carbs")}%`, background: "var(--c-carbs)" } }),
      h("span", { style: { width: `${pct("fat")}%`, background: "var(--c-fat)" } })),
    legend([[`${t("Protein")} ${pct("protein")} %`, "var(--c-protein)"], [`${t("Carbs")} ${pct("carbs")} %`, "var(--c-carbs)"],
      [`${t("Fat")} ${pct("fat")} %`, "var(--c-fat)"]]),
    h("div", { class: "sleep-stats", style: { marginTop: "10px" } },
      g(totals.protein, "Protein"), g(totals.carbs, "Carbs"), g(totals.fat, "Fat"), g(totals.fiber, "Fiber", 1), g(totals.sugars, "Sugars"),
      g(totals.satFat, "Saturated fat", 1), g(totals.salt, "Salt", 1)),
    !details ? null : n ? n.meals.map((m) => h("details", { class: "meal", open: open && n.meals.length <= 4 },
      h("summary", {}, h("b", {}, m.meal || t("Unassigned")),
        h("span", { class: "muted small" }, t("{n} items · {kcal} kcal", { n: m.items.length, kcal: f.num(m.calories) }))),
      h("div", { class: "table-wrap" }, h("table", {}, h("tbody", {}, m.items.map((it) => h("tr", {},
        h("td", {}, it.name, it.brand ? h("span", { class: "muted small" }, ` · ${it.brand}`) : null),
        h("td", { class: "r muted" }, `${f.num(it.amount * (it.quantity || 1), it.amount % 1 ? 1 : 0)} ${it.unit || ""}`),
        h("td", { class: "r" }, it.calories != null ? `${f.num(it.calories)} kcal` : "–"))))))))
      : h("p", { class: "muted small" }, t("Only daily totals imported (Waistline diary CSV)."), " ",
        t("Foods without a value count as 0.")));
}

function daysBetween(from, to) {
  const out = [];
  for (let d = from; d <= to; d = f.addDays(d, 1)) out.push(d);
  return out;
}

function balanceCard(m, burn, from, to) {
  const byDay = (k) => Object.fromEntries((m[k]?.days || []).map((x) => [x.day, x.value]));
  const intake = byDay("nutrition.energy");
  const days = daysBetween(from, to);
  const intakePts = days.map((d) => [dayMs(d), intake[d] ?? null]);
  const burnedPts = days.map((d) => [dayMs(d) + DAY_MS / 2, burn[d]?.total ?? null]);
  const watchOnly = Object.values(burn).every((b) => b.source === "watch");
  const burnedLabel = watchOnly ? t("Burned (watch)") : t("Burned (≈ estimated)");
  return card({ title: t("Energy intake vs. burned"), cls: "flat" }, legend([[t("Intake"), "var(--accent)"], [burnedLabel, "var(--c-hr)"]]),
    responsive(h("div"), (w) => timeChart({
      width: w, height: 200, xMin: dayMs(from), xMax: dayMs(to) + DAY_MS, yMin: 0,
      xLabel: (x) => f.shortDate(isoOf(x)), tooltipTime: (x) => f.longDate(isoOf(x)),
      series: [
        { label: t("Intake"), color: "var(--accent)", type: "bars", barWidth: DAY_MS, points: intakePts, format: (v) => `${f.num(v)} kcal` },
        { label: burnedLabel, color: "var(--c-hr)", points: burnedPts, gap: 1.5 * DAY_MS, strokeWidth: 2, format: (v) => `${f.num(v)} kcal` },
      ],
    })));
}

function drinksChart(rows, from, to, goal) {
  const byDay = Object.fromEntries(rows.map((x) => [x.day, x.value]));
  const pts = daysBetween(from, to).map((d) => [dayMs(d), byDay[d] ?? null]);
  // the selected day's goal as a reference (the formula goal varies a little from day to day)
  const goalLine = goal ? [{ label: t("Goal of the day"), color: "var(--ok)", strokeWidth: 1.5,
    points: [[dayMs(from), goal.min_value], [dayMs(to) + DAY_MS, goal.min_value]], format: (v) => `${f.num(v)} ml` }] : [];
  return card({ title: t("Drinking"), cls: "flat" }, goal ? legend([[t("Drinks"), "var(--accent)"], [t("Goal of the day"), "var(--ok)"]])
    : h("span", { class: "muted small" }, t("Last 30 days")),
    responsive(h("div"), (w) => timeChart({
      width: w, height: 200, xMin: dayMs(from), xMax: dayMs(to) + DAY_MS, yMin: 0,
      xLabel: (x) => f.shortDate(isoOf(x)), tooltipTime: (x) => f.longDate(isoOf(x)),
      series: [{ label: t("Drinks"), color: "var(--accent)", type: "bars", barWidth: DAY_MS, points: pts, format: (v) => `${f.num(v)} ml` },
        ...goalLine],
    })));
}

function loggingCard(ctx, m, today) {
  const food = Object.fromEntries((m["nutrition.energy"]?.days || []).map((x) => [x.day, 1]));
  const drinks = Object.fromEntries((m["nutrition.drinks"]?.days || []).map((x) => [x.day, 1]));
  const values = {};
  for (const d of new Set([...Object.keys(food), ...Object.keys(drinks)])) values[d] = (food[d] || 0) + (drinks[d] || 0);
  const from = f.addDays(today, -HEATMAP_DAYS + 1);
  const last30 = Object.keys(food).filter((d) => d >= f.addDays(today, -29)).length;
  return card({ title: t("Food logging"), cls: "flat" }, h("span", { class: "muted small" }, t("Days with food logged")),
    h("div", { class: "stat-row" },
      stat(t("Current streak"), dayRun(streak(today, (d) => !!food[d], f.addDays))),
      stat(t("Last 30 days"), t("{n} of the last {days} days", { n: last30, days: 30 }))),
    responsive(h("div"), (w) => calendarHeatmap({
      width: w, from, to: today, values, levels: 2,
      describe: (d) => `${f.longDate(d)}: ${[food[d] && t("Nutrition"), drinks[d] && t("Drinks")].filter(Boolean).join(" + ") || "–"}`,
      onSelect: (d) => ctx.navigate(`#/nutrition/${d}`),
    })));
}
