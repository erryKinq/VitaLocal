// Check-ins: quick entry for the journal (mood, energy, symptoms, notes) and the entry list; drink
// logging (nutrition, stored under /api/v1/drinks) and its list.

import { api } from "./api.js";
import { h } from "./dom.js";
import { icon } from "./icons.js";
import * as f from "./format.js";
import { t } from "./i18n.js";

// crypto.randomUUID needs a secure context (HTTPS/localhost); the LAN URL is plain HTTP.
export function uuid4() {
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const hex = [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export const todayIso = () => new Intl.DateTimeFormat("en-CA", { timeZone: f.timezone() }).format(new Date());

// Now for today; midday for other days (12:00 UTC falls on the same calendar day in Europe).
export const tsFor = (day) => (day === todayIso() ? new Date().toISOString() : `${day}T12:00:00Z`);

export async function saveCheckin(body) {
  return api("/api/v1/checkins", { method: "POST", json: { id: uuid4(), ...body } });
}

const MOOD = ["Very bad", "Bad", "Okay", "Good", "Very good"];
const ENERGY = ["Exhausted", "Tired", "Normal", "Fresh", "Energetic"];
export const DRINKS = [["water", "Water"], ["tea", "Tea"], ["coffee", "Coffee"], ["other", "Other"]];
// Default of the "caffeinated" switch per kind (the server applies the same default).
const CAFFEINATED = new Set(["coffee", "tea"]);
const drinkLabel = (label) => t((DRINKS.find(([k]) => k === label) || [null, "Water"])[1]);

export function describe(c) {
  const v = f.num(c.value);
  switch (c.kind) {
    case "mood": return t("Mood {v}/5 · {label}", { v, label: t(MOOD[Math.round(c.value) - 1] || "") });
    case "energy": return t("Energy {v}/5 · {label}", { v, label: t(ENERGY[Math.round(c.value) - 1] || "") });
    case "symptom": return c.value ? t("{label} · severity {v}/5", { label: c.label, v }) : c.label;
    case "rpe": return t("Workout effort (RPE) {v}/10", { v });
    default: return c.note || f.humanize(c.kind);
  }
}

function saver(status, onSaved, send = saveCheckin) {
  return async (body) => {
    status.textContent = t("Saving …");
    try {
      await send(body);
      status.textContent = t("Saved");
      onSaved?.();
    } catch (e) {
      status.textContent = t("Not saved: {msg}", { msg: e.message });
    }
  };
}

/** Journal quick entry. `onSaved` is called after every stored check-in. */
export function quickCheckin(day, onSaved) {
  const status = h("span", { class: "small muted" });
  const save = saver(status, onSaved);
  const run = (body) => save({ ts: tsFor(day), ...body });
  const scale = (kind, labels) => h("div", { class: "scale" },
    labels.map((label, i) => h("button", { class: "chip", title: t(label), onclick: () => run({ kind, value: i + 1 }) }, `${i + 1}`)));

  const symptom = h("input", { type: "text", placeholder: t("Symptom, e.g. headache"), list: "symptom-list", maxlength: 120 });
  const severity = h("select", {}, [1, 2, 3, 4, 5].map((v) => h("option", { value: v, selected: v === 2 }, t("Severity {n}", { n: v }))));
  const datalist = h("datalist", { id: "symptom-list" });
  api("/api/v1/symptoms").then((rows) => datalist.replaceChildren(...rows.map((r) => h("option", { value: r.label })))).catch(() => {});
  const note = h("input", { type: "text", placeholder: t("Short note"), maxlength: 4000 });

  return h("div", { class: "checkin-panel" },
    h("div", { class: "checkin-row" }, h("span", { class: "checkin-label" }, t("Mood")), scale("mood", MOOD),
      h("span", { class: "small muted" }, t("1 very bad · 5 very good"))),
    h("div", { class: "checkin-row" }, h("span", { class: "checkin-label" }, t("Energy")), scale("energy", ENERGY),
      h("span", { class: "small muted" }, t("1 exhausted · 5 energetic"))),
    h("form", { class: "checkin-row", onsubmit: (e) => {
      e.preventDefault();
      if (!symptom.value.trim()) return;
      run({ kind: "symptom", label: symptom.value.trim(), value: Number(severity.value) }).then(() => { symptom.value = ""; });
    } }, h("span", { class: "checkin-label" }, t("Symptom")), symptom, severity, datalist, h("button", { type: "submit" }, t("Add"))),
    h("form", { class: "checkin-row", onsubmit: (e) => {
      e.preventDefault();
      if (!note.value.trim()) return;
      run({ kind: "note", note: note.value.trim() }).then(() => { note.value = ""; });
    } }, h("span", { class: "checkin-label" }, t("Note")), note, h("button", { type: "submit" }, t("Add"))),
    status);
}

export async function saveDrink(body) {
  return api("/api/v1/drinks", { method: "POST", json: { id: uuid4(), ...body } });
}

/** Drinks of one day: {total_ml, by_drink, entries}. */
export const loadDrinks = (day) => api("/api/v1/drinks", { params: { day } });

/** Drink logging: type chips with the caffeine switch, quick amounts and a custom amount. */
export function drinkEntry(day, onSaved) {
  const status = h("span", { class: "small muted" });
  const save = saver(status, onSaved, saveDrink);
  let drink = "water";
  const caffeine = h("input", { type: "checkbox" });
  const chips = h("div", { class: "segmented drink-kinds" });
  const drawChips = () => chips.replaceChildren(...DRINKS.map(([k, label]) =>
    h("button", { type: "button", class: k === drink && "active", onclick: () => { drink = k; caffeine.checked = CAFFEINATED.has(k); drawChips(); } }, t(label))));
  drawChips();
  const custom = h("input", { type: "number", min: 1, max: 5000, step: 1, placeholder: t("Custom ml"), class: "num-input" });
  const run = (ml) => save({ drink, ml, caffeine: caffeine.checked, ts: tsFor(day) });
  return h("div", { class: "checkin-panel drink-entry" },
    h("div", { class: "toolbar" }, chips,
      h("label", { class: "form-check small", title: t("Counts for caffeine habits (e.g. no caffeine after 14:00)") }, caffeine, icon("coffee", { size: 16 }), t("caffeinated"))),
    h("div", { class: "quick-amounts" },
      [150, 250, 330, 500].map((ml) => h("button", { type: "button", onclick: () => run(ml) }, icon("plus", { size: 14 }), `${ml} ml`)),
      h("form", { class: "custom-amount", onsubmit: (e) => {
        e.preventDefault();
        const ml = Number(custom.value);
        if (ml > 0) run(ml).then(() => { custom.value = ""; });
      } }, custom, h("button", { type: "submit", class: "icon-only", title: t("Add"), "aria-label": t("Add") }, icon("check", { size: 16 })))),
    status);
}

export function checkinList(checkins, onChange, emptyText = "No check-ins for this day.") {
  if (!checkins.length) return h("div", { class: "muted small" }, t(emptyText));
  return h("ul", { class: "checkin-list" }, checkins.map((c) => h("li", {},
    h("span", { class: "muted small num" }, f.time(c.ts)),
    h("span", { class: `badge kind-${c.kind}` }, t(c.kind)),
    h("span", {}, describe(c), c.note && c.kind !== "note" ? h("span", { class: "muted" }, ` – ${c.note}`) : null),
    onChange ? h("button", { class: "ghost icon-only", title: t("Delete"), "aria-label": t("Delete check-in"),
      onclick: async () => { await api(`/api/v1/checkins/${c.id}`, { method: "DELETE" }); onChange(); } }, icon("x", { size: 16 })) : null)));
}

export function drinkList(entries, onChange) {
  if (!entries.length) return h("div", { class: "muted small" }, t("No drinks logged for this day."));
  return h("ul", { class: "checkin-list" }, entries.map((d) => drinkItem(d, onChange)));
}

/** One logged drink; with `onChange` it can be corrected (kind, time, amount, caffeine) or deleted. */
function drinkItem(d, onChange) {
  const li = h("li");
  const edit = () => {
    const kind = h("select", {}, DRINKS.map(([k, label]) => h("option", { value: k, selected: k === d.drink }, t(label))));
    const caffeine = h("input", { type: "checkbox", checked: !!d.caffeine });
    kind.addEventListener("change", () => { caffeine.checked = CAFFEINATED.has(kind.value); });
    const ml = h("input", { type: "number", min: 1, max: 5000, step: 1, value: d.ml, required: true, class: "num-input", "aria-label": "ml" });
    const at = h("input", { type: "time", value: f.clockOf(d.ts), required: true, "aria-label": t("Time") });
    const err = h("span", { class: "error small", hidden: true });
    li.className = "editing";
    li.replaceChildren(h("form", { class: "toolbar", onsubmit: async (e) => {
      e.preventDefault();
      try {
        await api(`/api/v1/drinks/${d.id}`, { method: "PUT",
          json: { ts: f.zonedIso(f.dayOf(d.ts), at.value), drink: kind.value, ml: Number(ml.value), caffeine: caffeine.checked } });
        onChange();
      } catch (ex) {
        err.hidden = false;
        err.textContent = ex.message;
      }
    } }, at, kind, ml, h("span", { class: "muted small" }, "ml"),
    h("label", { class: "form-check small" }, caffeine, t("caffeinated")),
    h("button", { type: "submit", class: "primary" }, t("Save")),
    h("button", { type: "button", onclick: view }, t("Cancel")), err));
  };
  const view = () => {
    li.className = "";
    li.replaceChildren(
      h("span", { class: "muted small num" }, f.time(d.ts)),
      h("span", { class: "badge kind-drink" }, drinkLabel(d.drink)),
      h("span", { class: "drink-ml" }, `${f.num(d.ml)} ml`, d.caffeine ? h("span", { class: "muted", title: t("caffeinated") }, icon("coffee", { size: 14 })) : null),
      onChange ? h("span", { class: "row-actions" },
        h("button", { class: "ghost icon-only", title: t("Edit"), "aria-label": t("Edit drink"), onclick: edit }, icon("pencil", { size: 16 })),
        h("button", { class: "ghost icon-only", title: t("Delete"), "aria-label": t("Delete drink"),
          onclick: async () => { await api(`/api/v1/drinks/${d.id}`, { method: "DELETE" }); onChange(); } }, icon("x", { size: 16 }))) : null);
  };
  view();
  return li;
}

/** A <details> list whose open state is remembered per browser under `key`. */
export function foldable(key, label, body) {
  const k = `vitalocal.fold.${key}`;
  let open = false;
  try { open = localStorage.getItem(k) === "1"; } catch { /* storage unavailable */ }
  const el = h("details", { class: "fold", open }, h("summary", {}, label), body);
  el.addEventListener("toggle", () => { try { localStorage.setItem(k, el.open ? "1" : "0"); } catch { /* ignore */ } });
  return el;
}

/** Drink entry plus the day's list, total and progress towards `goal` (from /goals); keeps itself up to date. */
export function drinksPanel(day, initial, goal = null) {
  const summary = h("div", { class: "drink-summary" });
  const list = h("div");
  const draw = (d) => {
    const pct = goal ? Math.round((d.total_ml / goal.min_value) * 100) : 0;
    const share = Math.min(100, pct);
    const fm = goal?.formula;
    summary.replaceChildren(
      h("div", { class: "drink-total" }, h("b", { class: "num" }, f.num(d.total_ml)),
        h("span", { class: "muted" }, goal ? ` / ${f.num(goal.min_value)} ml` : " ml"),
        goal ? h("span", { class: ["badge", d.total_ml >= goal.min_value ? "ok" : ""] }, `${pct} %`) : null),
      goal ? h("div", { class: "track goal-track drink-track" }, h("span", { class: d.total_ml >= goal.min_value ? "ok" : "", style: { width: `${share}%` } })) : null,
      fm ? h("div", { class: "muted small" }, t("Goal {goal} ml = {base} base + {train} training", {
        goal: f.num(goal.min_value), base: f.num(fm.base_ml), train: f.num(fm.training_ml) })) : null);
    // the entries fold away (open state remembered per browser); the total above is what counts
    list.replaceChildren(d.entries.length ? foldable("drinks", t("Entries ({n})", { n: d.entries.length }), drinkList(d.entries, refresh))
      : drinkList(d.entries, refresh));
  };
  const refresh = async () => draw(await loadDrinks(day));
  if (initial) draw(initial); else refresh().catch((e) => summary.replaceChildren(h("div", { class: "error" }, e.message)));
  return h("div", { class: "drinks-panel" }, summary, drinkEntry(day, refresh), list);
}
