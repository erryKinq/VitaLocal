// Food diary in VitaLocal: the day's meals with editable entries, adding food (own catalog while
// typing, Open Food Facts on demand, barcode by typing or a USB scanner, quick entries), the food
// and dish editor, body weight and the one-time Waistline takeover.

import { ApiError, api } from "./api.js";
import { foldable, todayIso, uuid4 } from "./checkin.js";
import { h, s } from "./dom.js";
import * as f from "./format.js";
import { lang, t } from "./i18n.js";
import { icon } from "./icons.js";
import { ask, card, iconBtn, menu, sheet } from "./ui.js";

export const MAIN = ["calories", "proteins", "carbohydrates", "fat"];
const QUICK = ["calories", "fat", "carbohydrates", "proteins"]; // order of a nutrition label
const SHORT_DE = { calories: "kcal", proteins: "P", carbohydrates: "KH", fat: "F" };
const SHORT_EN = { ...SHORT_DE, carbohydrates: "C" };
const short = (k) => (lang() === "de" ? SHORT_DE : SHORT_EN)[k];

let catalogPromise = null;
/** The nutrient catalog (key, unit, labels), loaded once. */
export const nutrientCatalog = () => (catalogPromise ||= api("/api/v1/nutrients").catch((e) => { catalogPromise = null; throw e; }));
const nLabel = (n) => (lang() === "de" ? n.label_de : n.label);

const unitText = (u) => (u === "portion" ? t("portion") : u || "");

/** Same rule as the server: nutrients per base amount × amount / base amount × quantity. */
export function scaled(nutrients, factor) {
  const out = {};
  for (const [k, v] of Object.entries(nutrients || {})) if (typeof v === "number") out[k] = v * factor;
  return out;
}

const macroLine = (n) => [
  n.calories != null ? `${f.num(n.calories)} kcal` : "– kcal",
  ...["fat", "carbohydrates", "proteins"].map((k) => `${short(k)} ${n[k] == null ? "–" : f.num(n[k], n[k] < 10 ? 1 : 0)}`),
].join(" · ");

/** A clock time on `day` in the server's time zone, as an ISO timestamp. */
const at = (day, hhmm) => f.zonedIso(day, hhmm);
const nowHHMM = () => f.clockOf(new Date());

function errorText(e) {
  if (e instanceof ApiError && e.status === 503) return t("Open Food Facts is not reachable right now.");
  if (e instanceof ApiError && e.status === 429) return t("Too many online lookups – try again in a minute.");
  return e.message;
}

// --- diary ---------------------------------------------------------------------------------------------

const GOAL_OF = { calories: "nutrition.energy", proteins: "nutrition.protein", carbohydrates: "nutrition.carbohydrates", fat: "nutrition.fat",
  "saturated-fat": "nutrition.saturated_fat", sugars: "nutrition.sugars" };
const LABEL_OF = { calories: "Energy", proteins: "Protein", carbohydrates: "Carbs", fat: "Fat", "saturated-fat": "of which saturated", sugars: "of which sugars" };
const SUB_OF = { fat: "saturated-fat", carbohydrates: "sugars" };
const COLOR_OF = { calories: "var(--cat-food)", proteins: "var(--c-protein)", carbohydrates: "var(--c-carbs)", fat: "var(--c-fat)" };

/** Energy as a ring against its goal, the macros as bars against theirs (from GET /goals). */
/** Tooltip for a day's burned energy (GET /days → energy): where the number comes from. */
export function burnedTitle(e) {
  if (!e) return "";
  const parts = `${t("base")} ${f.num(e.rest)} + ${t("active")} ${f.num(e.active)} kcal`;
  return e.source === "watch" ? `${t("Watch estimate: resting metabolic rate + active energy")} (${parts})`
    : e.source === "manual" ? `${t("Your own estimate of an everyday burn, plus the day's workouts")} (${parts})`
      : `${t("Estimated from your profile (BMR × activity level) and the day's workouts")} (${parts})`;
}

function daySummary(total, goals, energy) {
  const burned = energy?.total ?? null;
  const goal = (k) => (goals || []).find((g) => g.metric === GOAL_OF[k]);
  const target = (g) => (g ? g.max_value ?? g.min_value : null);
  const kcal = total.calories.value, kg = goal("calories"), kt = target(kg);
  const share = kt ? Math.min(1, (kcal || 0) / kt) : 0;
  const r = 34, c = 2 * Math.PI * r;
  const ring = h("div", { class: "kcal-ring" },
    s("svg", { viewBox: "0 0 80 80", width: 96, height: 96, "aria-hidden": "true", html:
      `<circle cx="40" cy="40" r="${r}" class="ring-track"/><circle cx="40" cy="40" r="${r}" class="ring-value${kg?.status === "above" ? " over" : ""}"
        stroke-dasharray="${(share * c).toFixed(1)} ${c.toFixed(1)}" transform="rotate(-90 40 40)"/>` }),
    h("div", { class: "kcal-ring-text" }, h("b", {}, kcal == null ? "–" : f.num(kcal)),
      h("span", {}, kt ? t("of {n} kcal", { n: f.num(kt) }) : "kcal")));
  const row = (k, { sub = false, color = COLOR_OF[k] } = {}) => {
    const v = total[k]?.value, g = goal(k), tg = target(g);
    return h("div", { class: ["macro-row", sub && "macro-sub"] },
      h("div", { class: "macro-row-head" }, h("span", {}, t(LABEL_OF[k])),
        h("span", { class: "num" }, h("b", {}, v == null ? "–" : `${f.num(v, sub && v < 10 ? 1 : 0)} g`), tg ? h("span", { class: "muted" }, ` / ${f.num(tg)} g`) : null)),
      h("div", { class: "track goal-track" }, h("span", { style: { width: `${tg ? Math.min(100, Math.round(((v || 0) / tg) * 100)) : 0}%`, background: color } })));
  };
  // a click on fat shows the saturated fat, on carbohydrates the sugars
  const bars = ["proteins", "carbohydrates", "fat"].map((k) => {
    const subKey = SUB_OF[k];
    if (!subKey) return row(k);
    const sub = row(subKey, { sub: true, color: COLOR_OF[k] });
    sub.hidden = true;
    const head = row(k);
    head.classList.add("expandable");
    head.title = t("Show {what}", { what: t(LABEL_OF[subKey]) });
    head.addEventListener("click", () => { sub.hidden = !sub.hidden; head.classList.toggle("open", !sub.hidden); });
    return h("div", {}, head, sub);
  });
  // energy balance against what was burned (watch, or estimated from profile + training)
  const balance = burned != null && kcal != null ? Math.round(kcal - burned) : null;
  const est = energy && energy.source !== "watch" ? ` (${t("estimated")})` : "";
  const bal = h("div", { class: "balance", title: burnedTitle(energy) },
    h("span", { class: "muted small" }, t("Balance")),
    h("b", { class: ["num", balance == null ? "muted" : balance < 0 ? "deficit" : "surplus"] },
      balance == null ? "–" : `${balance > 0 ? "+" : balance < 0 ? "−" : "±"}${f.num(Math.abs(balance))}`),
    h("span", { class: "muted small" }, balance == null
      ? (burned == null ? h("a", { href: "#/settings/profile" }, t("Set up your profile to estimate the burn")) : null)
      : `${balance < 0 ? t("deficit") : t("surplus")} · ${t("burned ≈ {n} kcal", { n: f.num(burned) })}${est}`));
  return h("div", { class: "day-summary" }, ring, h("div", { class: "macro-rows" }, bars), bal);
}

/**
 * The day's diary card. `n` is GET /nutrition/{day}; `reload()` redraws the page after a change.
 */
export function diaryCard(day, n, reload, goals = [], energy = null) {
  const meals = n.meals.filter((m) => m.index != null).map((m) => m.name);
  const total = n.totals;
  return card({ title: t("Food diary"), icon: "tools-kitchen-2", cls: "diary-card" }, null,
    daySummary(total, goals, energy),
    h("div", { class: "meals" }, n.meals.map((m) => mealBlock(day, m, meals, reload))));
}

function mealBlock(day, m, meals, reload) {
  const slot = h("div");
  const status = h("span", { class: "small muted" });
  const open = () => {
    if (slot.firstChild) { slot.replaceChildren(); return; }
    slot.replaceChildren(addPanel({ day, mealIndex: m.index ?? 0, meals, onDone: reload, onClose: () => slot.replaceChildren() }));
  };
  const copyYesterday = async () => {
    status.textContent = t("Saving …");
    try {
      const y = await api(`/api/v1/nutrition/${f.addDays(day, -1)}`);
      const src = (y.meals.find((x) => x.index === m.index)?.entries) || [];
      if (!src.length) { status.textContent = t("Nothing logged in this meal yesterday."); return; }
      await api("/api/v1/nutrition/copy", { method: "POST", json: {
        day, meal_index: m.index, entered_via: "webui", items: src.map((e) => ({ from: e.id, id: uuid4() })) } });
      reload();
    } catch (e) { status.textContent = t("Not saved: {msg}", { msg: e.message }); }
  };
  const saveTemplate = async () => {
    const items = m.entries.filter((e) => e.food_id && e.amount).map((e) => ({ food_id: e.food_id, amount: e.amount, quantity: e.quantity || 1 }));
    if (!items.length) return;
    const name = await ask({ title: t("Save as template"), icon: "bookmark", label: t("Name of the template"), value: m.name });
    if (!name) return;
    try {
      await api("/api/v1/templates", { method: "POST", json: { id: uuid4(), name, items } });
      status.textContent = t("Saved as template “{name}”", { name });
    } catch (e) { status.textContent = t("Not saved: {msg}", { msg: e.message }); }
  };
  const breakdown = h("div", { class: "meal-breakdown", hidden: true });
  const toggle = () => {
    breakdown.hidden = !breakdown.hidden;
    if (!breakdown.hidden) breakdown.replaceChildren(mealBreakdown(m.entries));
  };
  const tot = m.totals;
  const macros = m.entries.length ? ["fat", "carbohydrates", "proteins"].map((k) => `${short(k)} ${tot[k] == null ? "–" : f.num(tot[k])}`).join(" · ") : "";
  return h("div", { class: "meal-block" },
    h("div", { class: "meal-head" },
      h("div", { class: "meal-title" }, h("b", {}, m.name),
        m.entries.length ? h("button", { class: "link-btn kcal-btn", onclick: toggle, title: t("Show the composition of this meal") },
          `${f.num(tot.calories ?? 0)} kcal`, icon("chevron-down", { size: 14 })) : h("span", { class: "muted small" }, t("nothing yet")),
        macros ? h("span", { class: "muted small num" }, macros) : null),
      m.index != null ? h("div", { class: "row-actions" }, status,
        h("button", { class: "ghost", onclick: open, title: t("Add food") }, icon("plus", { size: 16 }), t("Add")),
        menu([
          { label: t("Like yesterday"), icon: "repeat", onclick: copyYesterday },
          m.entries.some((e) => e.food_id) ? { label: t("Save as template"), icon: "bookmark", onclick: saveTemplate } : null,
          m.entries.length ? { label: t("Composition"), icon: "chart-line", onclick: toggle } : null,
        ]))
        : null),
    breakdown,
    m.entries.length ? h("div", { class: "table-wrap" }, h("table", { class: "diary" }, h("tbody", {},
      m.entries.map((e) => entryRow(e, meals, reload))))) : null,
    slot);
}

const MACROS = [["fat", "Fat", 9, "var(--c-fat)"], ["carbohydrates", "Carbs", 4, "var(--c-carbs)"], ["proteins", "Protein", 4, "var(--c-protein)"]];
const DETAILS = [["saturated-fat", "Saturated fat"], ["sugars", "Sugars"], ["fiber", "Fiber"], ["salt", "Salt"]];

/** Energy, macros (grams and share of energy) and further nutrients of a set of entries. */
export function mealBreakdown(entries) {
  const sum = (k) => entries.reduce((a, e) => a + (e.nutrients?.[k] || 0), 0); // undeclared counts as 0
  const energy = MACROS.map(([k, , factor]) => sum(k) * factor);
  const all = energy.reduce((a, b) => a + b, 0) || 1;
  const pct = (i) => Math.round((energy[i] / all) * 100);
  return h("div", {},
    h("div", { class: "macro-bar" }, MACROS.map(([, , , color], i) => h("span", { style: { width: `${pct(i)}%`, background: color } }))),
    h("div", { class: "sleep-stats" },
      h("span", {}, h("span", { class: "muted" }, `${t("Energy")} `), h("b", {}, `${f.num(sum("calories"))} kcal`)),
      MACROS.map(([k, label], i) => h("span", {}, h("span", { class: "muted" }, `${t(label)} `),
        h("b", {}, `${f.num(sum(k), sum(k) < 10 ? 1 : 0)} g`), h("span", { class: "muted small" }, ` · ${pct(i)} %`)))),
    h("div", { class: "sleep-stats small" }, DETAILS.map(([k, label]) => h("span", {}, h("span", { class: "muted" }, `${t(label)} `),
      `${f.num(sum(k), 1)} g`))));
}

function entryRow(e, meals, reload) {
  const status = h("span", { class: "small muted" });
  const put = async (patch) => {
    status.textContent = "…";
    try {
      await api(`/api/v1/nutrition/entries/${e.id}`, { method: "PUT", json: {
        logged_at: e.logged_at || `${e.day}T12:00:00Z`, meal_index: e.meal_index ?? 0,
        amount: e.amount, quantity: e.quantity ?? 1, ...patch } });
      reload();
    } catch (ex) { status.textContent = ex.message; }
  };
  const amount = e.amount == null ? null : h("input", { type: "number", class: "num-input", min: 0, step: "any", value: e.amount,
    "aria-label": t("Amount"), onchange: (ev) => Number(ev.target.value) > 0 && put({ amount: Number(ev.target.value) }) });
  const qty = h("input", { type: "number", class: "num-input qty", min: 0, step: "any", value: e.quantity ?? 1,
    "aria-label": t("Quantity"), title: t("Quantity"), onchange: (ev) => Number(ev.target.value) > 0 && put({ quantity: Number(ev.target.value) }) });
  const del = async () => {
    try { await api(`/api/v1/nutrition/entries/${e.id}`, { method: "DELETE" }); reload(); } catch (ex) { status.textContent = ex.message; }
  };
  // correct the food itself; this entry then takes the new values (others keep theirs)
  const editFood = async () => {
    try {
      const food = await api(`/api/v1/foods/${e.food_id}`);
      const sh = sheet({ title: t("Edit food"), icon: "pencil", wide: true, body: [
        h("div", { class: "muted small" }, t("This entry takes the corrected values; earlier entries keep theirs.")),
        foodForm(food, { onSaved: async () => { sh.close(); await put({ recompute: true }); }, onCancel: () => sh.close() })] });
    } catch (ex) { status.textContent = ex.message; }
  };
  const actions = menu([
    e.food_id ? { label: t("Edit food"), icon: "pencil", onclick: editFood } : null,
    { heading: t("Move to") },
    ...meals.map((name, i) => (i === e.meal_index ? null : { label: name, icon: "arrow-right", onclick: () => put({ meal_index: i }) })),
    "-",
    { label: t("Delete"), icon: "trash", danger: true, onclick: del },
  ]);
  const n = e.nutrients || {};
  const title = h("td", { class: "excerpt" }, e.name, e.brand ? h("span", { class: "muted small" }, ` · ${e.brand}`) : null);
  if (!e.editable) {
    return h("tr", {}, title,
      h("td", { class: "r muted" }, e.amount != null ? `${f.num(e.amount * (e.quantity || 1), 1)} ${unitText(e.unit)}` : ""),
      h("td", { class: "r" }, n.calories != null ? `${f.num(n.calories)} kcal` : "–"),
      h("td", { class: "r muted small" }, macroLine(n).split(" · ").slice(1).join(" · ")), h("td"));
  }
  return h("tr", {}, title,
    h("td", { class: "r edit-amount" }, amount, amount ? ` ${unitText(e.unit)} ×` : "×", qty),
    h("td", { class: "r" }, n.calories != null ? `${f.num(n.calories)} kcal` : "–"),
    h("td", { class: "r muted small" }, macroLine(n).split(" · ").slice(1).join(" · ")),
    h("td", { class: "r" }, h("span", { class: "row-actions" }, status, actions)));
}

// --- adding food -----------------------------------------------------------------------------------------

/**
 * Search (own catalog while typing, OFF on demand), barcode, quick entry, new food. Clicked foods
 * go into a selection with one amount each (the last one suggested), logged together.
 */
export function addPanel({ day, mealIndex, meals, onDone, onClose }) {
  const body = h("div");
  const status = h("div", { class: "small muted" });
  const results = h("div", { class: "food-results" });
  const q = h("input", { type: "search", placeholder: t("Search foods …"), autocomplete: "off", maxlength: 100 });
  const code = h("input", { type: "text", inputmode: "numeric", placeholder: t("Barcode"), class: "barcode-input", maxlength: 20,
    title: t("Type a barcode or scan it with a USB scanner, then Enter") });

  const sel = selection({ day, mealIndex, meals, onDone, onChange: () => {
    for (const el of results.querySelectorAll(".food-hit[data-id]")) el.classList.toggle("selected", sel.has(el.dataset.id));
  } });
  // a click adds the food to the selection, a second one takes it out again
  const choose = (food, { toggle = false } = {}) => {
    if (toggle && sel.has(food.id)) sel.remove(food.id); else sel.add(food);
    body.replaceChildren(searchBox);
  };
  const newFood = (draft = {}) => body.replaceChildren(foodForm(null, { draft,
    onSaved: (food) => choose(food), onCancel: () => body.replaceChildren(searchBox) }));
  const hit = (food, { online = false } = {}) => h("button", { class: ["food-hit", sel.has(food.food_id || food.id) && "selected"], type: "button",
    "data-id": online ? food.food_id || undefined : food.id, onclick: async () => {
      if (!online) return choose(food, { toggle: true });
      if (food.food_id) return choose(await api(`/api/v1/foods/${food.food_id}`), { toggle: true });
      status.textContent = t("Saving …");
      try { choose(await api(`/api/v1/foods/online/${food.barcode}`, { method: "POST" })); status.textContent = ""; } catch (e) { status.textContent = errorText(e); }
    } },
  h("span", {}, food.favorite ? icon("star", { size: 14, cls: "fav" }) : "", h("b", {}, food.name), food.brand ? h("span", { class: "muted" }, ` · ${food.brand}`) : null,
    online ? h("span", { class: "badge" }, "OFF") : null, h("span", { class: "hit-check" }, icon("check", { size: 14 }))),
  h("span", { class: "muted small" },
    `${macroLine(food.nutrients || {})} / ${f.num(food.base_amount)} ${unitText(food.base_unit)}`,
    food.usage?.last_amount != null ? ` · ${t("last {amount}", { amount: `${f.num(food.usage.last_amount * (food.usage.last_quantity || 1))} ${unitText(food.base_unit)}` })}` : ""));

  // what the list shows: recent (default), favorites, dishes or templates; typing searches within it
  let mode = "recent";
  const chipsRow = h("div", { class: "chips" });
  const drawChips = () => chipsRow.replaceChildren(...[["recent", "Recent"], ["favorites", "Favorites"], ["dishes", "Dishes"], ["templates", "Templates"]]
    .map(([k, label]) => h("button", { type: "button", class: ["chip", mode === k ? "active" : ""], onclick: () => { mode = k; drawChips(); searchLocal(); } }, t(label))));
  drawChips();
  const pickTemplate = (tpl) => body.replaceChildren(templateForm(tpl, { day, mealIndex, meals, onSaved: onDone, onCancel: () => body.replaceChildren(searchBox) }));
  const templateHit = (tpl) => h("button", { class: "food-hit", type: "button", onclick: () => pickTemplate(tpl) },
    h("span", {}, tpl.favorite ? icon("star", { size: 14, cls: "fav" }) : "", h("b", {}, tpl.name), h("span", { class: "muted" }, ` · ${t("{n} foods", { n: tpl.items.length })}`)),
    h("span", { class: "muted small" }, `${macroLine(tpl.nutrients)} · ${tpl.items.map((i) => i.name).join(", ")}`));

  let seq = 0;
  const searchLocal = async () => {
    const id = ++seq;
    const query = q.value.trim();
    try {
      if (mode === "templates") {
        const rows = (await api("/api/v1/templates")).filter((x) => x.name.toLowerCase().includes(query.toLowerCase()));
        if (id !== seq) return;
        results.replaceChildren(...(rows.length ? rows.map(templateHit)
          : [h("div", { class: "muted small" }, t("No templates yet: “Save as template” on a meal, or on the Foods page."))]));
        return;
      }
      const params = { q: query, limit: mode === "recent" ? 20 : 200 };
      if (mode === "favorites") params.favorites = true;
      if (mode === "dishes") params.kind = "dish";
      const rows = await api("/api/v1/foods", { params });
      if (id !== seq) return;
      results.replaceChildren(...(rows.length ? rows.map((x) => hit(x))
        : [h("div", { class: "muted small" }, mode === "favorites" ? t("No favorites yet (star in the amount form).")
          : mode === "dishes" ? t("No dishes yet (Foods page → New dish).")
          : query ? t("Nothing in your foods. Search online?") : t("No foods yet: search online, scan a barcode or create one."))]));
    } catch (e) { status.textContent = e.message; }
  };
  let timer = null;
  q.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(searchLocal, 200); });
  q.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); clearTimeout(timer); searchLocal(); } });
  const searchOnline = async () => {
    if (q.value.trim().length < 2) { status.textContent = t("Type at least two letters."); return; }
    status.textContent = t("Searching Open Food Facts …");
    try {
      const rows = await api("/api/v1/foods/online", { params: { q: q.value.trim() } });
      status.textContent = rows.length ? "" : t("Nothing found online.");
      results.replaceChildren(...rows.map((x) => hit(x, { online: true })));
    } catch (e) { status.textContent = errorText(e); }
  };
  const lookup = async () => {
    const c = code.value.replace(/\D/g, "");
    if (!c) return;
    status.textContent = t("Looking up barcode …");
    try { choose(await api(`/api/v1/foods/barcode/${c}`)); } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        status.replaceChildren(t("Barcode {code} is unknown, also at Open Food Facts.", { code: c }), " ",
          h("button", { class: "link-btn", onclick: () => newFood({ barcode: c }) }, t("Create food")));
      } else status.textContent = errorText(e);
    }
  };
  code.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); lookup(); } });

  const searchBox = h("div", { class: "add-panel" },
    h("div", { class: "toolbar" }, q, h("button", { type: "button", onclick: searchOnline }, t("Search online")), code,
      h("button", { type: "button", onclick: () => body.replaceChildren(quickForm({ day, mealIndex, meals, onSaved: onDone, onCancel: () => body.replaceChildren(searchBox) })) }, t("Quick entry")),
      h("button", { type: "button", onclick: () => newFood({ name: q.value.trim() }) }, t("New food")),
      h("button", { type: "button", class: "link-btn", onclick: onClose }, t("Close"))),
    chipsRow, status, sel.el, results);
  body.append(searchBox);
  searchLocal();
  requestAnimationFrame(() => q.focus());
  return body;
}

/** The star that marks a food as favorite (saved at once). */
function favoriteStar(food) {
  const btn = h("button", { type: "button", class: ["link-btn", "star", food.favorite && "on"], title: t("Favorite"), "aria-pressed": String(!!food.favorite) }, icon("star"));
  btn.addEventListener("click", async () => {
    const next = !food.favorite;
    try {
      const body = { name: food.name, brand: food.brand, barcode: food.barcode, base_amount: food.base_amount, base_unit: food.base_unit,
        portions: food.portions || [], nutrients: food.nutrients || {}, favorite: next, archived: !!food.archived };
      const saved = await api(`/api/v1/foods/${food.id}`, { method: "PUT", json: body });
      food.favorite = saved.favorite;
      btn.classList.toggle("on", food.favorite);
      btn.setAttribute("aria-pressed", String(food.favorite));
    } catch (e) { btn.title = e.message; }
  });
  return btn;
}

/** Log a template: one entry per food, at the chosen meal and time. */
function templateForm(tpl, { day, mealIndex, meals, onSaved, onCancel }) {
  const meal = mealSelect(meals, mealIndex);
  const time = h("input", { type: "time", value: day === todayIso() ? nowHHMM() : "12:00" });
  const status = h("span", { class: "small muted" });
  return h("form", { class: "form add-panel", onsubmit: async (e) => {
    e.preventDefault();
    status.textContent = t("Saving …");
    try {
      await api(`/api/v1/templates/${tpl.id}/log`, { method: "POST", json: {
        logged_at: at(day, time.value || "12:00"), meal_index: Number(meal.value), entered_via: "webui",
        ids: tpl.items.map(() => uuid4()) } });
      onSaved?.();
    } catch (ex) { status.textContent = t("Not saved: {msg}", { msg: ex.message }); }
  } },
  h("b", {}, tpl.name),
  h("ul", { class: "template-items" }, tpl.items.map((i) => h("li", {}, `${i.name} · ${f.num(i.amount * i.quantity)} ${unitText(i.unit)}`,
    h("span", { class: "muted small" }, ` · ${f.num(i.nutrients.calories ?? 0)} kcal`)))),
  h("div", { class: "small" }, macroLine(tpl.nutrients)),
  h("div", { class: "toolbar small" }, meal, time),
  h("div", { class: "muted small" }, t("Each food becomes its own entry; amounts can be changed afterwards.")),
  h("div", { class: "toolbar" }, h("button", { class: "primary", type: "submit" }, t("Log")),
    h("button", { type: "button", onclick: onCancel }, t("Back")), status));
}

function mealSelect(meals, index) {
  return h("select", {}, meals.map((name, i) => h("option", { value: i, selected: i === index }, name)));
}

/**
 * The selected foods, each with portion chips, amount × count and its values; one meal and time for
 * all. `add(food)` puts a food in (with its last amount), the form logs one entry per food.
 */
function selection({ day, mealIndex, meals, onDone, onChange }) {
  const items = new Map(); // food id → { food, amount, qty, row }
  const rows = h("div", { class: "selection-rows" });
  const meal = mealSelect(meals, mealIndex);
  const time = h("input", { type: "time", value: day === todayIso() ? nowHHMM() : "12:00" });
  const total = h("div", { class: "small" });
  const status = h("span", { class: "small muted" });
  const values = (it) => {
    const a = Number(it.amount.value), n = Number(it.qty.value);
    return a > 0 && n > 0 ? scaled(it.food.nutrients, (a / it.food.base_amount) * n) : null;
  };
  const update = () => {
    const sum = {};
    let complete = true;
    for (const it of items.values()) {
      const v = values(it);
      it.preview.textContent = v ? macroLine(v) : "–";
      if (!v) { complete = false; continue; }
      for (const [k, x] of Object.entries(v)) sum[k] = (sum[k] || 0) + x;
    }
    total.textContent = items.size > 1 ? `${t("Together")}: ${macroLine(sum)}` : "";
    logBtn.disabled = !items.size || !complete;
    logBtn.textContent = items.size > 1 ? t("Log {n} foods", { n: items.size }) : t("Log");
    el.hidden = !items.size;
  };
  const remove = (id) => { items.get(id)?.row.remove(); items.delete(id); update(); onChange?.(); };
  const add = (food) => {
    if (items.has(food.id)) return;
    const u = food.usage || {};
    // last amount, else the first portion of a portion food, else the base amount (100 g/ml)
    const start = u.last_amount ?? (food.base_unit === "portion" ? food.portions?.[0]?.amount : null) ?? food.base_amount;
    const amount = h("input", { type: "number", class: "num-input", min: 0, step: "any", value: start, required: true, "aria-label": t("Amount") });
    const qty = h("input", { type: "number", class: "num-input qty", min: 0, step: "any", value: u.last_quantity ?? 1, "aria-label": t("Quantity") });
    const preview = h("span", { class: "muted small" });
    const chip = (label, value) => h("button", { type: "button", class: "chip", onclick: () => { amount.value = value; qty.value = 1; update(); } }, label);
    const row = h("div", { class: "selection-row" },
      h("div", { class: "selection-name" }, favoriteStar(food), h("b", {}, food.name), food.brand ? h("span", { class: "muted" }, ` · ${food.brand}`) : null,
        iconBtn("x", t("Remove"), () => remove(food.id))),
      h("div", { class: "chips" },
        ...(food.portions || []).map((p) => chip(`${p.label} (${f.num(p.amount)} ${unitText(food.base_unit)})`, p.amount)),
        food.base_unit !== "portion" ? chip(`${f.num(food.base_amount)} ${food.base_unit}`, food.base_amount) : null,
        u.last_amount != null ? chip(t("last: {amount}", { amount: `${f.num(u.last_amount)} ${unitText(food.base_unit)}` }), u.last_amount) : null),
      h("div", { class: "toolbar small" }, amount, h("span", {}, unitText(food.base_unit)), h("span", {}, "×"), qty, preview));
    amount.addEventListener("input", update);
    qty.addEventListener("input", update);
    items.set(food.id, { food, amount, qty, preview, row });
    rows.append(row);
    update();
    onChange?.();
  };
  const logBtn = h("button", { class: "primary", type: "submit" }, t("Log"));
  const el = h("form", { class: "selection", hidden: true, onsubmit: async (e) => {
    e.preventDefault();
    status.textContent = t("Saving …");
    try {
      const loggedAt = at(day, time.value || "12:00");
      for (const it of items.values()) {
        await api("/api/v1/nutrition/entries", { method: "POST", json: {
          id: uuid4(), logged_at: loggedAt, meal_index: Number(meal.value),
          food_id: it.food.id, amount: Number(it.amount.value), quantity: Number(it.qty.value) || 1, entered_via: "webui" } });
        remove(it.food.id); // logged: a retry after an error sends only the rest
      }
      onDone?.();
    } catch (ex) { status.textContent = t("Not saved: {msg}", { msg: ex.message }); }
  } },
  rows, total,
  h("div", { class: "toolbar small" }, meal, time, logBtn, status));
  return { el, add, remove, has: (id) => items.has(id) };
}

function quickForm({ day, mealIndex, meals, onSaved, onCancel }) {
  const name = h("input", { type: "text", placeholder: t("What, e.g. cake at work"), required: true, maxlength: 200 });
  const nums = Object.fromEntries(QUICK.map((k) => [k, h("input", { type: "number", class: "num-input", min: 0, step: "any",
    placeholder: k === "calories" ? "kcal" : `${short(k)} g` })]));
  const meal = mealSelect(meals, mealIndex);
  const time = h("input", { type: "time", value: day === todayIso() ? nowHHMM() : "12:00" });
  const status = h("span", { class: "small muted" });
  return h("form", { class: "form add-panel", onsubmit: async (e) => {
    e.preventDefault();
    const nutrients = {};
    for (const [k, el] of Object.entries(nums)) if (el.value !== "") nutrients[k] = Number(el.value);
    try {
      await api("/api/v1/nutrition/entries", { method: "POST", json: {
        id: uuid4(), logged_at: at(day, time.value || "12:00"), meal_index: Number(meal.value),
        name: name.value.trim(), nutrients, entered_via: "webui" } });
      onSaved?.();
    } catch (ex) { status.textContent = t("Not saved: {msg}", { msg: ex.message }); }
  } },
  h("b", {}, t("Quick entry")), name,
  h("div", { class: "toolbar small" }, ...QUICK.flatMap((k) => [nums[k], k === "calories" ? "kcal" : `g ${t(k === "proteins" ? "Protein" : k === "carbohydrates" ? "Carbs" : "Fat")}`])),
  h("div", { class: "toolbar small" }, meal, time),
  h("div", { class: "muted small" }, t("Empty fields stay unknown (not 0).")),
  h("div", { class: "toolbar" }, h("button", { class: "primary", type: "submit" }, t("Log")),
    h("button", { type: "button", onclick: onCancel }, t("Back")), status));
}

// --- food and dish editor ----------------------------------------------------------------------------------

/**
 * Create or edit a food (`food` null = new; `draft` prefills name/barcode). A dish gets components;
 * its nutrients are then computed by the server.
 */
export function foodForm(food, { draft = {}, dish = false, onSaved, onCancel, onDeleted } = {}) {
  const x = food || { name: draft.name || "", brand: "", barcode: draft.barcode || "", base_amount: dish ? 1 : 100,
    base_unit: dish ? "portion" : "g", portions: [], nutrients: {}, components: [], favorite: false, archived: false };
  const isDish = dish || (x.components || []).length > 0;
  const name = h("input", { type: "text", value: x.name, required: true, maxlength: 200, placeholder: t("Name") });
  const brand = h("input", { type: "text", value: x.brand || "", maxlength: 200, placeholder: t("Brand") });
  const barcode = h("input", { type: "text", inputmode: "numeric", value: x.barcode || "", maxlength: 20, placeholder: t("Barcode") });
  const base = h("input", { type: "number", class: "num-input", min: 0, step: "any", value: x.base_amount, required: true });
  const unit = h("select", {}, [["g", "g"], ["ml", "ml"], ["portion", t("portion")]].map(([v, l]) => h("option", { value: v, selected: v === x.base_unit }, l)));
  unit.addEventListener("change", () => {
    for (const el of portionRows.querySelectorAll(".p-unit")) el.textContent = unitText(unit.value);
  });
  const fav = h("input", { type: "checkbox", checked: x.favorite });
  const arch = h("input", { type: "checkbox", checked: x.archived });
  const status = h("span", { class: "small muted" });

  const nutrientInputs = {};
  const mainBox = h("div", { class: "nutrient-grid" });
  const moreBox = h("div", { class: "nutrient-grid" });
  nutrientCatalog().then((cat) => {
    for (const n of cat) {
      const el = h("input", { type: "number", class: "num-input", min: 0, step: "any", value: x.nutrients?.[n.key] ?? "", disabled: isDish });
      nutrientInputs[n.key] = el;
      (n.form ? mainBox : moreBox).append(h("label", { class: "form-row" }, `${nLabel(n)} (${n.unit})`, el));
    }
  }).catch((e) => { status.textContent = e.message; });

  const portionRows = h("div", { class: "portion-rows" });
  const addPortion = (p = { label: "", amount: "" }) => {
    const row = h("div", { class: "toolbar small" },
      h("input", { type: "text", value: p.label, placeholder: t("e.g. 1 slice"), maxlength: 60, class: "p-label" }),
      h("input", { type: "number", class: "num-input p-amount", min: 0, step: "any", value: p.amount }),
      h("span", { class: "muted p-unit" }, unitText(unit.value)),
      iconBtn("x", t("Remove"), () => row.remove()));
    portionRows.append(row);
  };
  (x.portions || []).forEach(addPortion);

  const compRows = h("div", { class: "portion-rows" });
  const addComp = (c) => {
    const row = h("div", { class: "toolbar small", "data-id": c.food_id },
      h("span", {}, c.name),
      h("input", { type: "number", class: "num-input c-amount", min: 0, step: "any", value: c.amount ?? "" }),
      h("span", { class: "muted" }, unitText(c.unit)),
      iconBtn("x", t("Remove"), () => row.remove()));
    compRows.append(row);
  };
  (x.components || []).forEach(addComp);
  const compSearch = h("input", { type: "search", placeholder: t("Add ingredient …") });
  const compHits = h("div", { class: "food-results" });
  compSearch.addEventListener("input", async () => {
    const qv = compSearch.value.trim();
    if (qv.length < 2) { compHits.replaceChildren(); return; }
    const rows = await api("/api/v1/foods", { params: { q: qv, limit: 8 } }).catch(() => []);
    compHits.replaceChildren(...rows.filter((r) => r.id !== x.id).map((r) => h("button", { type: "button", class: "food-hit", onclick: () => {
      addComp({ food_id: r.id, name: r.name, unit: r.base_unit,
        amount: r.usage?.last_amount != null ? r.usage.last_amount * (r.usage.last_quantity || 1) : r.portions?.[0]?.amount ?? r.base_amount });
      compSearch.value = ""; compHits.replaceChildren();
    } }, h("b", {}, r.name), h("span", { class: "muted small" }, ` ${macroLine(r.nutrients)} / ${f.num(r.base_amount)} ${unitText(r.base_unit)}`))));
  });

  const save = async (e) => {
    e.preventDefault();
    const nutrients = {};
    for (const [k, el] of Object.entries(nutrientInputs)) if (el.value !== "") nutrients[k] = Number(el.value);
    for (const [k, v] of Object.entries(x.nutrients || {})) if (!(k in nutrientInputs) && typeof v === "number") nutrients[k] = v; // unknown keys stay
    const portions = [...portionRows.children].map((r) => ({ label: r.querySelector(".p-label").value.trim(), amount: Number(r.querySelector(".p-amount").value) }))
      .filter((p) => p.label && p.amount > 0);
    const body = { name: name.value.trim(), brand: brand.value.trim() || null, barcode: barcode.value.trim() || null,
      base_amount: Number(base.value), base_unit: unit.value, portions, nutrients, favorite: fav.checked, archived: arch.checked };
    if (isDish) {
      body.components = [...compRows.children].map((r) => ({ food_id: r.dataset.id, amount: Number(r.querySelector(".c-amount").value) }))
        .filter((c) => c.amount > 0);
      if (!body.components.length) { status.textContent = t("A dish needs at least one ingredient."); return; }
    }
    status.textContent = t("Saving …");
    try {
      const saved = food
        ? await api(`/api/v1/foods/${food.id}`, { method: "PUT", json: body })
        : await api("/api/v1/foods", { method: "POST", json: { id: uuid4(), ...body } });
      status.textContent = t("Saved");
      onSaved?.(saved);
    } catch (ex) { status.textContent = t("Not saved: {msg}", { msg: ex.message }); }
  };

  return h("form", { class: "form add-panel", onsubmit: save },
    h("b", {}, food ? t("Edit food") : isDish ? t("New dish") : t("New food")),
    h("div", { class: "toolbar" }, name, brand, isDish ? null : barcode),
    h("div", { class: "toolbar small" }, isDish ? t("Makes") : t("Nutrients per"), base, unit,
      food?.source && food.source !== "vitalocal" ? h("span", { class: "badge" }, food.source === "off" ? "Open Food Facts" : "Waistline") : null),
    isDish ? h("div", {}, h("div", { class: "muted small" }, t("Ingredients (amount in their unit); nutrients are added up when saving.")),
      compRows, compSearch, compHits) : null,
    mainBox,
    h("details", {}, h("summary", { class: "small muted" }, t("More nutrients")), moreBox),
    h("div", {}, h("div", { class: "muted small" }, t("Portions")), portionRows,
      h("button", { type: "button", class: "link-btn", onclick: () => addPortion() }, t("+ Portion"))),
    h("div", { class: "toolbar small" }, h("label", { class: "form-check" }, fav, t("Favorite")),
      food ? h("label", { class: "form-check" }, arch, t("Archived")) : null),
    h("div", { class: "muted small" }, t("Empty fields stay unknown (not 0). Changes apply to new entries; logged ones keep their values.")),
    h("div", { class: "toolbar" }, h("button", { class: "primary", type: "submit" }, t("Save")),
      onCancel ? h("button", { type: "button", onclick: onCancel }, t("Back")) : null,
      food ? h("button", { type: "button", class: "link-btn", onclick: async () => {
        if (!confirm(t("Delete “{name}”? Foods already logged are archived instead.", { name: food.name }))) return;
        try { await api(`/api/v1/foods/${food.id}`, { method: "DELETE" }); onDeleted?.(); } catch (ex) { status.textContent = ex.message; }
      } }, t("Delete")) : null,
      status));
}

/** Page #/nutrition/foods: the own catalog with search, editor for foods and dishes. */
export async function foodsView(ctx) {
  const editor = h("div");
  const list = h("div");
  const q = h("input", { type: "search", placeholder: t("Search foods …"), value: ctx.params.get("q") || "" });
  const archived = h("input", { type: "checkbox" });
  const edit = (food, opts = {}) => {
    editor.replaceChildren(card(food ? food.name : opts.dish ? t("New dish") : t("New food"), null,
      foodForm(food, { ...opts, onSaved: () => { editor.replaceChildren(); load(); }, onCancel: () => editor.replaceChildren(),
        onDeleted: () => { editor.replaceChildren(); load(); } })));
    editor.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  const load = async () => {
    const rows = await api("/api/v1/foods", { params: { q: q.value.trim(), limit: 500, include_archived: archived.checked, include_unused: true } });
    list.replaceChildren(rows.length ? h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, t("Name")), h("th", { class: "r" }, t("per")), h("th", { class: "r" }, "kcal"),
        h("th", { class: "r" }, t("Protein")), h("th", { class: "r" }, t("Carbs")), h("th", { class: "r" }, t("Fat")),
        h("th", {}, t("Source")), h("th", { class: "r" }, t("Uses")))),
      h("tbody", {}, rows.map((r) => h("tr", { class: ["link", r.archived ? "rest" : ""], onclick: async () => edit(await api(`/api/v1/foods/${r.id}`)) },
        h("td", { class: "excerpt" }, r.favorite ? icon("star", { size: 14, cls: "fav" }) : "", r.name, r.brand ? h("span", { class: "muted small" }, ` · ${r.brand}`) : null,
          r.components?.length ? h("span", { class: "badge" }, t("dish")) : null),
        h("td", { class: "r muted" }, `${f.num(r.base_amount)} ${unitText(r.base_unit)}`),
        ...["calories", "proteins", "carbohydrates", "fat"].map((k) => h("td", { class: "r" }, r.nutrients[k] == null ? "–" : f.num(r.nutrients[k], r.nutrients[k] < 10 ? 1 : 0))),
        h("td", { class: "muted small" }, { vitalocal: "VitaLocal", off: "Open Food Facts", waistline: "Waistline" }[r.source]),
        h("td", { class: "r muted" }, r.usage.count || "")))))) : h("div", { class: "muted" }, t("No foods found.")));
  };
  const templateList = h("div");
  const editTemplate = (tpl) => {
    editor.replaceChildren(card(tpl ? tpl.name : t("New template"), null,
      templateEditor(tpl, { onSaved: () => { editor.replaceChildren(); loadTemplates(); }, onCancel: () => editor.replaceChildren() })));
    editor.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  const loadTemplates = async () => {
    const rows = await api("/api/v1/templates", { params: { include_archived: true } });
    templateList.replaceChildren(rows.length ? h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, t("Name")), h("th", {}, t("Foods")), h("th", { class: "r" }, "kcal"),
        h("th", { class: "r" }, t("Protein")), h("th", { class: "r" }, t("Uses")))),
      h("tbody", {}, rows.map((r) => h("tr", { class: ["link", r.archived ? "rest" : ""], onclick: () => editTemplate(r) },
        h("td", {}, r.favorite ? icon("star", { size: 14, cls: "fav" }) : "", r.name),
        h("td", { class: "excerpt muted small" }, r.items.map((i) => i.name).join(", ")),
        h("td", { class: "r" }, f.num(r.nutrients.calories ?? 0)),
        h("td", { class: "r" }, f.num(r.nutrients.proteins ?? 0)),
        h("td", { class: "r muted" }, r.uses || "")))))) : h("div", { class: "muted small" }, t("No templates yet.")));
  };
  let timer = null;
  q.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(load, 200); });
  archived.addEventListener("change", load);
  await Promise.all([load(), loadTemplates()]);
  return h("div", {},
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, t("Foods")), h("div", { class: "muted small" }, t("Your own catalog: created here, taken over from Open Food Facts or Waistline."))),
      h("div", { class: "toolbar" }, h("a", { class: "btn ghost", href: "#/nutrition" }, icon("arrow-left", { size: 16 }), t("Food diary")),
        h("button", { onclick: () => edit(null) }, t("New food")), h("button", { onclick: () => edit(null, { dish: true }) }, t("New dish")),
        h("button", { onclick: () => editTemplate(null) }, t("New template")))),
    editor,
    card(t("Templates"), h("span", { class: "muted small" }, t("Logged as one entry per food; a dish is one entry.")), templateList),
    h("div", { style: { marginTop: "16px" } },
      card(t("Catalog"), h("span", { class: "toolbar small" }, q, h("label", { class: "form-check" }, archived, t("Show archived"))), list)));
}

/** Create or edit a meal template: name, foods with amounts, favorite; delete. */
function templateEditor(tpl, { onSaved, onCancel }) {
  const name = h("input", { type: "text", value: tpl?.name || "", required: true, maxlength: 200, placeholder: t("Name") });
  const fav = h("input", { type: "checkbox", checked: !!tpl?.favorite });
  const arch = h("input", { type: "checkbox", checked: !!tpl?.archived });
  const rows = h("div", { class: "portion-rows" });
  const status = h("span", { class: "small muted" });
  const addRow = (i) => {
    const row = h("div", { class: "toolbar small", "data-id": i.food_id },
      h("span", { class: "goal-edit-label" }, i.name),
      h("input", { type: "number", class: "num-input t-amount", min: 0, step: "any", value: i.amount }),
      h("span", { class: "muted" }, `${unitText(i.unit)} ×`),
      h("input", { type: "number", class: "num-input qty t-qty", min: 0, step: "any", value: i.quantity ?? 1 }),
      iconBtn("x", t("Remove"), () => row.remove()));
    rows.append(row);
  };
  (tpl?.items || []).forEach(addRow);
  const search = h("input", { type: "search", placeholder: t("Add food …") });
  const hits = h("div", { class: "food-results" });
  search.addEventListener("input", async () => {
    const qv = search.value.trim();
    if (qv.length < 2) { hits.replaceChildren(); return; }
    const found = await api("/api/v1/foods", { params: { q: qv, limit: 8 } }).catch(() => []);
    hits.replaceChildren(...found.map((r) => h("button", { type: "button", class: "food-hit", onclick: () => {
      addRow({ food_id: r.id, name: r.name, unit: r.base_unit, amount: r.usage?.last_amount ?? r.portions?.[0]?.amount ?? r.base_amount, quantity: 1 });
      search.value = ""; hits.replaceChildren();
    } }, h("b", {}, r.name), h("span", { class: "muted small" }, ` ${macroLine(r.nutrients)} / ${f.num(r.base_amount)} ${unitText(r.base_unit)}`))));
  });
  return h("form", { class: "form add-panel", onsubmit: async (e) => {
    e.preventDefault();
    const items = [...rows.children].map((r) => ({ food_id: r.dataset.id, amount: Number(r.querySelector(".t-amount").value),
      quantity: Number(r.querySelector(".t-qty").value) || 1 })).filter((i) => i.amount > 0);
    if (!items.length) { status.textContent = t("A template needs at least one food."); return; }
    const body = { name: name.value.trim(), items, favorite: fav.checked, archived: arch.checked };
    try {
      if (tpl) await api(`/api/v1/templates/${tpl.id}`, { method: "PUT", json: body });
      else await api("/api/v1/templates", { method: "POST", json: { id: uuid4(), ...body } });
      onSaved?.();
    } catch (ex) { status.textContent = t("Not saved: {msg}", { msg: ex.message }); }
  } },
  name, rows, search, hits,
  h("div", { class: "toolbar small" }, h("label", { class: "form-check" }, fav, t("Favorite")),
    tpl ? h("label", { class: "form-check" }, arch, t("Archived")) : null),
  h("div", { class: "toolbar" }, h("button", { class: "primary", type: "submit" }, t("Save")),
    h("button", { type: "button", onclick: onCancel }, t("Back")),
    tpl ? h("button", { type: "button", class: "link-btn", onclick: async () => {
      if (!confirm(t("Delete template “{name}”? Logged entries stay.", { name: tpl.name }))) return;
      await api(`/api/v1/templates/${tpl.id}`, { method: "DELETE" });
      onSaved?.();
    } }, t("Delete")) : null,
    status));
}

// --- body weight ------------------------------------------------------------------------------------------

/** Enter today's (or the day's) weight; the latest values with delete for own entries. */
export async function weightCard(day, reload) {
  const rows = await api("/api/v1/body", { params: { metric: "weight", from: f.addDays(day, -60), to: day } });
  const input = h("input", { type: "number", class: "num-input", min: 20, max: 900, step: 0.1, placeholder: f.weightUnit(), required: true });
  const status = h("span", { class: "small muted" });
  const ts = day === todayIso() ? () => new Date().toISOString() : () => at(day, "07:00");
  return card({ title: t("Weight"), icon: "scale" }, rows[0] ? h("span", { class: "muted small" }, `${t("latest")} ${f.bodyWeight(rows[0].value)} · ${f.mediumDate(f.dayOf(rows[0].ts))}`) : null,
    h("form", { class: "toolbar", onsubmit: async (e) => {
      e.preventDefault();
      try {
        await api("/api/v1/body", { method: "POST", json: { id: uuid4(), ts: ts(), metric: "weight", value: Math.round(f.weightKg(input.value) * 100) / 100, source: "webui" } });
        reload();
      } catch (ex) { status.textContent = t("Not saved: {msg}", { msg: ex.message }); }
    } }, input, f.weightUnit(), h("button", { class: "primary", type: "submit" }, t("Save")), status),
    rows.length ? foldable("weight", t("Latest entries ({n})", { n: Math.min(rows.length, 6) }), h("ul", { class: "checkin-list", style: { marginTop: "10px" } }, rows.slice(0, 6).map((r) => h("li", { class: "weight-row" },
      h("span", { class: "muted small" }, f.mediumDate(f.dayOf(r.ts))), h("b", {}, f.bodyWeight(r.value)),
      h("span", { class: "muted small" }, r.editable ? "VitaLocal" : r.source === "waistline" ? "Waistline" : r.source),
      r.editable ? iconBtn("x", t("Delete"), async () => {
        await api(`/api/v1/body/${r.id}`, { method: "DELETE" }); reload();
      }) : h("span"))))) : null);
}
