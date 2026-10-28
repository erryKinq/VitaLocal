// Insight cards for the day view, fed by GET /api/v1/insights (computed on the server).

import { h, responsive, s } from "../dom.js";
import * as f from "../format.js";
import { t } from "../i18n.js";
import { isoWeek } from "../reviews.js";
import { card } from "../ui.js";

const COMPONENTS = {
  sleep_score: ["Sleep score", (c) => f.num(c.value), () => t("last night, full marks from 94")],
  recovery_time: ["Recovery time", (c) => t("{n} h", { n: f.num(c.value) }), () => t("remaining, from the watch; caps the score from 10 h on")],
  hrv_status: ["HRV status", (c) => `${f.num(c.value)} ms`, (c) => t("7-day average · balanced from {v} ms", { v: f.num(c.baseline) })],
  acute_load: ["Acute load", (c) => f.num(c.value, 2), () => t("acute : chronic, up to 1.3 is fine")],
  sleep_history: ["Sleep history", (c) => f.num(c.value), () => t("sleep score, last 3 nights")],
  stress_history: ["Stress history", (c) => f.num(c.value), () => t("average stress of the 3 days before, up to 34 is fine")],
};
// Garmin's bands: poor 0–24, low 25–49, moderate 50–74, high 75–94, prime 95–100
const LEVELS = { prime: ["Prime", "ok"], high: ["High", "ok"], moderate: ["Moderate", "warn"], low: ["Low", "bad"], poor: ["Poor", "bad"] };
// Garmin's own levels (training readiness level 1–5)
const GARMIN_LEVELS = { 1: "poor", 2: "low", 3: "moderate", 4: "high", 5: "prime" };

/** "Garmin: 86 (high)" or null when the watch sent no readiness for the day. */
export function garminReadiness(r) {
  const g = r.garmin;
  if (!g) return null;
  return h("span", { class: "muted small", title: t("The watch's own training readiness") },
    t("Garmin: {n}", { n: g.score }), g.level ? ` (${t(GARMIN_LEVELS[g.level])})` : "");
}

/** " · Garmin 72 %": the watch's own rating of this factor, when it sent one for the day. */
function garminFactor(r, key) {
  const v = r.garmin?.factors?.[key];
  if (v == null) return null;
  return h("span", { class: "muted small", title: t("The watch's own rating of this factor") }, ` · ${t("Garmin {n} %", { n: v })}`);
}

const impactText = (n) => (n < 0 ? `−${-n}` : "±0");

export function readinessCard(ins) {
  const r = ins.readiness;
  const note = h("div", { class: "small muted", style: { marginTop: "10px" } },
    t("VitaLocal's own estimate from Garmin's factors: 100 minus what each factor costs; a long recovery time caps it. Fitted against the watch's score."));
  if (r.score == null) {
    return card({ title: t("Training readiness"), icon: "gauge" }, null,
      h("div", { class: "muted" }, t("Not enough data for this day (needs last night's sleep score and two more factors).")),
      garminReadiness(r), note);
  }
  const [label, tone] = LEVELS[r.level];
  return card({ title: t("Training readiness"), icon: "gauge" }, null,
    h("div", { class: "readiness" },
      gauge(r.score, tone),
      h("div", {}, h("div", { class: ["readiness-level", tone] }, t(label)),
        h("div", { class: "small muted" }, t("{n} of 6 components available", { n: r.components.length })),
        garminReadiness(r))),
    h("div", { class: "score-list" }, r.components.map((c) => {
      const [name, value, base] = COMPONENTS[c.key] || [c.key, (x) => f.num(x.value), () => ""];
      return h("div", { class: "score-row", title: t("costs {n} points", { n: -c.impact }) },
        h("span", {}, h("span", { class: "muted" }, t(name)), " ", h("b", {}, value(c)),
          base(c) ? h("span", { class: "muted small" }, ` · ${base(c)}`) : null,
          garminFactor(r, c.key)),
        h("b", { class: ["num", c.impact <= -10 ? "bad" : c.impact < 0 ? "warn" : null] }, impactText(c.impact)),
        // the bar shows what is left of the 100 after this factor
        h("div", { class: "track" }, h("span", { class: c.impact <= -10 ? "bad" : c.impact < 0 ? "warn" : "ok",
          style: { width: `${Math.max(0, 100 + c.impact * 2)}%` } })));
    })),
    note);
}

function gauge(score, tone) {
  const r = 34, c = 2 * Math.PI * r;
  return s("svg", { viewBox: "0 0 84 84", width: 84, height: 84, class: "gauge", role: "img", "aria-label": `${score}/100` },
    s("circle", { cx: 42, cy: 42, r, class: "gauge-track" }),
    s("circle", { cx: 42, cy: 42, r, class: ["gauge-value", tone], "stroke-dasharray": `${(c * score) / 100} ${c}`, transform: "rotate(-90 42 42)" }),
    s("text", { x: 42, y: 49, "text-anchor": "middle", class: "gauge-text" }, String(score)));
}

// --- sleep rhythm --------------------------------------------------------------------------------

const clockMin = (hhmm) => { const [a, b] = hhmm.split(":").map(Number); return (a * 60 + b - 720 + 1440) % 1440; }; // minutes after noon

export function sleepRhythmCard(ins) {
  const sr = ins.sleep_rhythm;
  const row = (label, x) => h("div", { class: "rhythm-row" },
    h("span", { class: "muted small" }, label),
    x.nights ? h("b", { class: "num rhythm-window" }, `${x.bedtime} – ${x.wake_time}`) : h("span", { class: "muted" }, "–"),
    x.nights ? h("span", { class: "small muted" }, t("Ø {d} asleep · ±{b} / ±{w} min · {n} nights", {
      d: f.duration(x.asleep_minutes), b: f.num(x.bedtime_spread_min), w: f.num(x.wake_time_spread_min), n: x.nights })) : null);
  const habitRow = (hb) => h("div", { class: "rhythm-row" },
    h("span", { class: "muted small" }, hb.name),
    h("b", { class: "num rhythm-window" }, [hb.min_value, hb.max_value].map((v) => (v == null ? "…" : f.hhmm(v))).join(" – ")),
    h("span", { class: ["small", hb.measured && hb.done === hb.measured ? "ok" : "muted"] },
      hb.measured ? t("in the window on {n} of {m} days (last 7)", { n: hb.done, m: hb.measured }) : t("no nights measured")));
  return card({ title: t("Sleep rhythm"), icon: "clock" }, h("span", { class: "muted small" }, t("in bed, average")),
    row(t("Last 7 nights"), sr.last_7),
    row(t("Last 28 nights"), sr.last_28),
    ...(sr.habits || []).map(habitRow),
    h("div", { class: "muted small", style: { margin: "12px 0 4px" } }, t("Weekly trend: bars span bedtime to wake time")),
    responsive(h("div"), (w) => rhythmChart(sr.weeks, w)),
    h("div", { class: "small muted" }, t("± = average deviation from the mean time: lower means a steadier rhythm.")));
}

function rhythmChart(weeks, width) {
  const rows = weeks.filter((x) => x.nights);
  if (!rows.length) return h("div", { class: "chart-empty", style: { height: "120px" } }, t("No sleep recorded yet"));
  const lo = Math.min(...rows.map((x) => clockMin(x.bedtime))) - 30;
  const hi = Math.max(...rows.map((x) => clockMin(x.wake_time))) + 30;
  const height = 150, top = 8, bottom = 22, left = 40;
  const y = (m) => top + ((m - lo) / (hi - lo)) * (height - top - bottom);
  const bw = (width - left) / weeks.length;
  const ticks = [];
  for (let m = Math.ceil(lo / 60) * 60; m <= hi; m += 120) ticks.push(m);
  const label = (m) => `${String(Math.floor(((m + 720) % 1440) / 60)).padStart(2, "0")}:00`;
  return s("svg", { width, height, class: "chart-svg" },
    ticks.map((m) => [s("line", { x1: left, x2: width, y1: y(m), y2: y(m), class: "grid" }),
      s("text", { x: left - 6, y: y(m) + 4, "text-anchor": "end", class: "tick" }, label(m))]),
    weeks.map((wk, i) => {
      const x = left + i * bw + bw * 0.2;
      const parts = [s("text", { x: x + bw * 0.3, y: height - 6, "text-anchor": "middle", class: "tick" }, `${t("W")}${isoWeek(wk.week_start)}`)];
      if (wk.nights) {
        parts.push(s("rect", { x, width: bw * 0.6, y: y(clockMin(wk.bedtime)), height: Math.max(2, y(clockMin(wk.wake_time)) - y(clockMin(wk.bedtime))),
          rx: 4, class: ["rhythm-bar", wk.partial && "partial"] },
          s("title", {}, `${t("Week {n}", { n: isoWeek(wk.week_start) })}: ${wk.bedtime}–${wk.wake_time}, ${f.duration(wk.asleep_minutes)} · ${t("Sleep score")} ${f.num(wk.sleep_score)}`)));
      }
      return parts;
    }));
}

// --- recovery --------------------------------------------------------------------------------------

export function recoveryCard(ins) {
  const r = ins.recovery;
  const a = r.after_training, b = r.after_rest;
  const cmp = (key, dec, unit, better) => {
    const x = a[key], y = b[key];
    const diff = x != null && y != null ? x - y : null;
    const tone = diff == null || Math.abs(diff) < 0.5 ? "" : (diff > 0) === (better === "higher") ? "ok" : "bad";
    return h("tr", {},
      h("td", {}, t({ hrv: "HRV", resting_heart_rate: "Resting HR", sleep_score: "Sleep score" }[key])),
      h("td", { class: "r" }, x == null ? "–" : `${f.num(x, dec)} ${unit}`),
      h("td", { class: "r" }, y == null ? "–" : `${f.num(y, dec)} ${unit}`),
      h("td", { class: ["r", tone] }, diff == null ? "–" : `${diff > 0 ? "+" : "−"}${f.num(Math.abs(diff), dec)}`));
  };
  return card({ title: t("Recovery after training"), icon: "refresh" }, h("span", { class: "muted small" }, t("last {n} days", { n: r.lookback_days })),
    h("div", { class: "stat-row" },
      h("span", {}, h("span", { class: "muted" }, `${t("Typical recovery")} `),
        h("b", {}, r.typical_recovery_days == null ? "–" : t(r.typical_recovery_days === 1 ? "{n} day" : "{n} days", { n: f.num(r.typical_recovery_days, r.typical_recovery_days % 1 ? 1 : 0) })))),
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, t("Next night")), h("th", { class: "r" }, t("after training ({n})", { n: a.nights })),
        h("th", { class: "r" }, t("after rest day ({n})", { n: b.nights })), h("th", { class: "r" }, t("Difference")))),
      h("tbody", {}, cmp("hrv", 1, "ms", "higher"), cmp("resting_heart_rate", 1, "bpm", "lower"), cmp("sleep_score", 0, "", "higher")))),
    r.workouts.length ? h("div", { class: "table-wrap", style: { marginTop: "12px" } }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, t("Workout")), h("th", { class: "r" }, t("HRV next night")),
        h("th", { class: "r" }, t("Resting HR next night")), h("th", { class: "r" }, t("Back to baseline")))),
      h("tbody", {}, r.workouts.map((w) => h("tr", { class: "link", onclick: () => { location.hash = `#/training/general/${w.activity_id}`; } },
        h("td", {}, `${f.shortDate(w.day)} · ${w.name || t("Activity")}`, h("span", { class: "muted small" }, ` ${w.minutes} min`)),
        h("td", { class: "r" }, signed(w.next_night.hrv_delta, 1, "ms")),
        h("td", { class: "r" }, signed(w.next_night.resting_heart_rate_delta, 1, "bpm")),
        h("td", { class: "r" }, w.recovered_after_days == null ? "–" : t(w.recovered_after_days === 1 ? "after {n} day" : "after {n} days", { n: w.recovered_after_days }))))))) : null,
    h("div", { class: "small muted", style: { marginTop: "8px" } },
      t("Workouts from {n} min. Back to baseline: HRV ≥ 97 % and resting HR ≤ +1 bpm of the 28-day median; – = not within 4 days or not measured.", { n: 20 })));
}

const signed = (v, dec, unit) => (v == null ? "–" : `${v > 0 ? "+" : v < 0 ? "−" : "±"}${f.num(Math.abs(v), dec)} ${unit}`);

// --- energy ----------------------------------------------------------------------------------------

export function energyCard(ins) {
  const { last_7: a, last_28: b } = ins.energy;
  const row = (label, key, unit, note) => h("tr", {},
    h("td", {}, t(label), note ? h("span", { class: "muted small" }, ` ${note(a, b)}`) : null),
    h("td", { class: "r" }, a[key] == null ? "–" : `${key === "balance" && a[key] > 0 ? "+" : ""}${f.num(a[key])} ${unit}`),
    h("td", { class: "r" }, b[key] == null ? "–" : `${key === "balance" && b[key] > 0 ? "+" : ""}${f.num(b[key])} ${unit}`));
  return card({ title: t("Energy & activity"), icon: "battery-3" }, h("span", { class: "muted small" }, t("daily averages, complete days")),
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}), h("th", { class: "r" }, t("7 days")), h("th", { class: "r" }, t("28 days")))),
      h("tbody", {},
        row("Burned", "burned", "kcal"),
        row("Base (resting / everyday)", "resting", "kcal"),
        row("Active energy", "active", "kcal"),
        row("Intake", "intake", "kcal", (x, y) => t("({a} / {b} days logged)", { a: x.intake_days, b: y.intake_days })),
        row("Balance", "balance", "kcal", (x, y) => t("(days with both: {a} / {b})", { a: x.balance_days, b: y.balance_days })),
        row("Steps", "steps", ""),
        row("Drinks", "drinks_ml", "ml")))),
    h("div", { class: "small muted", style: { marginTop: "8px" } }, t("Burned = resting metabolic rate + active energy from the watch; on days without, estimated from your profile and workouts.")));
}

// --- weekly trend ------------------------------------------------------------------------------------

export function weeklyCard(ins) {
  const cols = [
    ["Sleep score", (w) => f.num(w.sleep_score)],
    ["Asleep", (w) => f.duration(w.asleep_minutes)],
    ["HRV", (w) => f.num(w.hrv, 1)],
    ["Resting HR", (w) => f.num(w.resting_heart_rate, 1)],
    ["Steps", (w) => f.num(w.steps)],
    ["Burned", (w) => f.num(w.burned)],
    ["Intake", (w) => f.num(w.intake)],
    ["Stress", (w) => f.num(w.stress)],
    ["Workouts", (w) => (w.workouts ? `${w.workouts} · ${w.workout_minutes} min` : "–")],
    ["Mood", (w) => f.num(w.mood, 1)],
  ];
  const weeks = [...ins.weeks].reverse();
  return card({ title: t("Weekly trend"), icon: "calendar-stats" }, h("span", { class: "muted small" }, t("weekly averages · current week so far")),
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, t("Week")), cols.map(([label]) => h("th", { class: "r" }, t(label))))),
      h("tbody", {}, weeks.map((w) => h("tr", { class: w.partial && "muted" },
        h("td", {}, `${t("W")}${isoWeek(w.week_start)} `, h("span", { class: "muted small" }, f.shortDate(w.week_start))),
        cols.map(([, fn]) => h("td", { class: "r" }, fn(w)))))))));
}
