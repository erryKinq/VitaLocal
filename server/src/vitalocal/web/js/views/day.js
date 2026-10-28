import { aiCard } from "../aiSummary.js";
import { aiOn, api, moduleOn } from "../api.js";
import { hypnogram, timeChart } from "../charts.js";
import { checkinList, drinksPanel, quickCheckin, todayIso } from "../checkin.js";
import { goalsCard } from "../goals.js";
import { h, responsive } from "../dom.js";
import * as f from "../format.js";
import { t } from "../i18n.js";
import { icon } from "../icons.js";
import { card, dayNav, iconBtn, legend, menu, sheet, stat } from "../ui.js";
import { gardenView } from "./habitRewards.js";
import { habitCheckList } from "./habits.js";
import { supplementRows } from "./health.js";
import { energyCard, readinessCard, recoveryCard, sleepRhythmCard, weeklyCard } from "./insights.js";
import { nutritionCard } from "./nutrition.js";

const HOUR = 3_600_000;
const BASELINE_DAYS = 28;

// KPI tiles in display order; `from` picks the value source.
const KPIS = [
  { key: "daily.sleep_score", label: "Sleep score", unit: "", icon: "moon" },
  { key: "asleep", icon: "zzz", label: "Time asleep", from: (d) => d.sleep.asleep_minutes, format: f.duration },
  { key: "daily.hrv_last_night_avg", icon: "activity", label: "HRV last night", unit: "ms" },
  { key: "resting_heart_rate", icon: "heart", label: "Resting HR", unit: "bpm" },
  { key: "steps", icon: "shoe", label: "Steps", unit: "" },
  { key: "active_energy", icon: "flame", label: "Active energy", unit: "kcal" },
  { key: "stress", icon: "bolt", label: "Avg. stress", unit: "" },
  { key: "body_energy", icon: "battery-3", label: "Body energy peak", unit: "" },
  { key: "intensity", icon: "run", label: "Intensity min.", unit: "min",
    from: (d) => sumOrNull(d.summary.intensity_minutes_moderate?.value, d.summary.intensity_minutes_vigorous?.value),
    sub: (d) => t("{mod} moderate · {vig} vigorous", {
      mod: f.num(d.summary.intensity_minutes_moderate?.value), vig: f.num(d.summary.intensity_minutes_vigorous?.value) }) },
  { key: "spo2", icon: "lungs", label: "Avg. SpO₂", unit: "%", decimals: 1 },
  { key: "respiratory_rate", icon: "wind", label: "Avg. respiration", unit: "brpm", decimals: 1 },
  { key: "daily.skin_temp_deviation", icon: "temperature", label: "Skin temp. deviation", unit: "°C", decimals: 2, signed: true },
  { key: "floors_climbed", icon: "stairs", label: "Floors climbed", unit: "", from: (d) => d.summary.floors_climbed?.value ?? (d.summary.steps ? 0 : null) },
  { key: "nutrition.energy", icon: "salad", label: "Energy intake", unit: "kcal" },
  // imperial: shown in lb (then without the kg baseline)
  { key: "weight", icon: "scale", label: "Weight", decimals: 1, get unit() { return f.weightUnit(); },
    get from() { return f.weightUnit() === "lb" ? (d) => f.weightValue(d.summary.weight?.value) : undefined; } },
];

function sumOrNull(...vals) {
  const v = vals.filter((x) => x != null);
  return v.length ? v.reduce((a, b) => a + b, 0) : null;
}

function median(values) {
  const v = values.filter((x) => x != null).sort((a, b) => a - b);
  if (!v.length) return null;
  const m = v.length >> 1;
  return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2;
}

// --- widgets -------------------------------------------------------------------------------------

// id -> title and render(env). `insight` widgets need GET /insights, loaded once per view.
const WIDGETS = {
  kpis: { icon: "layout-grid", group: "body", title: "Key values", render: (e) => h("div", { class: "kpis" },
    KPIS.filter((k) => moduleOn("watch") || NO_WATCH_KPIS.has(k.key)).map((k) => kpiTile(k, e.d, e.base))) },
  drinks: { icon: "droplet", group: "food", title: "Drinks", render: (e) => card(t("Drinks"), h("a", { href: `#/nutrition/${e.day}` }, t("Nutrition")), drinksPanel(e.day, e.d.drinks, drinkGoal(e.d))) },
  goals: { icon: "target", group: "food", title: "Goals", render: (e) => goalsCard(e.d.goals) },
  habits: { icon: "list-check", group: "habit", title: "Habits", render: (e) => habitsCard(e.day) },
  garden: { icon: "plant-2", group: "habit", title: "Habit garden", render: () => gardenWidget() },
  supplements: { icon: "pill", group: "habit", title: "Supplements", render: (e) => supplementsCard(e.day) },
  ai: { icon: "sparkles", group: "mind", title: "AI summary", render: (e) => aiCard(e.day) },
  sleep: { icon: "moon", group: "sleep", title: "Sleep", render: (e) => sleepCard(e.d) },
  nutrition: { icon: "salad", group: "food", title: "Nutrition", render: (e) => nutritionCard(e.d) },
  journal: { icon: "notebook", group: "mind", title: "Journal & check-ins", render: (e) => journalCard(e.ctx, e.day, e.d) },
  heart_rate: { icon: "heart", group: "body", title: "Heart rate", render: (e) => card(t("Heart rate"),
    legend([[t("Monitoring"), "var(--c-hr)"], [t("Workout recording"), "var(--c-hr-soft)"], [t("Activity"), "var(--band)"]]),
    responsive(h("div"), e.dayChart({ height: 200, series: [
      { label: t("Heart rate"), color: "var(--c-hr)", points: e.d.series.heart_rate, gap: 5 * 60_000, format: (v) => `${f.num(v)} bpm` },
      { label: t("Workout"), color: "var(--c-hr-soft)", points: e.d.series.workout_heart_rate, gap: 2 * 60_000, format: (v) => `${f.num(v)} bpm` },
    ] }))) },
  stress: { icon: "bolt", group: "body", title: "Stress & body energy", render: (e) => card(t("Stress & body energy"),
    legend([[t("Stress"), "var(--c-stress)"], [t("Body energy"), "var(--c-energy)"]]),
    responsive(h("div"), e.dayChart({ height: 180, yMin: 0, yMax: 100, series: [
      { label: t("Stress"), color: "var(--c-stress)", type: "area", points: e.d.series.stress, gap: 10 * 60_000 },
      { label: t("Body energy"), color: "var(--c-energy)", points: e.d.series.body_energy, gap: 10 * 60_000 },
    ] }))) },
  steps: { icon: "shoe", group: "body", title: "Steps per hour", render: (e) => card(t("Steps per hour"), h("span", { class: "muted small" }, t("{n} total", { n: f.num(e.d.summary.steps?.value) })),
    responsive(h("div"), e.dayChart({ height: 150, series: [
      { label: t("Steps"), color: "var(--c-steps)", type: "bars", barWidth: HOUR, points: perHour(e.d.series.steps, e.x0, e.x1) },
    ] }))) },
  spo2: { icon: "lungs", group: "body", title: "SpO₂", render: (e) => card("SpO₂", null, responsive(h("div"), e.dayChart({ height: 140, series: [
    { label: "SpO₂", color: "var(--c-spo2)", type: "dots", points: e.d.series.spo2, format: (v) => `${f.num(v)} %` },
  ] }))) },
  respiration: { icon: "wind", group: "body", title: "Respiration", render: (e) => card(t("Respiration"), null, responsive(h("div"), e.dayChart({ height: 140, series: [
    { label: t("Respiration"), color: "var(--c-resp)", points: e.d.series.respiratory_rate, gap: 10 * 60_000, format: (v) => `${f.num(v, 1)} brpm` },
  ] }))) },
  activities: { icon: "run", group: "train", title: "Activities", render: (e) => activitiesCard(e.d) },
  readiness: { icon: "gauge", group: "train", title: "Training readiness", insight: readinessCard },
  fitness: { icon: "activity", group: "train", title: "Fitness", render: (e) => fitnessCard(e.day) },
  sleep_rhythm: { icon: "clock", group: "sleep", title: "Sleep rhythm", insight: sleepRhythmCard },
  recovery: { icon: "refresh", group: "train", title: "Recovery after training", insight: recoveryCard },
  energy: { icon: "battery-3", group: "body", title: "Energy & activity", insight: energyCard },
  weekly: { icon: "calendar-stats", group: "body", title: "Weekly trend", insight: weeklyCard },
};

const drinkGoal = (d) => (d.goals || []).find((g) => g.metric === "nutrition.drinks");

// Widgets sit on a grid of 4 columns and fixed-height rows; `w` columns wide, `h` rows high. What
// does not fit scrolls inside the widget, and some cards show less when they are small.
const WIDTHS = [[1, "¼"], [2, "½"], [3, "¾"], [4, "1/1"]];
const HEIGHTS = [[1, "S"], [2, "M"], [3, "L"], [4, "XL"], [5, "XXL"], [6, "XXXL"]];
// default height per widget, so that its content fits at the default width
const DEFAULT_H = { kpis: 3, drinks: 3, habits: 3, garden: 2, supplements: 3, goals: 2, ai: 2, sleep: 5, nutrition: 3, journal: 3,
  heart_rate: 2, stress: 2, steps: 2, spo2: 2, respiration: 2, activities: 2, readiness: 3, sleep_rhythm: 3, fitness: 2,
  recovery: 3, energy: 3, weekly: 3 };
const w = (id, cols = 4) => ({ id, w: cols, h: DEFAULT_H[id] || 2 });
const heading = (n, text) => ({ id: `heading_${n}`, w: 4, h: 1, text });
const isHeading = (id) => id.startsWith("heading_");
/** A saved widget in the current form: layouts from before the grid only had size half/full. */
const normalize = (x) => isHeading(x.id) ? { id: x.id, w: 4, h: 1, text: x.text || "" }
  : { id: x.id, w: x.w || (x.size === "half" ? 2 : 4), h: x.h || DEFAULT_H[x.id] || 2 };
// Today is mostly about logging; past days lead with their measured values. Headings group the grid.
const defaultLayout = () => ({
  today: [w("drinks", 2), w("habits", 2), w("supplements", 2), w("garden", 2),
    heading(1, t("Body")), w("kpis"), w("readiness", 2), w("ai", 2),
    heading(2, t("Sleep & recovery")), w("sleep"), w("sleep_rhythm", 2), w("recovery", 2),
    heading(3, t("Over the day")), w("heart_rate"), w("stress"), w("steps"), w("spo2", 2), w("respiration", 2), w("activities"),
    heading(4, t("Nutrition & journal")), w("nutrition", 2), w("journal", 2), w("fitness", 2), w("energy", 2), w("weekly")],
  past: [heading(1, t("Body")), w("kpis"), w("readiness", 2), w("ai", 2),
    heading(2, t("Sleep & recovery")), w("sleep"), w("sleep_rhythm", 2), w("recovery", 2),
    heading(3, t("Over the day")), w("heart_rate"), w("stress"), w("steps"), w("spo2", 2), w("respiration", 2), w("activities"),
    heading(4, t("Logged")), w("drinks", 2), w("habits", 2), w("supplements", 2), w("nutrition", 2), w("journal", 2),
    w("fitness", 2), w("energy", 2), w("weekly")],
});

// What a widget needs: an area switched on, or an AI feature that is set up. Watch widgets show
// measurements only a watch (or Health Connect) provides.
const NEEDS = {
  ai: () => aiOn("summary"),
  drinks: () => moduleOn("drinks"), goals: () => moduleOn("nutrition"), nutrition: () => moduleOn("nutrition"),
  habits: () => moduleOn("habits"), garden: () => moduleOn("habits"), supplements: () => moduleOn("supplements"),
  journal: () => moduleOn("journal"), activities: () => moduleOn("training") || moduleOn("watch"),
  ...Object.fromEntries(["sleep", "heart_rate", "stress", "steps", "spo2", "respiration", "readiness", "fitness",
    "sleep_rhythm", "recovery"].map((id) => [id, () => moduleOn("watch")])),
};
const widgetOn = (id) => isHeading(id) || (!!WIDGETS[id] && (NEEDS[id]?.() ?? true));
// KPI tiles that work without a watch
const NO_WATCH_KPIS = new Set(["nutrition.energy", "weight"]);

/** Removes headings left without a widget below them. */
function dropEmptyHeadings(list) {
  return list.filter((x, i) => !isHeading(x.id) || (list[i + 1] && !isHeading(list[i + 1].id)));
}

async function loadLayout() {
  let layout;
  try {
    const saved = await api("/api/v1/settings/day-layout");
    // widgets that no longer exist are dropped; new ones stay available under "Add widget"
    layout = saved ? { today: saved.today.map(normalize), past: saved.past.map(normalize) } : defaultLayout();
  } catch {
    layout = defaultLayout();
  }
  const clean = (list) => dropEmptyHeadings(list.filter((x) => widgetOn(x.id)));
  return { today: clean(layout.today), past: clean(layout.past) };
}

// --- view ------------------------------------------------------------------------------------------

export async function dayView(ctx, requested) {
  const today = todayIso();
  const day = requested || today;
  const baselineKeys = KPIS.filter((k) => !k.from).map((k) => k.key);
  const [d, base, layout] = await Promise.all([
    api(`/api/v1/days/${day}`),
    api("/api/v1/trends", { params: { metrics: baselineKeys.join(","), from: f.addDays(day, -BASELINE_DAYS), to: f.addDays(day, -1) } }),
    loadLayout(),
  ]);

  const x0 = Date.parse(d.window.start), x1 = Date.parse(d.window.end);
  const activityBands = d.activities.map((a) => ({
    from: Date.parse(a.start_ts), to: a.end_ts ? Date.parse(a.end_ts) : Date.parse(a.start_ts) + (a.elapsed_s || 0) * 1000,
    label: a.name || f.humanize(a.sport) || t("Activity"),
  }));
  let insights = null;
  const env = {
    ctx, day, today, d, base, x0, x1,
    dayChart: (opts) => (width) => timeChart({ width, xMin: x0, xMax: x1, xTickEvery: 3 * HOUR, bands: activityBands, ...opts }),
    insights: () => (insights ??= api("/api/v1/insights", { params: { day } })),
  };
  const coverage = d.coverage.heart_rate_minutes / d.coverage.minutes_in_day;
  const board = dashboard(env, layout, day === today ? "today" : "past");
  const news = h("div", { hidden: true });
  if (day === today) {
    followNewData(ctx, () => busy(board), () => {
      news.replaceChildren(h("div", { class: "notice accent", style: { marginBottom: "16px" } },
        icon("refresh"), h("span", { class: "grow" }, t("New watch data arrived.")),
        h("button", { onclick: () => ctx.refresh() }, t("Refresh"))));
      news.hidden = false;
    });
  }
  return h("div", {},
    header(ctx, day, d, board.toggleEdit, coverage),
    news,
    board.node);
}

function header(ctx, day, d, onEdit, coverage) {
  const today = todayIso();
  const latest = ctx.meta.last_day;
  return h("div", { class: "page-head" },
    h("div", { class: "page-title-block" },
      h("h1", { class: "page-title" }, icon("home", { size: 22 }), t("Overview")),
      h("div", { class: "toolbar small muted" }, day === today ? `${t("Today")} · ${f.longDate(day)}` : `${f.longDate(day)} · ${t("Day boundaries: {tz}", { tz: ctx.meta.timezone })}`,
        statusPill(day, today, d.sync, coverage))),
    dayNav({
      day, today, min: ctx.meta.first_day, go: (x) => ctx.navigate(x === today ? "#/" : `#/day/${x}`), addDays: f.addDays, t, mediumDate: f.mediumDate,
      extra: [
        latest && latest !== today && day !== latest
          ? h("button", { title: t("Latest day with watch data"), onclick: () => ctx.navigate(`#/day/${latest}`) }, t("Latest")) : null,
        h("button", { class: "edit-toggle", title: t("Customize this page"), onclick: onEdit }, icon("layout-dashboard", { size: 16 }), t("Customize")),
      ],
    }));
}

const SOURCE_NAMES = {
  gadgetbridge_export: "Gadgetbridge", gadgetbridge_db: "Gadgetbridge", gadgetbridge_files: "Gadgetbridge",
  waistline_export: "Waistline", waistline_diary_csv: "Waistline",
};
const POLL_MS = 60_000;

/**
 * Today's page follows new data: every minute (while visible) the import status is checked, and
 * when an import finished the page is redrawn in place. While something is being edited, a
 * link offers the refresh instead of redrawing under the user's hands.
 */
function followNewData(ctx, isBusy, offer) {
  let version = null;
  let timer = null;
  const stop = () => { clearTimeout(timer); document.removeEventListener("visibilitychange", onVisible); };
  const tick = async () => {
    clearTimeout(timer);
    if (!ctx.isCurrent()) return stop();
    // the first check right after drawing sets the baseline, also on a hidden tab
    if (version === null || document.visibilityState === "visible") {
      try {
        const s = await api("/api/v1/ingest/status");
        if (version === null) version = s.data_version;
        else if (s.data_version !== version) {
          version = s.data_version;
          if (!isBusy()) { stop(); ctx.refresh(); return; }
          offer();
        }
      } catch { /* offline or server restarting: next minute */ }
    }
    timer = setTimeout(tick, POLL_MS);
  };
  // back on the tab (phone unlocked, window switched): check at once instead of within a minute
  const onVisible = () => { if (document.visibilityState === "visible") tick(); };
  document.addEventListener("visibilitychange", onVisible);
  tick();
}

function busy(board) {
  const el = document.activeElement;
  return board.isEditing() || (el && el !== document.body && el.closest("#app") && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName))
    || !!document.querySelector("#app details[open]");
}

/**
 * Data freshness as a pill (as in the app): green while watch data is recent, amber when it is
 * older than an hour or the heart rate covers less than 80 % of a past day. A click lists the
 * last upload per source.
 */
function statusPill(day, today, sync, coverage) {
  if (!moduleOn("watch")) return null; // no watch: there is nothing to be complete or late
  const lines = [];
  let warn = false, label;
  if (day < today) {
    warn = coverage < 0.8;
    label = warn ? t("Heart rate {pct}%", { pct: Math.round(coverage * 100) }) : t("Complete day");
    if (warn) lines.push(t("Heart rate covers {pct}% of this day. Daily totals may be incomplete.", { pct: Math.round(coverage * 100) }));
  } else if (sync?.last_heart_rate_at) {
    const at = sync.last_heart_rate_at;
    warn = Date.now() - Date.parse(at) > HOUR;
    label = t("Watch {ago}", { ago: f.ago(at) });
    lines.push(t("Watch data until {t}", { t: f.dateTime(at) }));
  } else {
    label = t("No watch data yet");
    warn = true;
  }
  const latest = {};
  for (const [src, b] of Object.entries(sync?.sources || {})) {
    const name = SOURCE_NAMES[src] || src;
    if (!latest[name] || b.received_at > latest[name].received_at) latest[name] = b;
  }
  for (const [name, b] of Object.entries(latest)) {
    if (b.status === "failed") warn = true;
    lines.push(`${t("{src} sync {ago}", { src: name, ago: f.ago(b.received_at) })}${b.status === "failed" ? ` (${t("failed")})` : ""}`
      + ` · ${t("Received {t}", { t: f.dateTime(b.received_at) })}`);
  }
  const pill = h("button", { type: "button", class: ["pill", warn ? "warn" : "ok"], title: lines.join("\n") }, h("span", { class: "dot" }), label);
  return lines.length ? menu(lines.map((l) => ({ heading: l })), { trigger: pill, left: true }) : pill;
}

// --- dashboard with edit mode ------------------------------------------------------------------------

const GROUPS = [["body", "Body", "activity"], ["sleep", "Sleep", "moon"], ["food", "Nutrition", "salad"],
  ["train", "Training", "barbell"], ["habit", "Habits & supplements", "plant-2"], ["mind", "Journal & AI", "notebook"]];
const AREA = { body: "day", sleep: "sleep", food: "food", train: "train", habit: "habit", mind: "mind" };

/** Gives a widget's card its icon and area colour, whichever module drew it. */
function decorate(el, def) {
  el.classList.add(`area-${AREA[def.group] || "day"}`);
  const h2 = el.querySelector(".card-head h2");
  if (h2 && !h2.querySelector(".icon")) h2.prepend(icon(def.icon));
  return el;
}

function dashboard(env, layout, mode) {
  const node = h("div", { class: "dashboard-wrap" });
  const rendered = new Map(); // widget id -> node, so reordering does not rebuild charts
  let editing = false;
  let editMode = mode; // which layout the editor works on

  const widgetNode = (id) => {
    if (!rendered.has(id)) {
      const def = WIDGETS[id];
      let el;
      if (def.insight) {
        el = h("div", {}, card(t(def.title), null, h("div", { class: "muted small" }, t("Loading …"))));
        decorate(el, def);
        env.insights().then((ins) => { el.replaceChildren(def.insight(ins)); decorate(el, def); })
          .catch((e) => el.replaceChildren(card(t(def.title), null, h("div", { class: "error" }, e.message))));
      } else {
        el = h("div", {}, def.render(env));
        decorate(el, def);
      }
      rendered.set(id, el);
    }
    return rendered.get(id);
  };

  const status = h("span", { class: "small muted" });
  const save = async () => {
    try { await api("/api/v1/settings/day-layout", { method: "PUT", json: layout }); status.textContent = ""; }
    catch (e) { status.textContent = t("Not saved: {msg}", { msg: e.message }); }
  };
  const change = () => { save(); draw(); };

  const move = (from, to) => {
    const list = layout[editMode];
    if (to < 0 || to >= list.length || from === to) return;
    const [item] = list.splice(from, 1);
    list.splice(to, 0, item);
    change();
  };

  const dnd = (el, i) => {
    el.draggable = true;
    el.addEventListener("dragstart", (e) => { e.dataTransfer.setData("text/plain", String(i)); el.classList.add("dragging"); });
    el.addEventListener("dragend", () => el.classList.remove("dragging"));
    el.addEventListener("dragover", (e) => { e.preventDefault(); el.classList.add("drop-target"); });
    el.addEventListener("dragleave", () => el.classList.remove("drop-target"));
    el.addEventListener("drop", (e) => { e.preventDefault(); move(Number(e.dataTransfer.getData("text/plain")), i); });
  };

  // Headings split the page into sections; each section is its own grid, so a heading stays one line.
  const draw = () => {
    const list = layout[editMode];
    const out = [];
    let grid = null;
    const newGrid = () => { grid = h("div", { class: ["dashboard", editing && "editing"] }); out.push(grid); };
    list.forEach((item, i) => {
      if (isHeading(item.id)) {
        const el = headingNode(item, i, list.length);
        if (editing) dnd(el, i);
        out.push(el);
        grid = null;
        return;
      }
      if (!grid) newGrid();
      const cell = h("div", { class: ["widget", `w-${item.w}`, `h-${item.h}`], "data-id": item.id },
        editing ? editBar(item, i, list.length) : null, h("div", { class: "widget-body" }, widgetNode(item.id)));
      if (editing) dnd(cell, i);
      grid.append(cell);
    });
    node.replaceChildren(...(editing ? [editorBar(), ...out] : out));
  };

  function headingNode(item, i, n) {
    if (!editing) return h("h2", { class: "dash-heading" }, item.text);
    const input = h("input", { type: "text", value: item.text, maxlength: 60, "aria-label": t("Heading"),
      onchange: (e) => { item.text = e.target.value.trim(); save(); } });
    return h("div", { class: "dash-heading editing" },
      h("span", { class: "drag-handle", title: t("Drag to move") }, icon("grip-vertical")), input,
      itemMenu(i, n, null));
  }

  function itemMenu(i, n, item) {
    const list = layout[editMode];
    const sizes = item ? [
      { heading: t("Width") }, ...WIDTHS.map(([v, l]) => ({ label: `${l}${v === item.w ? " ✓" : ""}`, icon: "arrows-move", onclick: () => { item.w = v; change(); } })),
      { heading: t("Height") }, ...HEIGHTS.map(([v, l]) => ({ label: `${l}${v === item.h ? " ✓" : ""}`, icon: "arrows-move", onclick: () => { item.h = v; change(); } })),
      "-",
    ] : [];
    return menu([
      ...sizes,
      i > 0 ? { label: t("Move up"), icon: "arrow-up", onclick: () => move(i, i - 1) } : null,
      i < n - 1 ? { label: t("Move down"), icon: "arrow-down", onclick: () => move(i, i + 1) } : null,
      { label: t("Remove"), icon: "trash", danger: true, onclick: () => { list.splice(i, 1); change(); } },
    ]);
  }

  function editBar(item, i, n) {
    const def = WIDGETS[item.id];
    return h("div", { class: "widget-bar" },
      h("span", { class: "drag-handle", title: t("Drag to move") }, icon("grip-vertical")),
      icon(def.icon, { size: 16 }),
      h("b", { class: "small widget-title", title: t(def.title) }, t(def.title)),
      h("span", { class: "small muted" }, `${WIDTHS.find(([v]) => v === item.w)?.[1]} · ${HEIGHTS.find(([v]) => v === item.h)?.[1]}`),
      itemMenu(i, n, item));
  }

  /** The widget catalogue: grouped by area, shown ones marked, a click adds to the top. */
  function catalogue() {
    const list = layout[editMode];
    const shown = new Set(list.map((x) => x.id));
    const sh = sheet({ title: t("Add widget"), icon: "layout-dashboard", wide: true, body: [
      ...GROUPS.map(([g, label, ic]) => {
        const ids = Object.keys(WIDGETS).filter((id) => WIDGETS[id].group === g && widgetOn(id));
        if (!ids.length) return null;
        return h("div", { class: `area-${AREA[g]}` },
          h("div", { class: "section-title" }, icon(ic, { size: 16 }), t(label)),
          h("div", { class: "catalogue" }, ids.map((id) => h("button", { type: "button", class: ["cat-item", shown.has(id) && "shown"], disabled: shown.has(id),
            onclick: () => { list.unshift(w(id)); change(); sh.close(); } },
            icon(WIDGETS[id].icon, { size: 22 }), h("span", {}, t(WIDGETS[id].title)),
            shown.has(id) ? icon("check", { size: 16, cls: "muted" }) : null))));
      }),
      h("div", {}, h("div", { class: "section-title" }, icon("heading", { size: 16 }), t("Structure")),
        h("button", { type: "button", class: "cat-item", onclick: () => {
          const n = Math.max(0, ...list.filter((x) => isHeading(x.id)).map((x) => Number(x.id.slice(8)) || 0)) + 1;
          list.unshift(heading(n, t("New section"))); change(); sh.close();
        } }, icon("heading", { size: 22 }), h("span", {}, t("Section heading")))),
    ] });
  }

  function editorBar() {
    return h("div", { class: "card editor-bar" },
      h("div", { class: "toolbar" },
        h("b", {}, t("Customize")),
        h("div", { class: "segmented" }, [["today", t("Today")], ["past", t("Past days")]].map(([m, label]) =>
          h("button", { class: m === editMode && "active", onclick: () => { editMode = m; draw(); } }, label))),
        h("button", { onclick: catalogue }, icon("plus", { size: 16 }), t("Add widget")),
        h("span", { style: { flex: 1 } }),
        status,
        h("button", { class: "ghost", onclick: async () => {
          if (!confirm(t("Reset both layouts to the default?"))) return;
          Object.assign(layout, defaultLayout());
          await api("/api/v1/settings/day-layout", { method: "DELETE" }).catch(() => {});
          draw();
        } }, t("Reset")),
        h("button", { class: "primary", onclick: () => toggleEdit() }, icon("check", { size: 16 }), t("Done"))),
      h("div", { class: "small muted" }, editMode !== mode
        ? t("Preview of the other layout with this day's data.")
        : t("Drag widgets or use their menu; changes are saved at once and apply in every browser.")));
  }

  function toggleEdit() {
    editing = !editing;
    editMode = mode;
    draw();
  }

  draw();
  return { node, toggleEdit, isEditing: () => editing };
}

// --- cards ---------------------------------------------------------------------------------------------

function kpiTile(k, d, base) {
  const s = d.summary[k.key];
  const value = k.from ? k.from(d) : s?.value;
  const decimals = k.decimals ?? s?.decimals ?? 0;
  const unit = k.unit ?? s?.unit;
  const shown = value == null ? "–" : k.format ? k.format(value) : `${k.signed && value > 0 ? "+" : ""}${f.num(value, decimals)}`;

  let delta = k.sub ? k.sub(d) : null;
  const history = base.metrics[k.key]?.days;
  if (!delta && history && value != null) {
    const med = median(history.map((x) => x.value));
    if (med != null && history.length >= 7) {
      const diff = value - med;
      const arrow = Math.abs(diff) < 10 ** -decimals / 2 ? "=" : diff > 0 ? "▲" : "▼";
      delta = t("{arrow} {diff} vs. {days}-day median {med}", {
        arrow, diff: f.num(Math.abs(diff), decimals), days: BASELINE_DAYS, med: f.num(med, decimals) });
    } else {
      delta = t("Baseline needs ≥7 days ({n})", { n: history.length });
    }
  }
  return h("div", { class: ["card", "kpi", value == null && "missing"], title: value == null ? t("No measurement for this day") : "" },
    h("div", { class: "label" }, k.icon ? icon(k.icon, { size: 14 }) : null, t(k.label)),
    h("div", { class: "value" }, shown, value != null && unit && !k.format ? h("small", {}, unit) : null),
    h("div", { class: "delta" }, value == null ? t("not measured") : delta || ""));
}

function perHour(points, x0, x1) {
  const out = [];
  for (let x = x0; x < x1; x += HOUR) out.push([x, null]);
  for (const [x, v] of points) {
    const i = Math.floor((x - x0) / HOUR);
    if (i >= 0 && i < out.length) out[i][1] = (out[i][1] ?? 0) + v;
  }
  return out;
}

function sleepCard(d) {
  const sl = d.sleep;
  const segs = sl.segments.filter((s) => s.end);
  const from = segs.length ? Date.parse(segs[0].start) - 15 * 60_000 : null;
  const to = segs.length ? Date.parse(segs.at(-1).end) + 15 * 60_000 : null;
  const m = sl.minutes;
  return card(t("Sleep"),
    legend([[t("Deep"), "var(--c-sleep-deep)"], [t("Light sleep stage"), "var(--c-sleep-light)"], [t("REM"), "var(--c-sleep-rem)"], [t("Awake"), "var(--c-sleep-awake)"]]),
    segs.length ? h("div", { class: "sleep-stats" },
      stat(t("Asleep"), f.duration(sl.asleep_minutes)),
      stat(t("Deep"), f.duration(m.deep)), stat(t("Light sleep stage"), f.duration(m.light)), stat(t("REM"), f.duration(m.rem)),
      stat(t("Awake"), f.duration(m.awake ?? 0)),
      stat(t("In bed"), `${f.time(segs[0].start)}–${f.time(segs.at(-1).end)}`),
      d.summary["daily.sleep_score"] ? stat(t("Score"), f.num(d.summary["daily.sleep_score"].value)) : null) : null,
    responsive(h("div"), (w) => hypnogram({ width: w, segments: sl.segments, from, to })),
    sleepScores(d.summary),
    sl.hrv.length ? h("div", { style: { marginTop: "12px" } },
      h("div", { class: "muted small", style: { marginBottom: "4px" } }, t("Overnight HRV (RMSSD)")),
      responsive(h("div"), (w) => timeChart({ width: w, height: 110, xMin: from ?? undefined, xMax: to ?? undefined,
        series: [{ label: "HRV", color: "var(--c-hrv)", points: sl.hrv, gap: 15 * 60_000, format: (v) => `${f.num(v)} ms` }] }))) : null,
    sl.skin_temp?.length && from ? h("div", { style: { marginTop: "12px" } },
      h("div", { class: "muted small", style: { marginBottom: "4px" }, title: t("Watch curve every 3 minutes relative to an undocumented reference: the course counts, not the level") },
        t("Skin temperature during the night (relative, °C)")),
      responsive(h("div"), (w) => timeChart({ width: w, height: 110, xMin: from, xMax: to,
        series: [{ label: t("Skin temperature"), color: "var(--c-spo2)", points: sl.skin_temp.filter(([x]) => x >= from && x <= to), gap: 10 * 60_000,
          format: (v) => `${v > 0 ? "+" : ""}${f.num(v, 1)} °C` }] }))) : null);
}

const SLEEP_SCORES = [
  ["daily.sleep_quality_score", "Quality"], ["daily.sleep_duration_score", "Duration"],
  ["daily.sleep_recovery_score", "Recovery"], ["daily.sleep_deep_score", "Deep sleep"],
  ["daily.sleep_rem_score", "REM sleep"], ["daily.sleep_light_score", "Light sleep"],
  ["daily.sleep_awake_time_score", "Awake time"], ["daily.sleep_interruptions_score", "Interruptions"],
  ["daily.sleep_restlessness_score", "Restlessness"],
];

function sleepScores(summary) {
  const rows = SLEEP_SCORES.filter(([k]) => summary[k]);
  if (!rows.length) return null;
  const extra = [
    summary["daily.sleep_awakenings"] ? t("{n} awakenings", { n: f.num(summary["daily.sleep_awakenings"].value) }) : null,
    summary["daily.sleep_avg_stress"] ? t("avg. stress during sleep {n}", { n: f.num(summary["daily.sleep_avg_stress"].value) }) : null,
  ].filter(Boolean).join(" · ");
  return h("div", {},
    h("div", { class: "score-list" }, rows.map(([k, label]) => {
      const v = summary[k].value;
      return h("div", { class: "score-row" }, h("span", { class: "muted" }, t(label)), h("b", { class: "num" }, f.num(v)),
        h("div", { class: "track" }, h("span", { style: { width: `${Math.max(0, Math.min(100, v))}%` } })));
    })),
    h("div", { class: "muted small", style: { marginTop: "8px" } },
      t("Garmin sleep sub-scores (0–100) from the watch's sleep file"), extra ? ` · ${extra}` : ""));
}

function journalCard(ctx, day, d) {
  const j = d.journal;
  return card(t("Journal & check-ins"), h("a", { href: `#/journal/${day}` }, j.text || j.checkins.length ? t("Open journal") : t("Add entry")),
    j.checkins.length ? checkinList(j.checkins) : h("div", { class: "muted small" }, t("No check-ins for this day.")),
    j.text ? h("p", { class: "journal-excerpt" }, j.text.length > 400 ? `${j.text.slice(0, 400)} …` : j.text) : null,
    h("details", { style: { marginTop: "10px" } }, h("summary", { class: "small muted" }, t("Quick check-in")),
      quickCheckin(day, () => ctx.rerender())));
}

function supplementsCard(day) {
  const box = h("div", { class: "muted small" }, t("Loading …"));
  const notice = h("div");
  const paintNotice = (st) => {
    const above = st.ingredients.filter((i) => i.above_upper_level);
    notice.replaceChildren(...(above.length ? [h("div", { class: "notice warn small", style: { marginTop: "8px" } },
      t("Above the EFSA upper level: {list}. The level applies to the total intake including food.", { list: above.map((i) => i.label).join(", ") }))] : []));
  };
  // the rows update themselves; after a save only the upper-level notice is refreshed
  const changed = () => api("/api/v1/supplements/status", { params: { day } }).then(paintNotice).catch(() => {});
  const load = async () => {
    const st = await api("/api/v1/supplements/status", { params: { day } });
    paintNotice(st);
    box.replaceChildren(supplementRows(st, day, changed), notice);
  };
  load().catch((e) => box.replaceChildren(h("div", { class: "error" }, e.message)));
  return card(t("Supplements"), h("a", { href: `#/health/${day}` }, t("Manage")), box);
}

const FITNESS = [
  ["vo2max", "VO₂max", (v) => f.num(v, 1), "higher"], ["vo2max_daily", "VO₂max (24/7)", (v) => f.num(v, 1), "higher"],
  ["race_time_5k", "5 km", (v) => raceTime(v), "lower"], ["race_time_10k", "10 km", (v) => raceTime(v), "lower"],
  ["race_time_half_marathon", "Half marathon", (v) => raceTime(v), "lower"], ["race_time_marathon", "Marathon", (v) => raceTime(v), "lower"],
];
/** 1515 → "25:15", 7719 → "2:08:39" */
function raceTime(sec) {
  const s = Math.round(sec), hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60), ss = String(s % 60).padStart(2, "0");
  return hh ? `${hh}:${String(mm).padStart(2, "0")}:${ss}` : `${mm}:${ss}`;
}

/** VO₂max and the watch's race predictions, latest up to the day, with the change over 4 weeks. */
function fitnessCard(day) {
  const box = h("div", { class: "muted small" }, t("Loading …"));
  api("/api/v1/fitness", { params: { day } }).then((r) => {
    const rows = FITNESS.filter(([k]) => r.metrics[k]);
    if (!rows.length) { box.replaceChildren(t("The watch has not sent VO₂max or race predictions yet.")); return; }
    box.className = "score-list";
    box.replaceChildren(...rows.map(([k, label, fmt, better]) => {
      const m = r.metrics[k];
      const diff = m.previous != null ? m.value - m.previous : null;
      const good = diff == null || Math.abs(diff) < 1e-9 ? null : (diff > 0) === (better === "higher");
      return h("div", { class: "score-row wide", title: t("as of {d}", { d: f.dateTime(m.ts) }) },
        h("span", { class: "muted" }, t(label)), h("b", { class: "num" }, fmt(m.value)),
        h("span", { class: ["small", good === true && "ok", good === false && "warn", good == null && "muted"] },
          diff == null ? "" : Math.abs(diff) < 1e-9 ? t("= 4 weeks ago") : t("{d} vs. 4 weeks ago", {
            d: `${diff > 0 ? "+" : "−"}${k.startsWith("race") ? raceTime(Math.abs(diff)) : f.num(Math.abs(diff), 1)}` })));
    }));
  }).catch((e) => box.replaceChildren(h("div", { class: "error" }, e.message)));
  return card(t("Fitness"), h("span", { class: "muted small" }, t("from the watch")), box);
}

function gardenWidget() {
  const box = h("div", { class: "muted small" }, t("Loading …"));
  api("/api/v1/habits/rewards").then((r) => box.replaceChildren(
    h("div", { class: "muted small garden-line" }, h("span", { class: "flame-num" }, icon("flame", { size: 14 }), f.num(r.flames.total)),
      ` · ${t("Level {n}", { n: r.level.level })} · ${t(r.level.title)}`),
    gardenView(r.garden, { compact: true }),
  )).catch((e) => box.replaceChildren(h("div", { class: "error" }, e.message)));
  return card(t("Habit garden"), h("a", { href: "#/habits" }, t("Habits")), box);
}

function habitsCard(day) {
  const box = h("div", { class: "muted small" }, t("Loading …"));
  const load = async () => box.replaceChildren(await habitCheckList(day));
  load().catch((e) => box.replaceChildren(h("div", { class: "error" }, e.message)));
  return card(t("Habits"), h("a", { href: "#/habits" }, t("Manage")), box);
}

function activitiesCard(d) {
  if (!d.activities.length) return card(t("Activities"), null, h("div", { class: "muted" }, t("No activities recorded.")));
  return card(t("Activities"), null, h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, h("th", {}, t("Time")), h("th", {}, t("Activity")), h("th", { class: "r" }, t("Duration")),
      h("th", { class: "r" }, t("Avg HR")), h("th", { class: "r" }, "kcal"), h("th", { class: "r" }, t("Sets")))),
    h("tbody", {}, d.activities.map((a) => h("tr", { class: "link", onclick: () => { location.hash = `#/training/general/${a.id}`; } },
      h("td", {}, f.time(a.start_ts)),
      h("td", {}, a.name || f.humanize(a.sub_sport || a.sport) || t("Activity")),
      h("td", { class: "r" }, f.seconds(a.elapsed_s)),
      h("td", { class: "r" }, f.num(a.avg_hr)),
      h("td", { class: "r" }, f.num(a.energy_kcal)),
      h("td", { class: "r" }, a.active_sets ? a.active_sets : "–")))))));
}
