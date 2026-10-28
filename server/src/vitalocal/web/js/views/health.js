// Health: supplements (X of Y a day, active ingredients vs. EFSA upper levels) and lab results.

import { api } from "../api.js";
import { calendarHeatmap } from "../charts.js";
import { todayIso, uuid4 } from "../checkin.js";
import { h, responsive } from "../dom.js";
import * as f from "../format.js";
import { t } from "../i18n.js";
import { icon } from "../icons.js";
import { card, dayNav, iconBtn, latestWriter, menu, pageHead, sheet, stat } from "../ui.js";
import { labsSection } from "./labs.js";

const YEAR = 365;
const FORMS = [["capsule", "capsule(s)"], ["tablet", "tablet(s)"], ["drops", "drop(s)"], ["powder", "serving(s)"], ["other", "unit(s)"]];
const formLabel = (form) => t((FORMS.find(([k]) => k === form) || FORMS[4])[1]);
const streakText = (n, unit = "days") => t(unit === "weeks" ? (n === 1 ? "{n} week" : "{n} weeks")
  : (n === 1 ? "{n} day in a row" : "{n} days in a row"), { n });
/** "2 capsules a day", or for some days a week "1 capsule · 1× per week (this week 1/1)". */
const scheduleText = (s) => (s.per_week >= 7 ? `${s.per_day} ${formLabel(s.form)} ${t("a day")}`
  : `${s.per_day} ${formLabel(s.form)} · ${t("{n}× per week", { n: s.per_week })} (${t("this week {n}/{m}", { n: s.week_count ?? 0, m: s.per_week })})`);

/**
 * X of Y per supplement for one day with −/+; `st` from /supplements/status (day widget, health page).
 * Counts change in place and are saved in the background; `onChange` runs after a save (e.g. to
 * update the ingredient totals) without rebuilding the list.
 */
export function supplementRows(st, day, onChange) {
  if (!st.supplements.length) {
    return h("div", { class: "muted small" }, t("No supplements yet. "), h("a", { href: "#/health" }, t("Add one")));
  }
  const err = h("div", { class: "error small", hidden: true });
  const row = (s) => {
    let count = s.count;
    const minus = h("button", { class: "habit-toggle", "aria-label": t("one less"), onclick: () => set(count - 1) }, icon("minus", { size: 16 }));
    const shown = h("b", { class: "num", style: { minWidth: "44px", textAlign: "center" } });
    const plus = h("button", { class: "habit-toggle", "aria-label": t("one more"), onclick: () => set(count + 1) }, icon("plus", { size: 16 }));
    const paint = () => {
      const done = count >= s.per_day;
      shown.textContent = `${count}/${s.per_day}`;
      shown.classList.toggle("ok", done);
      plus.classList.toggle("done", done);
      minus.disabled = !count;
    };
    const save = latestWriter((n) => api(`/api/v1/supplements/${s.id}/log/${day}`, { method: "PUT", json: { count: n } }), {
      onSaved: () => { err.hidden = true; onChange?.(); },
      onError: (e) => { err.hidden = false; err.textContent = e.message; },
    });
    const set = (n) => { count = Math.max(0, n); paint(); save(count); };
    paint();
    return h("div", { class: "habit-row" },
      h("span", { class: "toolbar" }, minus, shown, plus),
      h("div", {},
        h("div", { class: "habit-name" }, s.name, s.brand ? h("span", { class: "muted small" }, ` · ${s.brand}`) : null),
        h("div", { class: "muted small habit-sub" }, h("span", {}, [scheduleText(s), s.timing].filter(Boolean).join(" · ")),
          s.streak ? h("span", { class: "flame-num" }, icon("flame", { size: 13 }), streakText(s.streak, s.streak_unit)) : null)));
  };
  return h("div", { class: "habit-list" }, st.supplements.map(row), err);
}

/** The day's ingredient totals with the EFSA upper levels, closest to its upper level first, in a
 * box of fixed height that scrolls (the list gets long with a multivitamin). */
export function ingredientTable(ingredients) {
  if (!ingredients.length) return h("div", { class: "muted small" }, t("No ingredients recorded for this day."));
  ingredients = [...ingredients].sort((a, b) => (b.share_of_upper_level ?? -1) - (a.share_of_upper_level ?? -1) || a.label.localeCompare(b.label));
  return h("div", { class: "table-wrap scroll-box" }, h("table", { class: "sticky-head" },
    h("thead", {}, h("tr", {}, h("th", {}, t("Ingredient")), h("th", { class: "r" }, t("Today")),
      h("th", { class: "r" }, t("EFSA upper level")), h("th", {}, t("Source")))),
    h("tbody", {}, ingredients.map((i) => h("tr", {},
      h("td", { title: i.note || "" }, i.label),
      h("td", { class: ["r", "num", i.above_upper_level && "bad"] }, `${f.num(i.amount, i.amount % 1 ? 1 : 0)} ${i.unit}`),
      h("td", { class: "r num muted" }, i.upper_level
        ? `${f.num(i.upper_level)} ${i.unit}${i.share_of_upper_level != null ? ` · ${f.num(i.share_of_upper_level * 100)} %` : ""}` : "–"),
      h("td", { class: "muted small" }, (i.from || []).join(", ")))))));
}

export async function healthView(ctx, requested) {
  const today = todayIso();
  const day = requested || today;
  const [st, catalog, all, labs] = await Promise.all([
    api("/api/v1/supplements/status", { params: { day } }),
    api("/api/v1/supplements/ingredients"),
    api("/api/v1/supplements", { params: { include_archived: true } }),
    labsSection(ctx),
  ]);
  const refresh = () => ctx.rerender();
  const openForm = (s = null) => {
    const sh = sheet({ title: s ? t("Edit supplement") : t("New supplement"), icon: "pill", wide: true,
      body: [supplementForm(s, catalog, () => { sh.close(); refresh(); }, () => sh.close())] });
  };
  const archived = all.filter((s) => s.archived);
  const ingredientsBox = h("div");
  const ingredientsSummary = h("span");
  const paintIngredients = (x) => {
    const above = x.ingredients.filter((i) => i.above_upper_level);
    ingredientsSummary.textContent = above.length ? t("{n} above the upper level", { n: above.length }) : t("all below the upper levels");
    ingredientsSummary.className = above.length ? "warn-text" : "";
    ingredientsBox.replaceChildren(
      ...(above.length ? [h("div", { class: "notice warn small" }, t("Above the EFSA upper level: {list}. The level applies to the total intake including food.",
        { list: above.map((i) => i.label).join(", ") }))] : []),
      ingredientTable(x.ingredients));
  };
  paintIngredients(st);
  const ingredientsChanged = () => api("/api/v1/supplements/status", { params: { day } }).then(paintIngredients).catch(() => {});
  const ingredients = card({ title: t("Ingredients"), icon: "flask", collapsible: true, id: "health.ingredients", summary: ingredientsSummary }, null,
    h("div", { class: "muted small", style: { marginBottom: "8px" } }, t("from supplements only")), ingredientsBox);

  return h("div", {},
    pageHead({ icon: "heartbeat", title: t("Health"), sub: day === today ? f.longDate(day) : null, actions: [
      dayNav({ day, today, go: (x) => ctx.navigate(x === today ? "#/health" : `#/health/${x}`), addDays: f.addDays, t, mediumDate: f.mediumDate }),
      h("button", { class: "primary", onclick: () => openForm() }, icon("plus", { size: 16 }), t("New supplement")),
    ] }),
    h("div", { class: "grid-main" },
      card({ title: t("Supplements"), icon: "pill" }, null, supplementRows(st, day, ingredientsChanged)),
      ingredients),
    st.supplements.length ? supplementMatrix(ctx, st.supplements, today, openForm, catalog) : null,
    h("div", { style: { marginTop: "16px" } }, labs),
    archived.length ? card({ title: t("Archived ({n})", { n: archived.length }), icon: "history", collapsible: true, id: "health.archived", cls: "spaced" }, null,
      h("div", { class: "rows" }, archived.map((s) => h("div", { class: "row" }, h("span", { class: "grow" }, s.name),
        h("button", { class: "ghost", onclick: async () => { await api(`/api/v1/supplements/${s.id}`, { method: "PUT", json: { ...s, archived: false } }); refresh(); } },
          icon("refresh", { size: 16 }), t("restore")))))) : null);
}

function ingredientText(s, catalog) {
  return s.ingredients.map((i) => {
    const c = catalog.find((x) => x.key === i.key);
    return `${c ? c.label : i.name} ${f.num(i.amount, i.amount % 1 ? 1 : 0)} ${c ? c.unit : i.unit}`;
  }).join(", ");
}

const MATRIX_DAYS = 56;

/**
 * All supplements in one matrix: a row per supplement over the last eight weeks (taken fully,
 * partly, missed). A row opens its numbers, the whole year and edit/archive.
 */
function supplementMatrix(ctx, list, today, openForm, catalog) {
  const days = Array.from({ length: MATRIX_DAYS }, (_, i) => f.addDays(today, i - MATRIX_DAYS + 1));
  const rows = list.map((s) => {
    const detail = h("div", { class: "supp-detail", hidden: true });
    const cells = days.map((d) => {
      const e = s.days[d];
      const cls = e?.done ? "on" : e?.value ? "part" : "none";
      return h("i", { class: ["sm", cls, d === today && "today"], title: `${f.mediumDate(d)}: ${e ? `${e.value}/${s.per_day}` : "–"}` });
    });
    const head = h("button", { type: "button", class: "supp-row", "aria-expanded": "false", onclick: () => {
      detail.hidden = !detail.hidden;
      head.setAttribute("aria-expanded", String(!detail.hidden));
      if (!detail.hidden && !detail.firstChild) detail.append(supplementDetail(ctx, s, today, openForm, catalog));
    } },
    h("span", { class: "supp-name" }, h("b", {}, s.name), h("span", { class: "muted small" }, t("{n}/30", { n: s.done_last_30 }))),
    h("span", { class: "supp-cells", style: { gridTemplateColumns: `repeat(${MATRIX_DAYS}, 1fr)` } }, cells),
    icon("chevron-down", { size: 16, cls: "chev" }));
    return h("div", { class: "supp-item" }, head, detail);
  });
  return card({ title: t("Intake over eight weeks"), icon: "calendar-stats", cls: "spaced" },
    h("span", { class: "legend" }, h("span", {}, h("i", { class: "sm on" }), t("taken")), h("span", {}, h("i", { class: "sm part" }), t("partly")),
      h("span", {}, h("i", { class: "sm none" }), t("not taken"))),
    h("div", { class: "supp-matrix" }, rows));
}

function supplementDetail(ctx, s, today, openForm, catalog) {
  const values = {};
  for (const [d, v] of Object.entries(s.days)) if (v.done) values[d] = 1; // only days taken completely
  return h("div", {},
    h("div", { class: "toolbar", style: { justifyContent: "space-between" } },
      h("div", { class: "stat-row" },
        stat(t("Per day"), `${s.per_day} ${formLabel(s.form)}`),
        s.per_week < 7 ? stat(t("Days a week"), t("{n}× per week", { n: s.per_week })) : null,
        s.ingredients.length ? stat(t("Per unit"), ingredientText(s, catalog)) : null,
        stat(t("Current streak"), streakText(s.streak, s.streak_unit)),
        stat(t("Last 30 days"), t("{n} of the last {days} days", { n: s.done_last_30, days: 30 }))),
      menu([
        { label: t("Edit"), icon: "pencil", onclick: () => openForm(s) },
        { label: t("Archive"), icon: "history", onclick: async () => { await api(`/api/v1/supplements/${s.id}`, { method: "PUT", json: { ...s, archived: true } }); ctx.rerender(); } },
      ])),
    responsive(h("div"), (w) => calendarHeatmap({
      width: w, from: f.addDays(today, -YEAR + 1), to: today, values, levels: 1,
      describe: (d) => `${f.longDate(d)}: ${s.days[d] ? `${s.days[d].value}/${s.per_day}` : "–"}`,
      onSelect: (d) => ctx.navigate(`#/health/${d}`),
    })));
}

function supplementForm(s, catalog, done, cancel = done) {
  const input = (value, attrs = {}) => h("input", { type: "text", value: value ?? "", ...attrs });
  const name = input(s?.name, { maxlength: 80, required: true, class: "wide", placeholder: t("e.g. Vitamin D3, Magnesium") });
  const brand = input(s?.brand, { maxlength: 80, placeholder: t("optional") });
  const form = h("select", {}, FORMS.map(([k, l]) => h("option", { value: k, selected: k === (s?.form || "capsule") }, t(l))));
  const perDay = h("input", { type: "number", min: 1, max: 50, step: 1, value: s?.per_day ?? 1, class: "num-input" });
  const perWeek = h("select", {}, [7, 6, 5, 4, 3, 2, 1].map((n) =>
    h("option", { value: n, selected: n === (s?.per_week ?? 7) }, n === 7 ? t("daily") : t("{n}× per week", { n }))));
  const timing = input(s?.timing, { maxlength: 80, placeholder: t("e.g. mornings with breakfast") });
  const startedOn = h("input", { type: "date", value: s?.started_on || "" });
  const note = input(s?.note, { maxlength: 500, class: "wide", placeholder: t("optional") });
  const rows = h("div", { class: "stack-sm" });
  const addRow = (ing = {}) => {
    const kind = h("select", {}, [...catalog.map((c) => h("option", { value: c.key, selected: c.key === ing.key }, `${c.label} (${c.unit})`)),
      h("option", { value: "", selected: !!ing.name }, t("Other ingredient …"))]);
    const free = input(ing.name, { maxlength: 60, placeholder: t("Name") });
    const unit = input(ing.unit, { maxlength: 10, placeholder: t("Unit"), style: { width: "70px" } });
    const amount = h("input", { type: "number", min: 0, step: "any", value: ing.amount ?? "", required: true, class: "num-input", "aria-label": t("Amount per unit") });
    const sync = () => { free.hidden = unit.hidden = kind.value !== ""; };
    kind.addEventListener("change", sync);
    sync();
    const row = h("div", { class: "toolbar" }, kind, free, unit, amount, h("span", { class: "muted small" }, t("per unit")),
      iconBtn("x", t("Remove"), () => row.remove()));
    row.value = () => (kind.value ? { key: kind.value, amount: Number(amount.value) }
      : { name: free.value.trim(), unit: unit.value.trim(), amount: Number(amount.value) });
    rows.append(row);
  };
  (s?.ingredients || []).forEach(addRow);
  const err = h("div", { class: "error", hidden: true });

  const submit = async (e) => {
    e.preventDefault();
    const body = {
      id: s?.id || uuid4(), name: name.value.trim(), brand: brand.value.trim() || null, form: form.value,
      per_day: Number(perDay.value), per_week: Number(perWeek.value), timing: timing.value.trim() || null, note: note.value.trim() || null,
      started_on: startedOn.value || null,
      ingredients: [...rows.children].map((r) => r.value()), position: s?.position ?? 0, archived: false,
    };
    try {
      await api(s ? `/api/v1/supplements/${s.id}` : "/api/v1/supplements", { method: s ? "PUT" : "POST", json: body });
      done();
    } catch (ex) {
      err.hidden = false;
      err.textContent = ex.message;
    }
  };

  return h("form", { class: "form", onsubmit: submit },
      h("label", { class: "form-row" }, h("span", {}, t("Name")), name),
      h("div", { class: "toolbar" },
        h("label", { class: "form-row" }, h("span", {}, t("Brand")), brand),
        h("label", { class: "form-row" }, h("span", {}, t("Units per day")), h("span", { class: "toolbar" }, perDay, form)),
        h("label", { class: "form-row", title: t("Taken on some days only: the habit counts it on the days it is taken and misses it on Sunday if the weekly goal was not reached.") },
          h("span", {}, t("How often")), perWeek),
        h("label", { class: "form-row" }, h("span", {}, t("When")), timing),
        h("label", { class: "form-row", title: t("Also before it was logged here; lab results show since when it is taken.") }, h("span", {}, t("Taken since")), startedOn)),
      h("div", { class: "form-row" }, h("span", {}, t("Active ingredients per unit (for daily totals and upper levels)")), rows,
        h("div", {}, h("button", { type: "button", onclick: () => addRow() }, t("+ Ingredient")))),
      h("label", { class: "form-row" }, h("span", {}, t("Note")), note),
      err,
      h("div", { class: "toolbar" },
        h("button", { class: "primary", type: "submit" }, t("Save")),
        h("button", { type: "button", onclick: () => cancel() }, t("Cancel")),
        s ? h("button", { type: "button", class: "danger", onclick: async () => {
          if (!confirm(t("Delete this supplement including its history?"))) return;
          await api(`/api/v1/supplements/${s.id}`, { method: "DELETE" });
          done();
        } }, t("Delete")) : null));
}
