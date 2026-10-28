// Profile (body data for the energy estimate, units, goal) and the areas shown in the UI. Used on
// the settings page and, step by step, in the setup wizard.

import { api } from "../api.js";
import { uuid4 } from "../checkin.js";
import { h } from "../dom.js";
import * as f from "../format.js";
import { t } from "../i18n.js";
import { card } from "../ui.js";

const LB = 0.45359237, IN = 2.54;

export const PAL_LEVELS = [
  [1.2, "Mostly sitting, little walking"],
  [1.4, "Office job, some walking"],
  [1.6, "On my feet a lot"],
  [1.8, "Physical work"],
];
export const MODULES = [
  ["nutrition", "Nutrition diary", "salad"], ["drinks", "Drinks", "droplet"], ["training", "Training", "barbell"],
  ["habits", "Habits", "plant-2"], ["journal", "Journal & check-ins", "notebook"], ["health", "Health & lab results", "heartbeat"],
  ["supplements", "Supplements", "pill"], ["watch", "Watch data (sleep, heart rate, steps …)", "device-watch"],
];

const row = (label, input, hint) => h("label", { class: "form-row" }, h("span", {}, label), input, hint ? h("small", { class: "muted" }, hint) : null);
const select = (options, value) => h("select", {}, options.map(([v, label]) => h("option", { value: String(v), selected: String(v) === String(value) }, label)));

/** Inputs for the profile; `read()` gives the PUT body (metric), `weightKg()` the entered weight. */
export function profileFields(p, weightKg) {
  const imperial = () => units.value === "imperial";
  const units = select([["metric", t("Metric (kg, cm)")], ["imperial", t("Imperial (lb, in)")]], p.units);
  const sex = select([["", "–"], ["female", t("female")], ["male", t("male")]], p.sex || "");
  const birth = h("input", { type: "number", min: 1900, max: new Date().getFullYear() - 10, value: p.birth_year ?? "", class: "num-input" });
  const height = h("input", { type: "number", min: 40, max: 250, step: 0.5, class: "num-input" });
  const weight = h("input", { type: "number", min: 20, max: 700, step: 0.1, class: "num-input" });
  const heightUnit = h("span", { class: "muted" }), weightUnit = h("span", { class: "muted" });
  if (p.height_cm) height.dataset.cm = p.height_cm;
  if (weightKg) weight.dataset.kg = weightKg;
  const show = () => {
    const hc = Number(height.dataset.cm || 0), wk = Number(weight.dataset.kg || 0);
    height.value = hc ? (imperial() ? (hc / IN).toFixed(1) : hc) : "";
    weight.value = wk ? (imperial() ? (wk / LB).toFixed(1) : wk.toFixed(1)) : "";
    heightUnit.textContent = imperial() ? "in" : "cm";
    weightUnit.textContent = imperial() ? "lb" : "kg";
  };
  const keep = () => {
    if (height.value) height.dataset.cm = imperial() ? Number(height.value) * IN : Number(height.value);
    if (weight.value) weight.dataset.kg = imperial() ? Number(weight.value) * LB : Number(weight.value);
  };
  height.addEventListener("change", keep);
  weight.addEventListener("change", keep);
  units.addEventListener("change", show);
  show();
  const pal = select(PAL_LEVELS.map(([v, l]) => [v, `${t(l)} (${f.num(v, 1)})`]), PAL_LEVELS.reduce((a, [v]) => (Math.abs(v - p.pal) < Math.abs(a - p.pal) ? v : a), 1.4));
  const goal = select([["lose", t("Lose weight")], ["keep", t("Keep my weight")], ["gain", t("Gain weight / muscle")]], p.goal);
  return {
    units, sex, birth, pal, goal,
    height: h("span", { class: "toolbar" }, height, heightUnit),
    weight: h("span", { class: "toolbar" }, weight, weightUnit),
    weightKg: () => (weight.value && weight.dataset.kg ? Math.round(Number(weight.dataset.kg) * 10) / 10 : null),
    read: () => ({
      units: units.value, sex: sex.value || null, birth_year: birth.value ? Number(birth.value) : null,
      height_cm: height.value && height.dataset.cm ? Math.round(Number(height.dataset.cm) * 10) / 10 : null,
      pal: Number(pal.value), goal: goal.value,
    }),
  };
}

/** Where the daily burn comes from. */
export function energyFields(p) {
  const source = select([
    ["auto", t("Watch where available, otherwise estimate")],
    ["watch", t("Only the watch")],
    ["estimate", t("Estimate from profile and workouts")],
    ["manual", t("My own daily burn")],
  ], p.energy_source);
  const tdee = h("input", { type: "number", min: 800, max: 6000, step: 10, value: p.manual_tdee_kcal ?? "", class: "num-input" });
  const tdeeRow = row(t("Daily burn without training (kcal)"), tdee,
    t("E.g. from another app or a calculator. On training days the workouts are added on top."));
  const sync = () => { tdeeRow.hidden = !["manual", "auto"].includes(source.value); };
  source.addEventListener("change", sync);
  sync();
  return { source, tdee, tdeeRow, read: () => ({ energy_source: source.value, manual_tdee_kcal: tdee.value ? Number(tdee.value) : null }) };
}

/** Saves a weight that differs from the latest one as a body measurement. */
export async function saveWeight(kg, previous) {
  if (!kg || (previous && Math.abs(previous - kg) < 0.05)) return;
  await api("/api/v1/body", { method: "POST", json: { id: uuid4(), metric: "weight", value: kg } });
}

/** The estimate the server computed, with a button that takes the suggested kcal goal over. */
export function estimateBox(p, onApplied) {
  const e = p.estimate;
  if (!e || (!e.bmr && !e.everyday_kcal)) {
    return h("p", { class: "muted small" }, t("Enter sex, birth year, height and weight to estimate your daily burn."));
  }
  const out = h("span", { class: "small muted" });
  const apply = async () => {
    const body = p.goal === "gain" ? { min_value: e.suggested_kcal, max_value: null } : { min_value: null, max_value: e.suggested_kcal };
    await api("/api/v1/goals/nutrition.energy", { method: "PUT", json: body });
    out.textContent = t("Saved");
    onApplied?.();
  };
  return h("div", { class: "notice" },
    h("div", { class: "sleep-stats" },
      e.bmr ? h("span", {}, h("span", { class: "muted" }, `${t("Resting (BMR)")} `), h("b", {}, `${f.num(e.bmr)} kcal`)) : null,
      e.everyday_kcal ? h("span", {}, h("span", { class: "muted" }, `${t("Everyday burn")} `), h("b", {}, `${f.num(e.everyday_kcal)} kcal`)) : null,
      e.avg_burn_14d ? h("span", {}, h("span", { class: "muted" }, `${t("Ø last 14 days")} `), h("b", {}, `${f.num(e.avg_burn_14d)} kcal`)) : null),
    e.suggested_kcal ? h("div", { class: "toolbar", style: { marginTop: "8px" } },
      h("span", {}, t("Suggested daily goal: {n} kcal", { n: f.num(e.suggested_kcal) })),
      h("button", { type: "button", onclick: apply }, t("Use as goal")), out) : null,
    h("div", { class: "small muted", style: { marginTop: "6px" } },
      t("An estimate (Mifflin-St Jeor), not a measurement. Adjust it if your weight moves differently than expected.")));
}

/** Area switches; `read()` gives the PUT body. */
export function moduleFields(mods) {
  const boxes = MODULES.map(([k, label]) => [k, h("input", { type: "checkbox", checked: mods[k] !== false }), label]);
  return {
    node: h("div", { class: "stack-sm" }, boxes.map(([k, box, label]) => h("label", { class: "form-check" }, box, ` ${t(label)}`))),
    read: () => Object.fromEntries(boxes.map(([k, box]) => [k, box.checked])),
    set: (key, on) => { const b = boxes.find(([k]) => k === key); if (b) b[1].checked = on; },
  };
}

// --- settings section ------------------------------------------------------------------------------

export async function profileSection(ctx) {
  const [p, mods] = await Promise.all([api("/api/v1/profile"), api("/api/v1/modules")]);
  const pf = profileFields(p, p.estimate?.weight_kg);
  const ef = energyFields(p);
  const mf = moduleFields(mods);
  const out = h("span", { class: "small muted" });
  const save = async (ev) => {
    ev.preventDefault();
    out.textContent = t("Saving …");
    try {
      await saveWeight(pf.weightKg(), p.estimate?.weight_kg);
      await api("/api/v1/profile", { method: "PUT", json: { ...pf.read(), ...ef.read() } });
      await api("/api/v1/modules", { method: "PUT", json: mf.read() });
      await ctx.reloadCaps();
    } catch (e) {
      out.textContent = t("Not saved: {msg}", { msg: e.message });
    }
  };
  return h("form", { class: "stack", onsubmit: save },
    card({ title: t("Profile"), icon: "user" }, null,
      h("p", { class: "muted small" }, t("Used to estimate your daily burn when no watch measures it, and for goal suggestions.")),
      h("div", { class: "form" },
        row(t("Units"), pf.units), row(t("Sex"), pf.sex, t("For the resting metabolic rate formula.")),
        h("div", { class: "toolbar" }, row(t("Birth year"), pf.birth), row(t("Body height"), pf.height), row(t("Weight"), pf.weight)),
        row(t("Everyday activity (without training)"), pf.pal), row(t("Goal"), pf.goal))),
    card({ title: t("Energy burn"), icon: "flame" }, null,
      h("div", { class: "form" }, row(t("Source"), ef.source), ef.tdeeRow),
      estimateBox(p, () => ctx.rerender())),
    card({ title: t("Areas"), icon: "layout-grid" }, null,
      h("p", { class: "muted small" }, t("Areas you don't use are hidden in the web app and the Android app. Data stays.")),
      mf.node),
    h("div", { class: "toolbar" }, h("button", { class: "primary", type: "submit" }, t("Save")), out));
}
