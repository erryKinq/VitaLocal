// Daily goals (nutrition page and day widget): nutrient goals set here (versioned, valid from the
// day they are saved), the drink goal from a formula (body weight × ml/kg + a supplement per
// training hour), all from GET /api/v1/goals.

import { api, meta } from "./api.js";
import { h } from "./dom.js";
import * as f from "./format.js";
import { t } from "./i18n.js";
import { card, sheet } from "./ui.js";

const STATUS = { within: ["in the goal", "ok"], below: ["below", "warn"], above: ["above", "warn"] };

const bound = (g) => {
  const v = (x) => `${f.num(x, g.decimals)}${g.unit ? ` ${g.unit}` : ""}`;
  if (g.min_value != null && g.max_value != null) return `${v(g.min_value)} – ${v(g.max_value)}`;
  return g.min_value != null ? `≥ ${v(g.min_value)}` : `≤ ${v(g.max_value)}`;
};

/** Progress towards the goal: full at the lower bound (or at the upper bound for "stay below"). */
function goalBar(g) {
  const ref = g.min_value ?? g.max_value;
  const share = g.value == null || !ref ? 0 : Math.min(1, g.value / (g.max_value ?? ref));
  const tone = g.status ? STATUS[g.status][1] : "";
  return h("div", { class: "track goal-track" }, h("span", { class: tone, style: { width: `${Math.round(share * 100)}%` } }),
    g.min_value != null && g.max_value != null
      ? h("i", { class: "goal-mark", style: { left: `${Math.round((g.min_value / g.max_value) * 100)}%` }, title: t("lower bound") }) : null);
}

/** One row per goal: value, bounds, status and where the goal comes from. */
export function goalRows(goals) {
  if (!goals?.length) return h("div", { class: "muted small" }, t("No goals set yet."));
  return h("div", { class: "score-list goal-list" }, goals.map((g) => {
    const [label, tone] = g.status ? STATUS[g.status] : ["no value yet", "muted"];
    const value = g.value == null ? "–" : `${f.num(g.value, g.decimals)}${g.unit ? ` ${g.unit}` : ""}`;
    const note = g.formula
      ? t("{goal} = {base} base ({kg} kg) + {train} training ({min} min)", {
        goal: f.num(g.min_value), base: f.num(g.formula.base_ml), kg: g.formula.weight_kg == null ? "–" : f.num(g.formula.weight_kg, 1),
        train: f.num(g.formula.training_ml), min: f.num(g.formula.training_min) })
      : null;
    return h("div", { class: "score-row goal-row" },
      h("span", {}, h("span", { class: "muted" }, t(g.label)), " ", h("b", {}, value),
        h("span", { class: "muted small" }, ` · ${t("goal")} ${bound(g)}`),
        note ? h("div", { class: "muted small" }, note) : null),
      h("span", { class: ["small", tone] }, t(label)),
      goalBar(g));
  }));
}

/** Goals card; `settings` adds the nutrient goal editor and the drink formula (nutrition page). */
export function goalsCard(goals, { settings = false, onChange } = {}) {
  const within = (goals || []).filter((g) => g.status === "within").length;
  return card({ title: t("Goals"), icon: "target", collapsible: settings, id: "nutrition.goals",
    summary: goals?.length ? t("{n} of {m} reached", { n: within, m: goals.length }) : null },
    settings ? h("button", { class: "ghost", onclick: () => goalSettings(goals, onChange) }, t("Edit")) : null,
    goalRows(goals));
}

/** Nutrient goals and the drink formula in a sheet (nutrition page › Goals). */
export function goalSettings(goals, onChange) {
  const editor = goalEditor(goals, onChange), formula = drinkFormula(onChange);
  sheet({ title: t("Goals"), icon: "target", wide: true, body: [editor, formula] });
  editor.open = true;
}

// Metrics a nutrient goal can be set for (as the server's GOAL_METRICS).
const GOAL_METRICS = ["nutrition.energy", "nutrition.protein", "nutrition.carbohydrates", "nutrition.fat",
  "nutrition.saturated_fat", "nutrition.sugars", "nutrition.fiber", "nutrition.salt", "nutrition.caffeine", "nutrition.alcohol"];

/** Minimum and maximum per nutrient; a change applies from today on, earlier days keep theirs. */
function goalEditor(goals, onChange) {
  const box = h("details", { class: "sheet-section" }, h("summary", {}, t("Nutrient goals")));
  box.addEventListener("toggle", async () => {
    if (!box.open || box.dataset.loaded) return;
    box.dataset.loaded = "1";
    const md = await meta();
    const defs = Object.fromEntries(md.metrics.map((m) => [m.key, m]));
    const current = await api("/api/v1/goals", { params: { day: new Intl.DateTimeFormat("en-CA", { timeZone: f.timezone() }).format(new Date()) } });
    const byMetric = Object.fromEntries(current.goals.map((g) => [g.metric, g]));
    const status = h("span", { class: "small muted" });
    const rows = GOAL_METRICS.map((m) => {
      const g = byMetric[m] || {};
      const input = (v) => h("input", { type: "number", class: "num-input", min: 0, step: "any", value: v ?? "" });
      const lo = input(g.min_value), hi = input(g.max_value);
      return { m, lo, hi, was: [g.min_value ?? null, g.max_value ?? null],
        el: h("div", { class: "toolbar small goal-edit" }, h("span", { class: "goal-edit-label" }, t(defs[m]?.label || m)),
          t("min"), lo, t("max"), hi, h("span", { class: "muted" }, defs[m]?.unit || "")) };
    });
    box.append(h("form", { class: "form", style: { marginTop: "8px" }, onsubmit: async (e) => {
      e.preventDefault();
      const num = (el) => (el.value === "" ? null : Number(el.value));
      try {
        for (const r of rows) {
          const now = [num(r.lo), num(r.hi)];
          if (now[0] === r.was[0] && now[1] === r.was[1]) continue;
          if (now[0] == null && now[1] == null) await api(`/api/v1/goals/${r.m}`, { method: "DELETE" });
          else await api(`/api/v1/goals/${r.m}`, { method: "PUT", json: { min_value: now[0], max_value: now[1] } });
          r.was = now;
        }
        status.textContent = t("Saved");
        onChange?.();
      } catch (ex) { status.textContent = t("Not saved: {msg}", { msg: ex.message }); }
    } },
    rows.map((r) => r.el),
    h("div", { class: "muted small" }, t("Leave both empty for no goal. Changes apply from today; earlier days keep the goal valid then.")),
    h("div", { class: "toolbar" }, h("button", { class: "primary", type: "submit" }, t("Save")), status)));
  });
  return box;
}

function drinkFormula(onChange) {
  const box = h("details", { class: "sheet-section" }, h("summary", {}, t("Drink goal formula")));
  box.addEventListener("toggle", async () => {
    if (!box.open || box.dataset.loaded) return;
    box.dataset.loaded = "1";
    const s = await api("/api/v1/settings/drink-goal");
    const num = (v, max) => h("input", { type: "number", min: 0, max, step: 1, value: v, class: "num-input" });
    const enabled = h("input", { type: "checkbox", checked: s.enabled });
    const perKg = num(s.ml_per_kg, 100), perHour = num(s.ml_per_training_hour, 3000), fallback = num(s.fallback_ml, 10000);
    const status = h("span", { class: "small muted" });
    box.append(h("form", { class: "form", style: { marginTop: "8px" }, onsubmit: async (e) => {
      e.preventDefault();
      try {
        await api("/api/v1/settings/drink-goal", { method: "PUT", json: {
          enabled: enabled.checked, ml_per_kg: Number(perKg.value), ml_per_training_hour: Number(perHour.value), fallback_ml: Number(fallback.value) } });
        status.textContent = t("Saved");
        onChange?.();
      } catch (ex) { status.textContent = ex.message; }
    } },
    h("label", { class: "form-check small" }, enabled, t("Daily drink goal from the formula")),
    h("div", { class: "toolbar small" }, perKg, t("ml per kg body weight"), "+", perHour, t("ml per hour of activity")),
    h("div", { class: "toolbar small" }, t("Without a weight:"), fallback, "ml"),
    h("div", { class: "muted small" }, t("Weight: the latest value entered (or imported from Waistline). Activity: everything the watch recorded that day, so the goal rises after a workout is synced.")),
    h("div", { class: "toolbar" }, h("button", { class: "primary", type: "submit" }, t("Save")), status)));
  });
  return box;
}
