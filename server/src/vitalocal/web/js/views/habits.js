import { api } from "../api.js";
import { calendarHeatmap } from "../charts.js";
import { todayIso, uuid4 } from "../checkin.js";
import { h, responsive } from "../dom.js";
import * as f from "../format.js";
import { locale, t } from "../i18n.js";
import { PICKER, glyph, icon } from "../icons.js";
import { card, dayNav, iconBtn, latestWriter, menu, pageHead, sheet, stat, subnav, toast } from "../ui.js";
import { achievementsCard, challengesCard, flamePop, gardenCard, named, newBadges, recordTiles, rewardHero, routinesCard, templatesSheet } from "./habitRewards.js";

const YEAR = 365;

const schedule = (hb) => (hb.per_week >= 7 ? t("daily") : t("{n}× per week", { n: hb.per_week }));
// Clock metrics (bedtime, wake time, last caffeine): values are minutes after midnight.
const isClock = (def) => def?.unit === "clock";
const valueText = (v, def) => (isClock(def) ? f.hhmm(v) : f.num(v, v % 1 ? 1 : 0));
function bounds(hb, def) {
  const [lo, hi] = [hb.min_value, hb.max_value];
  if (lo != null && hi != null) return `${valueText(lo, def)}–${valueText(hi, def)}`;
  return lo != null ? `≥ ${valueText(lo, def)}` : `≤ ${valueText(hi, def)}`;
}
const rule = (hb, metricDef) => hb.metric
  ? `${t("automatic")}: ${t(metricDef(hb.metric)?.label || hb.metric)} ${bounds(hb, metricDef(hb.metric))}`
  : hb.kind === "count" ? `${t("goal")}: ${f.num(hb.target)} ${hb.unit || ""}`.trim() : "";
/** What an automatic habit measured on a day: "22:41 · +21 min", "8.412 · reached", "none". */
function measured(e, def) {
  if (e.none) return t("none");
  const shown = e.display ?? f.num(e.value, e.value % 1 ? 1 : 0);
  if (e.done) return `${shown} · ${t("reached")}`;
  return isClock(def) && e.delta ? `${shown} · ${e.delta > 0 ? "+" : "−"}${f.num(Math.abs(e.delta))} min` : `${shown} · ${t("not reached")}`;
}
const streakText = (hb) => t(hb.streak_unit === "weeks" ? (hb.streak === 1 ? "{n} week" : "{n} weeks")
  : (hb.streak === 1 ? "{n} day in a row" : "{n} days in a row"), { n: hb.streak });

/** Check-off list for one day (used on the habits page and in the journal). */
export async function habitCheckList(day, onChange) {
  const [status, defs] = await Promise.all([
    api("/api/v1/habits/status", { params: { from: f.addDays(day, -YEAR), to: day, today: day } }),
    api("/api/v1/habits/metrics"),
  ]);
  const metricDef = (k) => defs.find((m) => m.key === k);
  const box = h("div", { class: "habit-list" });
  if (!status.length) {
    box.append(h("div", { class: "muted small" }, t("No habits yet. "), h("a", { href: "#/habits" }, t("Create one"))));
    return box;
  }
  for (const hb of status) box.append(habitRow(hb, day, onChange, metricDef));
  return box;
}

function habitRow(hb, day, onChange, metricDef) {
  let entry = hb.days[day];
  const sub = h("div", { class: "muted small habit-sub" },
    h("span", {}, [schedule(hb), hb.metric || hb.kind === "count" ? rule(hb, metricDef) : null].filter(Boolean).join(" · ")),
    hb.streak ? h("span", { class: "flame-num", title: t("Current streak") }, icon("flame", { size: 13 }), streakText(hb)) : null,
    hb.jokers ? h("span", { title: t("Jokers") }, icon("cards", { size: 13 }), hb.jokers) : null,
    hb.hard ? h("span", { title: t("Hard habit: two flames per done day") }, icon("barbell", { size: 13 }), "×2") : null);
  const err = h("div", { class: "error small", hidden: true });
  // manual habits change in place and are saved in the background (no reload of the list)
  const save = latestWriter((value) => api(`/api/v1/habits/${hb.id}/log/${day}`, { method: "PUT", json: { value } }), {
    onSaved: () => { err.hidden = true; onChange?.(); },
    onError: (e) => { err.hidden = false; err.textContent = e.message; },
  });
  const log = (value) => {
    const was = !!entry?.done;
    entry = { value, done: hb.kind === "count" ? value >= (hb.target || 1) : value >= 1 };
    paint();
    if (entry.done && !was) flamePop(control);
    save(value);
  };
  let control;
  let paint = () => {};
  if (hb.metric) {
    control = entry
      ? h("span", { class: ["badge", entry.done ? "ok" : "warn"], title: t("evaluated from your data") }, measured(entry, metricDef(hb.metric)))
      : h("span", { class: "badge", title: t("no measurement for this day – not counted as missed") }, t("no data"));
  } else if (hb.kind === "count") {
    const input = h("input", { type: "number", min: 0, step: 1, value: entry?.value ?? "", class: "num-input", "aria-label": hb.name,
      onchange: (e) => log(Math.max(0, Number(e.target.value) || 0)) });
    const minus = h("button", { class: "habit-toggle", "aria-label": t("one less"), onclick: () => log(Math.max(0, (entry?.value || 0) - 1)) }, icon("minus", { size: 16 }));
    const plus = h("button", { class: "habit-toggle", "aria-label": t("one more"), onclick: () => log((entry?.value || 0) + 1) }, icon("plus", { size: 16 }));
    const ok = h("span", { class: "badge ok" }, icon("check", { size: 12 }));
    paint = () => {
      input.value = entry?.value ?? "";
      minus.disabled = !entry?.value;
      plus.classList.toggle("done", !!entry?.done);
      ok.hidden = !entry?.done;
    };
    control = h("span", { class: "toolbar" }, minus, input, plus, h("span", { class: "muted small" }, `/ ${f.num(hb.target)} ${hb.unit || ""}`), ok);
  } else {
    control = h("button", { class: "habit-toggle", onclick: () => log(entry?.done ? 0 : 1) });
    paint = () => {
      control.classList.toggle("done", !!entry?.done);
      control.setAttribute("aria-pressed", String(!!entry?.done));
      control.replaceChildren(entry?.done ? icon("check", { size: 18 }) : "");
    };
  }
  paint();
  return h("div", { class: "habit-row" },
    control,
    h("div", {}, h("div", { class: "habit-name" }, named(hb)), sub, err));
}

export async function habitsView(ctx, tab = "today") {
  const today = todayIso();
  const day = ctx.params.get("day") || today;
  const [status, metricDefs, all, rewards] = await Promise.all([
    api("/api/v1/habits/status", { params: { from: f.addDays(today, -YEAR + 1), to: today, today } }),
    api("/api/v1/habits/metrics"),
    api("/api/v1/habits", { params: { include_archived: true } }),
    api("/api/v1/habits/rewards"),
  ]);
  const metricDef = (k) => metricDefs.find((m) => m.key === k);
  const refresh = () => ctx.rerender();
  const openForm = (hb = null) => habitSheet(hb, metricDefs, refresh);
  let added = false;
  const openTemplates = () => templatesSheet(all, () => { added = true; }, () => { if (added) refresh(); });

  const head = pageHead({ icon: "plant-2", title: t("Habits"), actions: [
    tab === "today" ? dayNav({ day, today, go: (x) => ctx.navigate(x === today ? "#/habits" : `#/habits?day=${x}`), addDays: f.addDays, t, mediumDate: f.mediumDate }) : null,
    h("button", { class: "primary", onclick: () => openForm() }, icon("plus", { size: 16 }), t("New habit")),
    menu([{ label: t("Templates"), icon: "sparkles", onclick: openTemplates }]),
  ] });
  const tabs = subnav([
    { href: "#/habits", label: t("Today"), icon: "list-check", active: tab === "today" },
    { href: "#/habits/garden", label: t("Garden & achievements"), icon: "trophy", active: tab === "garden", count: rewards.new.length || null },
    { href: "#/habits/manage", label: t("Manage"), icon: "adjustments-horizontal", active: tab === "manage" },
  ]);

  let body;
  if (tab === "garden") body = gardenTab(rewards, refresh);
  else if (tab === "manage") body = manageTab(ctx, { status, all, rewards, today, metricDef, openForm, openTemplates });
  else body = await todayTab(ctx, { day, today, status, all, rewards, refresh });
  return h("div", { class: "area-habit" }, head, tabs, body);
}

/** Today: the banner with flames and level, the check list, this week's challenges and routines. */
async function todayTab(ctx, { day, today, status, all, rewards, refresh }) {
  let r = rewards;
  const heroBox = h("div"), weekBox = h("div", { class: "stack" });
  const paint = () => {
    heroBox.replaceChildren(rewardHero(r, status));
    weekBox.replaceChildren(challengesCard(r), routinesCard(r, all, { day, onChange: refresh }));
  };
  paint();
  const reloadRewards = async () => {
    const before = r.new.length;
    r = await api("/api/v1/habits/rewards");
    paint();
    if (r.new.length > before) {
      toast(r.new.length === 1 ? t("New achievement!") : t("{n} new achievements!", { n: r.new.length }),
        { icon: "trophy", action: [t("Show"), () => ctx.navigate("#/habits/garden")] });
    }
  };
  if (r.new.length) {
    setTimeout(() => toast(r.new.length === 1 ? t("New achievement!") : t("{n} new achievements!", { n: r.new.length }),
      { icon: "trophy", action: [t("Show"), () => ctx.navigate("#/habits/garden")] }), 300);
  }
  return h("div", {},
    heroBox,
    h("div", { class: "grid-main" },
      card({ title: day === today ? t("Today") : f.longDate(day), icon: "list-check" }, null, await habitCheckList(day, reloadRewards)),
      weekBox));
}

/** Garden and achievements: what the habits have grown into. New badges are marked once, then seen. */
function gardenTab(r, refresh) {
  const fresh = newBadges(r, () => {});
  if (r.new.length) api("/api/v1/habits/rewards/seen", { method: "POST" }).catch(() => {});
  return h("div", { class: "stack" },
    fresh,
    gardenCard(r),
    recordTiles(r),
    achievementsCard(r));
}

/** Manage: every habit with its numbers; details, edit and archive per habit; the calendar of all. */
function manageTab(ctx, { status, all, rewards, today, metricDef, openForm, openTemplates }) {
  const byId = new Map(rewards.habits.map((x) => [x.id, x]));
  const archived = all.filter((x) => x.archived);
  if (!status.length) {
    return card({ title: t("Habits"), icon: "plant-2" }, null, h("div", { class: "empty" }, icon("seedling", { size: 28 }),
      h("b", {}, t("Start your first habit")), h("span", { class: "small" }, t("Pick a template or create your own.")),
      h("div", { class: "toolbar" }, h("button", { onclick: openTemplates }, icon("sparkles", { size: 16 }), t("Templates")),
        h("button", { class: "primary", onclick: () => openForm() }, t("New habit")))));
  }
  return h("div", { class: "stack" },
    h("div", { class: "habit-manage" }, status.map((hb) => habitCard(ctx, hb, today, metricDef, openForm, byId.get(hb.id)))),
    overallCalendar(ctx, status, rewards, today),
    archived.length ? card({ title: t("Archived ({n})", { n: archived.length }), icon: "history", collapsible: true, id: "habits.archived" }, null,
      h("div", { class: "rows" }, archived.map((hb) => h("div", { class: "row" }, h("span", { class: "grow" }, named(hb)),
        h("button", { class: "ghost", onclick: async () => { await api(`/api/v1/habits/${hb.id}`, { method: "PUT", json: { ...hb, archived: false } }); ctx.rerender(); } },
          icon("refresh", { size: 16 }), t("restore")))))) : null);
}

const recordText = (x) => t(x.unit === "weeks" ? (x.best_streak === 1 ? "{n} week" : "{n} weeks") : (x.best_streak === 1 ? "{n} day" : "{n} days"), { n: x.best_streak });

/** One habit in the manage list: a summary line, opened it shows rule, records and its calendar. */
function habitCard(ctx, hb, today, metricDef, openForm, g) {
  const values = {};
  for (const [d, v] of Object.entries(hb.days)) if (v.done) values[d] = 1; // only days the goal was reached
  const put = async (patch) => { await api(`/api/v1/habits/${hb.id}`, { method: "PUT", json: { ...hb, ...patch } }); ctx.rerender(); };
  const actions = menu([
    { label: t("Edit"), icon: "pencil", onclick: () => openForm(hb) },
    { label: t("Archive"), icon: "history", onclick: () => put({ archived: true }) },
  ]);
  const summary = h("div", { class: "habit-line" },
    h("span", { class: "habit-ic" }, hb.icon ? glyph(hb.icon, { size: 20 }) : icon("circle-dot", { size: 20 })),
    h("div", { class: "grow" }, h("div", { class: "habit-name" }, hb.name),
      h("div", { class: "muted small" }, [schedule(hb), hb.metric || hb.kind === "count" ? rule(hb, metricDef) : null].filter(Boolean).join(" · "))),
    h("div", { class: "habit-nums" },
      h("span", { title: t("Last 30 days") }, h("b", {}, `${hb.done_last_30}`), h("small", {}, "/30")),
      h("span", { class: "flame-num", title: t("Current streak") }, icon("flame", { size: 14 }), hb.streak || 0),
      g?.best_streak ? h("span", { title: t("Record") }, icon("trophy", { size: 14 }), g.best_streak) : null),
    actions,
    icon("chevron-down", { cls: "chev" }));
  const el = h("details", { class: "card habit-card" }, h("summary", {}, summary),
    h("div", { class: "habit-detail" },
      h("div", { class: "stat-row" },
        stat(t("Current streak"), streakText(hb)),
        hb.streak_unit === "days" ? stat(t("Jokers"), hb.jokers ? String(hb.jokers) : "–") : null,
        g ? stat(t("Record"), recordText(g)) : null,
        g ? stat(t("Flames"), f.num(g.flames)) : null,
        stat(t("Last 30 days"), t("{n} of the last {days} days", { n: hb.done_last_30, days: 30 }))),
      g?.suggest_hard ? h("div", { class: "notice small hard-hint" },
        t("Done rarely in the last four weeks – mark it as hard? Hard habits earn two flames per day."), " ",
        h("button", { class: "link-btn", onclick: () => put({ hard: true }) }, t("Mark as hard"))) : null,
      g?.next_tier ? h("div", { class: "muted small next-line" }, t("next:"), glyph(g.next_tier.icon, { size: 14 }),
        `${t(g.next_tier.title)} · ${t(g.unit === "weeks" ? "{n} weeks" : "{n} days", { n: g.next_tier.n })}`,
        g.streak ? ` · ${t("{n} to go", { n: g.next_tier.n - g.streak })}` : "") : null,
      g?.tracked_from && hb.metric ? h("div", { class: "muted small" },
        t("Streaks and flames count from {date}; the calendar also shows the time before.", { date: f.mediumDate(g.tracked_from) })) : null,
      responsive(h("div", { style: { marginTop: "12px" } }), (w) => calendarHeatmap({
        width: w, from: f.addDays(today, -YEAR + 1), to: today, values, levels: 1,
        describe: (d) => {
          const e = hb.days[d];
          if (e?.joker) return `${f.longDate(d)}: ${t("missed – a joker kept the streak")}`;
          return `${f.longDate(d)}: ${e ? (hb.metric ? measured(e, metricDef(hb.metric)) : e.done ? t("done") : `${f.num(e.value)} · ${t("not reached")}`) : "–"}`;
        },
        onSelect: (d) => ctx.navigate(`#/habits?day=${d}`),
      }))));
  summary.querySelector(".menu-wrap").addEventListener("click", (e) => e.preventDefault());
  return el;
}

const MATRIX_DAYS = 28;

/** All habits in one calendar: the more done on a day, the stronger the colour; perfect days in
 * gold. Below, the last four weeks per habit, each row recognizable by its icon. */
function overallCalendar(ctx, status, r, today) {
  if (!status.length) return null;
  const perfect = new Set(r.perfect.days);
  const tracked = new Map(r.habits.map((x) => [x.id, x.tracked_from]));
  const values = {};
  const doneOn = {};
  for (const hb of status) {
    for (const [d, v] of Object.entries(hb.days)) {
      if (!v.done) continue;
      values[d] = (values[d] || 0) + 1;
      (doneOn[d] ||= []).push(hb.name);
    }
  }
  const go = (d) => ctx.navigate(`#/habits?day=${d}`);
  const days = Array.from({ length: MATRIX_DAYS }, (_, i) => f.addDays(today, i - MATRIX_DAYS + 1));
  const wd = new Intl.DateTimeFormat(locale(), { weekday: "narrow", timeZone: "UTC" });
  const cell = (hb, d) => {
    const e = hb.days[d];
    const from = tracked.get(hb.id);
    let cls = "none", label = "–";
    if (e?.done) { cls = "on"; label = t("done"); }
    else if (e?.joker) { cls = "joker"; label = t("joker"); }
    else if (d === today) { cls = "open"; label = t("open"); }
    else if (hb.per_week < 7) { cls = "none"; label = t("not done"); }
    else if (hb.metric ? e : from && d >= from) { cls = "miss"; label = t("missed"); }
    return h("td", { class: ["mx", cls, perfect.has(d) && "perfect"], title: `${hb.name} · ${f.mediumDate(d)}: ${label}`, onclick: () => go(d) },
      cls === "joker" ? icon("cards", { size: 12 }) : "");
  };
  return card({ title: t("All habits"), icon: "calendar-stats", collapsible: true, id: "habits.calendar", summary: t("the more done, the stronger the colour; gold = perfect day") }, null,
    responsive(h("div"), (w) => calendarHeatmap({
      width: w, from: f.addDays(today, -YEAR + 1), to: today, values, levels: 4,
      classFor: (d) => (perfect.has(d) ? "cal-perfect" : null),
      describe: (d, v) => `${f.longDate(d)}: ${v ? t("{n} done", { n: v }) : t("nothing done")}${perfect.has(d) ? ` · ${t("perfect day")}` : ""}${doneOn[d] ? ` · ${doneOn[d].join(", ")}` : ""}`,
      onSelect: go,
    })),
    h("h3", { class: "medal-head" }, t("Last four weeks")),
    h("div", { class: "table-scroll" }, h("table", { class: "habit-matrix" },
      h("thead", {}, h("tr", {}, h("th", {}, ""), days.map((d) => h("th", { class: ["mx-head", perfect.has(d) && "perfect"], title: f.mediumDate(d) },
        h("div", {}, wd.format(new Date(`${d}T00:00:00Z`))), h("div", {}, d.slice(8)))))),
      h("tbody", {}, status.map((hb) => h("tr", {},
        h("th", { class: "mx-name", title: hb.name }, named(hb, 14)),
        days.map((d) => cell(hb, d))))))),
    h("div", { class: "mx-legend muted small" },
      h("span", {}, h("i", { class: "mx on" }), t("done")), h("span", {}, h("i", { class: "mx miss" }), t("missed")),
      h("span", {}, icon("cards", { size: 12 }), " ", t("joker")), h("span", {}, h("i", { class: "mx none" }), t("no data / not due"))));
}

/** New or edit habit in a sheet; `done` after saving or deleting. */
function habitSheet(hb, metricDefs, done) {
  const sh = sheet({ title: hb ? t("Edit habit") : t("New habit"), icon: "plant-2", body: [habitForm(hb, metricDefs, () => { sh.close(); done(); }, () => sh.close())] });
}

function habitForm(hb, metricDefs, done, cancel) {
  const name = h("input", { type: "text", value: hb?.name || "", maxlength: 80, required: true, class: "wide", placeholder: t("e.g. Stretching, No alcohol, Read") });
  // the icon is stored as an emoji (as from the app and the templates) and drawn as its line icon
  let chosen = hb?.icon || "";
  const custom = h("input", { type: "text", value: PICKER.includes(chosen) ? "" : chosen, maxlength: 16, class: "icon-input", "aria-label": t("Own emoji"), placeholder: t("Emoji"),
    oninput: (e) => { chosen = e.target.value.trim(); paintPicks(); } });
  const hard = h("input", { type: "checkbox", checked: !!hb?.hard });
  const iconPicks = h("div", { class: "icon-picks" });
  const paintPicks = () => iconPicks.replaceChildren(...PICKER.map((x) =>
    h("button", { type: "button", class: ["icon-pick", x.replace(/\uFE0F/g, "") === chosen.replace(/\uFE0F/g, "") && "active"], "aria-label": x,
      onclick: () => { chosen = x; custom.value = ""; paintPicks(); } }, glyph(x, { size: 20 }))));
  paintPicks();
  const mode = h("select", {},
    [["check", t("Tick off")], ["count", t("Count")], ["auto", t("Automatic from data")]].map(([v, l]) =>
      h("option", { value: v, selected: v === (hb?.metric ? "auto" : hb?.kind || "check") }, l)));
  const unit = h("input", { type: "text", value: hb?.unit || "", maxlength: 20, placeholder: t("e.g. pages") });
  const target = h("input", { type: "number", min: 0, step: "any", value: hb?.target ?? "", class: "num-input" });
  const perWeek = h("select", {}, [7, 6, 5, 4, 3, 2, 1].map((n) =>
    h("option", { value: n, selected: n === (hb?.per_week ?? 7) }, n === 7 ? t("daily") : t("{n}× per week", { n }))));
  const collator = new Intl.Collator(locale(), { sensitivity: "base", numeric: true });
  const metricOptions = metricDefs.map((m) => [m.key, `${t(m.label)}${m.unit && m.unit !== "clock" ? ` (${m.unit})` : ""}`]).sort((a, b) => collator.compare(a[1], b[1]));
  const metric = h("select", {}, metricOptions.map(([k, l]) => h("option", { value: k, selected: k === hb?.metric }, l)));
  // Bounds: numbers, or times of day for clock metrics (a window like 21:50–22:20; either side may stay empty).
  const minIn = h("input", { class: "num-input" });
  const maxIn = h("input", { class: "num-input" });
  const minLabel = h("span", { class: "muted small" });
  const maxLabel = h("span", { class: "muted small" });
  const clockNow = () => isClock(metricDefs.find((m) => m.key === metric.value));
  const syncBounds = (initial = false) => {
    const clock = clockNow();
    for (const [el, v] of [[minIn, hb?.min_value], [maxIn, hb?.max_value]]) {
      el.type = clock ? "time" : "number";
      el.step = clock ? "60" : "any";
      el.style.width = clock ? "auto" : "";
      el.value = initial && v != null ? (clock ? f.hhmm(v) : v) : "";
    }
    minLabel.textContent = clock ? t("from") : t("at least");
    maxLabel.textContent = clock ? t("until") : t("at most");
  };
  metric.addEventListener("change", () => syncBounds());
  syncBounds(true);
  const bound = (el) => (el.value === "" ? null : clockNow() ? f.minutesOf(el.value) : Number(el.value));
  const err = h("div", { class: "error", hidden: true });

  const countRow = h("div", { class: "toolbar" }, h("span", { class: "muted small" }, t("Daily goal")), target, unit);
  const autoRow = h("div", { class: "stack-sm" }, h("div", { class: "toolbar" }, metric),
    h("div", { class: "toolbar" }, minLabel, minIn, maxLabel, maxIn),
    h("div", { class: "muted small" }, t("Leave a side empty for an open bound. Bedtime counts for the evening, wake time for the morning.")));
  const sync = () => { countRow.hidden = mode.value !== "count"; autoRow.hidden = mode.value !== "auto"; };
  mode.addEventListener("change", sync);
  sync();

  const submit = async (e) => {
    e.preventDefault();
    const auto = mode.value === "auto";
    const body = {
      id: hb?.id || uuid4(), name: name.value.trim(), icon: chosen || null, template: hb?.template ?? null, kind: mode.value === "count" ? "count" : "check",
      unit: mode.value === "count" ? unit.value.trim() || null : null,
      target: mode.value === "count" && target.value !== "" ? Number(target.value) : null,
      per_week: Number(perWeek.value),
      metric: auto ? metric.value : null,
      min_value: auto ? bound(minIn) : null, max_value: auto ? bound(maxIn) : null,
      position: hb?.position ?? 0, archived: false, hard: hard.checked,
    };
    try {
      await api(hb ? `/api/v1/habits/${hb.id}` : "/api/v1/habits", { method: hb ? "PUT" : "POST", json: body });
      done();
    } catch (ex) {
      err.hidden = false;
      err.textContent = ex.message;
    }
  };

  return h("form", { class: "form", onsubmit: submit },
      h("label", { class: "form-row" }, h("span", {}, t("Name")), name),
      h("div", { class: "form-row" }, h("span", {}, t("Icon")), iconPicks, h("div", { class: "toolbar small" }, t("or your own emoji:"), custom)),
      h("div", { class: "toolbar" }, h("label", { class: "form-row" }, h("span", {}, t("Type")), mode),
        h("label", { class: "form-row" }, h("span", {}, t("Goal")), perWeek)),
      h("label", { class: "form-check" }, hard, t("Hard habit: two flames per done day")),
      countRow, autoRow,
      err,
      h("div", { class: "toolbar" },
        h("button", { class: "primary", type: "submit" }, t("Save")),
        h("button", { type: "button", onclick: () => cancel() }, t("Cancel")),
        hb ? h("button", { type: "button", class: "danger", onclick: async () => {
          if (!confirm(t("Delete this habit including its history?"))) return;
          await api(`/api/v1/habits/${hb.id}`, { method: "DELETE" });
          done();
        } }, t("Delete")) : null));
}
