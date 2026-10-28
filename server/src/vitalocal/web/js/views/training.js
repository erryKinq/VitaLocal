// Strength training: next workout with the progression's suggestions, review of watch sessions
// (plan day, exercise per set, warm-up/hidden, added sets, rating per exercise), manual workouts,
// plans with split days, the exercise library and stats (hard sets per muscle group, estimated 1RM,
// best holds of time exercises) with a paged workout history.

import { aiOn, api } from "../api.js";
import { bodyFigure, bodyMap, byGroup, hardSetsOf, scaleFill, setBodyFigure } from "../bodyMap.js";
import { debriefBox } from "./coach.js";
import { labChart } from "../charts.js";
import { uuid4 } from "../checkin.js";
import { h, responsive } from "../dom.js";
import * as f from "../format.js";
import { lang, t } from "../i18n.js";
import { icon } from "../icons.js";
import { ask, card, iconBtn, pageHead, subnav } from "../ui.js";
import { readinessCard } from "./insights.js";

const T = "/api/v1/training";
const RATINGS = [["easy", "too easy"], ["good", "good"], ["hard", "too hard"]];
const ratingLabel = (r) => t((RATINGS.find(([k]) => k === r) || [null, "–"])[1]);
/** Server texts like "3×8 @ 11.5 kg" with the decimal comma in German. */
const loc = (s) => (lang() === "de" ? String(s).replace(/(\d)\.(\d)/g, "$1,$2") : String(s));
const num = (v) => (v == null || v === "" ? null : Number(v));
const kg = (v) => (v == null ? "" : `${f.num(v, v % 1 ? 1 : 0)} kg`);

// --- Garmin exercise catalog ----------------------------------------------------------------------

let catalogPromise = null;
/** The library's exercises ("Kurzhantel-Curl · Library") first, then every Garmin exercise
 * ("Dumbbell biceps curl · Curl") plus each category as a whole. */
const garminCatalog = () => (catalogPromise ??= Promise.all([libraryEntries(), api("/api/v1/exercises")]).then(([lib, cats]) => [
  ...lib.map((e) => ({ library: e, category: e.garmin_category, name: e.garmin_name, label: `${e.name} · ${t("Library")}` })),
  ...cats.flatMap((c) => [
    { category: c.category, name: null, label: `${f.humanize(c.category)} (${t("any variant")})` },
    ...c.names.map((n) => ({ category: c.category, name: n, label: `${f.humanize(n)} · ${f.humanize(c.category)}` })),
  ]),
]));

let libraryPromise = null;
const libraryEntries = () => (libraryPromise ??= api(`${T}/library`).catch((e) => { libraryPromise = null; throw e; }));

/** The own exercise for a catalog entry, created on first use. `exercises` is updated in place. */
async function ensureExercise(exercises, e) {
  if (e.library) {
    const ex = await api(`${T}/library/${e.library.key}/adopt`, { method: "POST" });
    libraryPromise = catalogPromise = null;  // exercise_id changed
    const i = exercises.findIndex((x) => x.id === ex.id);
    if (i < 0) exercises.push(ex); else exercises[i] = ex;
    return ex;
  }
  const found = exercises.find((x) => !x.archived && x.garmin_category === e.category && (x.garmin_name || null) === e.name);
  if (found) return found;
  const base = f.humanize(e.name || e.category);
  for (const name of [base, `${base} (${f.humanize(e.category)})`]) {
    try {
      const ex = await api(`${T}/exercises`, { method: "POST", json: { id: uuid4(), name, garmin_category: e.category, garmin_name: e.name } });
      exercises.push(ex);
      return ex;
    } catch (err) {
      if (err.status !== 409) throw err;
    }
  }
  throw new Error(t("an exercise named {name} exists already", { name: base }));
}

/** Searchable catalog input; `onPick(entry)` gets the chosen catalog entry. */
function catalogSearch(onPick, { autofocus = false, onCancel = null } = {}) {
  const list = h("datalist", { id: `g-${uuid4()}` });
  const input = h("input", { type: "text", list: list.id, class: "wide", placeholder: t("search the library or the Garmin catalog, e.g. Curl, Kniebeuge, bench press") });
  garminCatalog().then((entries) => {
    list.replaceChildren(...entries.map((e) => h("option", { value: e.label })));
    input.addEventListener("change", () => {
      const e = entries.find((x) => x.label === input.value);
      if (e) onPick(e);
    });
  });
  if (onCancel) {
    input.addEventListener("keydown", (ev) => { if (ev.key === "Escape") onCancel(); });
    input.addEventListener("blur", () => setTimeout(() => { if (input.isConnected && !input.value.trim()) onCancel(); }, 250));
  }
  if (autofocus) requestAnimationFrame(() => input.focus());
  return h("span", { class: "catalog-search" }, input, list);
}

/** Adds "+ from the Garmin catalog …" to an exercise <select>. The picked entry becomes an own
 * exercise, appears in every exercise picker of the page and is selected here. */
function withCatalog(select, exercises, valueOf, onAdded) {
  let last = select.value;
  select.classList.add("exercise-pick");
  select.dataset.prefix = valueOf("");
  select.append(h("option", { value: "garmin" }, t("+ from the library or the Garmin catalog …")));
  select.addEventListener("change", () => {
    if (select.value !== "garmin") { last = select.value; return; }
    const restore = (value) => {
      search.remove();
      select.hidden = false;
      select.value = value;
      last = value;
      select.dispatchEvent(new Event("input", { bubbles: true }));
      select.dispatchEvent(new Event("change", { bubbles: true }));
    };
    const search = catalogSearch(async (entry) => {
      try {
        const ex = await ensureExercise(exercises, entry);
        onAdded?.(ex);
        for (const s of document.querySelectorAll("select.exercise-pick")) {
          const v = `${s.dataset.prefix}${ex.id}`;
          if ([...s.options].some((o) => o.value === v)) continue;
          const group = s.querySelector("optgroup[data-all]") || s;
          group.insertBefore(h("option", { value: v }, ex.name), group === s ? s.querySelector("option[value=garmin]") : null);
        }
        restore(valueOf(ex.id));
      } catch (err) { alert(err.message); restore(last); }
    }, { autofocus: true, onCancel: () => restore(last) });
    select.hidden = true;
    select.after(search);
  });
  return select;
}

/** Creates an exercise for every category/name the watch recorded that no exercise covers yet. */
async function createFromWatch(suggestions) {
  let n = 0;
  for (const s of suggestions) {
    try {
      await api(`${T}/exercises`, { method: "POST", json: { id: uuid4(), name: f.humanize(s.garmin_name || s.garmin_category),
        garmin_category: s.garmin_category, garmin_name: s.garmin_name || null } });
      n++;
    } catch { /* name taken: that exercise exists already */ }
  }
  return n;
}

// --- overview ------------------------------------------------------------------------------------

const SECTIONS = [["", "Overview", "layout-grid"], ["history", "History", "history"], ["plans", "Plans & exercises", "clipboard-list"], ["coach", "AI coach", "sparkles"],
  ["equipment", "Dumbbells & plates", "barbell"]];

/** The training area: page head with "Training | General" and, for training, its sections. */
export function trainingHead(ctx, { general = false, section = "", actions = [] } = {}) {
  return h("div", {},
    pageHead({ icon: "barbell", title: t("Training"), actions }),
    subnav([
      { href: "#/training", label: t("Training"), icon: "barbell", active: !general },
      { href: "#/training/general", label: t("General"), icon: "run", active: general },
    ]),
    general ? null : h("div", { class: "segmented section-nav" }, SECTIONS.filter(([k]) => k !== "coach" || aiOn("coach")).map(([k, label, ic]) =>
      h("button", { class: k === section && "active", onclick: () => ctx.navigate(k ? `#/training/${k}` : "#/training") }, icon(ic, { size: 16 }), t(label)))));
}

export async function trainingView(ctx, section = "") {
  const actions = [
    h("button", { onclick: () => ctx.navigate("#/training/new") }, icon("pencil", { size: 16 }), t("Enter workout")),
    h("button", { class: "primary", onclick: () => ctx.navigate("#/training/plan/new") }, icon("plus", { size: 16 }), t("New plan")),
  ];
  const head = trainingHead(ctx, { section, actions });
  if (section === "history") return h("div", {}, head, historyCard(ctx));
  if (section === "equipment") return h("div", {}, head, platesCard());
  if (section === "plans") {
    const [plans, exercises, muscles, equipment, suggestions, catalog] = await Promise.all([
      api(`${T}/plans`), api(`${T}/exercises`), api(`${T}/muscles`), api(`${T}/equipment`), api(`${T}/exercises/suggestions`), api("/api/v1/exercises")]);
    const formBox = h("div");
    const show = (el) => { formBox.replaceChildren(el); formBox.scrollIntoView({ behavior: "smooth" }); };
    const openExercise = (ex) => show(exerciseForm(ex, catalog, muscles, equipment, () => ctx.rerender(), () => formBox.replaceChildren()));
    const openLibrary = () => show(libraryCard(muscles, equipment, (changed) => (changed ? ctx.rerender() : formBox.replaceChildren())));
    return h("div", {}, head, h("div", { class: "stack" }, plansCard(ctx, plans),
      exercisesCard(ctx, exercises, muscles, equipment, suggestions, openExercise, openLibrary), formBox));
  }

  const [next, sessions, stats, plans, exercises, ins] = await Promise.all([
    api(`${T}/next`), api(`${T}/sessions`), api(`${T}/stats`, { params: { weeks: 8 } }), api(`${T}/plans`),
    api(`${T}/exercises`), api("/api/v1/insights").catch(() => null),
  ]);
  const open = sessions.filter((s) => !s.reviewed || s.unassigned);
  return h("div", {}, head,
    h("div", { class: "grid-main" },
      nextCard(ctx, next, plans, exercises),
      h("div", { class: "stack" }, ins ? readinessCard(ins) : null,
        card({ title: t("To review"), icon: "list-check" }, h("span", { class: ["badge", open.length ? "warn" : "ok"] }, t("{n} open", { n: open.length })),
          open.length ? h("div", { class: "rows" }, open.slice(0, 8).map((s) => h("a", { class: "row link", href: `#/training/session/${s.id}` },
            h("div", { class: "grow" }, h("div", { class: "habit-name" }, s.display_title),
              h("div", { class: "muted small" }, `${f.mediumDate(localDay(s.start_ts))} · ${t("{n} sets", { n: s.sets })}`)),
            h("span", { class: "badge warn" }, s.reviewed ? t("{n} without exercise", { n: s.unassigned }) : t("open")),
            icon("chevron-right", { size: 16 }))))
            : h("div", { class: "muted small" }, t("All workouts are reviewed. After the next sync the watch workout shows up here."))))),
    h("div", { class: "section-title" }, icon("body-scan", { size: 16 }), t("Muscles")),
    musclesCard(next),
    h("div", { class: "section-title" }, icon("chart-line", { size: 16 }), t("Progress")),
    h("div", { class: "grid-2" }, hardSetsCard(stats), progressCard()),
    balanceCard());
}

/** Opposing movement patterns: weekly sets and best estimated 1RM against sensible ranges. */
function balanceCard() {
  const body = h("div", { class: "muted small" }, t("Loading …"));
  const STATE = { ok: ["ok", "balanced"], low: ["warn", "{a} falls short"], high: ["warn", "{a} outweighs"], missing: ["", "no data"] };
  const row = (x, value, labels) => {
    const [cls, txt] = STATE[x.state];
    const a = t(labels[x.a] || x.a), b = t(labels[x.b] || x.b);
    return h("tr", {}, h("td", {}, `${a} : ${b}`), h("td", { class: "num r" }, value),
      h("td", { class: "num r muted small" }, x.ratio != null ? `${loc(f.num(x.ratio, 2))} (${loc(f.num(x.range[0], 2))}–${loc(f.num(x.range[1], 2))})` : "–"),
      h("td", {}, h("span", { class: ["badge", cls] }, t(txt, { a }))));
  };
  api(`${T}/balance`, { params: { weeks: 8 } }).then((b) => body.replaceChildren(
    h("div", { class: "muted small" }, t("Last 8 weeks. Patterns come from the watch category of the exercises; hints only, the plan stays as it is.")),
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, t("Sets per week")), h("th", { class: "r" }, ""), h("th", { class: "r" }, t("ratio (range)")), h("th", {}))),
      h("tbody", {}, b.volume.map((x) => row(x, `${loc(f.num(x.a_value, 1))} : ${loc(f.num(x.b_value, 1))}`, b.labels))))),
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, t("Best estimated 1RM")), h("th", { class: "r" }, ""), h("th", { class: "r" }, t("ratio (reference)")), h("th", {}))),
      h("tbody", {}, b.strength.map((x) => row(x, x.a_kg && x.b_kg ? `${loc(f.num(x.a_kg, 0))} : ${loc(f.num(x.b_kg, 0))} kg` : "–", b.labels)))))),
  (e) => body.replaceChildren(h("div", { class: "error" }, e.message)));
  return card({ title: t("Structural balance"), icon: "scale" }, null, body);
}

// --- muscle map ------------------------------------------------------------------------------------

const STATE_COLOR = { fatigued: "var(--c-fatigued)", recovering: "var(--c-recovering)", ready: { color: "var(--ok)", opacity: 0.45 } };
const STATE_LABEL = { fatigued: "fatigued", recovering: "recovering", ready: "ready" };
const MUSCLE_MODES = [["recovery", "Recovery"], ["volume", "Volume"], ["neglected", "Untrained"], ["next", "Next workout"]];
/** "ca. 31 h" / "ca. 2 days" */
const hoursText = (h_) => (h_ < 36 ? t("about {n} h", { n: Math.max(1, Math.round(h_)) }) : t("about {n} days", { n: loc(f.num(h_ / 24, 1)) }));
/** A small body map of hard sets per muscle ({muscle: n}), or a hint while there are none. */
function setsMap(values, labels, empty) {
  if (!Object.keys(values).length) return h("div", { class: "muted small" }, empty);
  return bodyMap({ fill: scaleFill(values, "var(--accent)"), size: "sm",
    title: (m) => `${t(labels[m] || m)}: ${t("{n} hard sets", { n: loc(f.num(values[m] || 0, 1)) })}` });
}
const muscleLabels = (muscles) => Object.fromEntries(muscles.map((m) => [m.key, m.label]));

const daysSince = (ts, at) => (ts ? Math.floor((Date.parse(at) - Date.parse(ts)) / 86_400_000) : null);

/** Recovery, volume, untrained muscles and what the next workout trains, on the body map; a muscle
 * picked shows its details (contributing workouts link to their review). */
function musclesCard(next) {
  let mode = "recovery", weeks = 4, picked = null, figure = bodyFigure();
  let state = null, nextDay = null;
  const volume = {};
  const body = h("div", {}, h("div", { class: "muted small" }, t("Loading …")));
  const modes = h("div", { class: "segmented", role: "group", "aria-label": t("Muscles") });
  const weeksSel = h("select", { "aria-label": t("Period") }, [[1, "this week"], [4, "4 weeks"], [12, "12 weeks"]]
    .map(([w, l]) => h("option", { value: w, selected: w === weeks }, t(l))));
  const figBtn = h("button", { type: "button", class: "ghost small" });
  const label = (m) => t(state.muscles.find((x) => x.key === m)?.label || m);
  const byKey = () => Object.fromEntries(state.muscles.map((x) => [x.key, x]));

  const fills = {
    recovery: () => { const k = byKey(); return (m) => (k[m]?.last_trained && k[m].fatigue > 0.02 ? STATE_COLOR[k[m].state] : null); },
    volume: () => scaleFill(volume[weeks] || {}),
    neglected: () => {
      const k = byKey();
      return (m) => {
        const d = daysSince(k[m]?.last_trained, state.at);
        return d == null || d > 14 ? "var(--c-fatigued)" : d > 7 ? "var(--c-recovering)" : d > 3 ? { color: "var(--ok)", opacity: 0.45 } : "var(--ok)";
      };
    },
    next: () => scaleFill(nextDay || {}, "var(--accent)"),
  };
  const legends = {
    recovery: () => [["var(--c-fatigued)", "fatigued"], ["var(--c-recovering)", "recovering"], ["color-mix(in srgb, var(--ok) 45%, transparent)", "ready (trained recently)"]],
    volume: () => [["var(--cat-train)", "more hard sets"]],
    neglected: () => [["var(--ok)", "≤ 3 days"], ["var(--c-recovering)", "8–14 days"], ["var(--c-fatigued)", "> 14 days or never"]],
    next: () => [["var(--accent)", "sets in the next workout"]],
  };
  const titleOf = (m) => {
    const x = byKey()[m];
    if (mode === "volume") return `${label(m)}: ${t("{n} hard sets", { n: loc(f.num((volume[weeks] || {})[m] || 0, 1)) })}`;
    if (mode === "next") return `${label(m)}: ${t("{n} hard sets", { n: loc(f.num((nextDay || {})[m] || 0, 1)) })}`;
    if (mode === "neglected") {
      const d = daysSince(x.last_trained, state.at);
      return `${label(m)}: ${d == null ? t("not in the last 6 weeks") : t("{n} days ago", { n: d })}`;
    }
    return `${label(m)}: ${t(STATE_LABEL[x.state])}${x.ready_in_h ? ` · ${hoursText(x.ready_in_h)}` : ""}`;
  };

  const detail = (m) => {
    const x = byKey()[m];
    const d = daysSince(x.last_trained, state.at);
    return h("div", { class: "muscle-detail" },
      h("div", { class: "toolbar" }, h("b", {}, label(m)),
        h("span", { class: ["badge", x.state === "ready" ? "ok" : "warn"] }, t(STATE_LABEL[x.state])),
        x.ready_in_h ? h("span", { class: "muted small" }, t("ready in {t}", { t: hoursText(x.ready_in_h) })) : null,
        h("span", { class: "grow" }), iconBtn("x", t("Close"), () => { picked = null; render(); })),
      h("div", { class: "muted small" }, [
        d == null ? t("not trained in the last 6 weeks") : t("last trained {when}", { when: f.dateTime(x.last_trained) }),
        t("{n} hard sets in 7 days", { n: loc(f.num(x.hard_sets_7d, 1)) }),
        mode === "volume" ? t("{n} hard sets in the period", { n: loc(f.num((volume[weeks] || {})[m] || 0, 1)) }) : null,
      ].filter(Boolean).join(" · ")),
      x.sessions.length ? h("div", { class: "rows" }, x.sessions.map((sess) => h("a", { class: "row link", href: `#/training/session/${sess.id}` },
        h("div", { class: "grow" }, h("div", {}, sess.exercises.join(", ") || t("Workout")),
          h("div", { class: "muted small" }, [f.dateTime(sess.ts), t("{n} effective sets", { n: loc(f.num(sess.sets, 1)) }),
            t("{p} % still to recover", { p: Math.round(sess.remaining * 100) }), sess.provisional ? t("not reviewed yet") : null].filter(Boolean).join(" · "))),
        icon("chevron-right", { size: 16 })))) : null);
  };

  const render = () => {
    if (!state) return;
    modes.replaceChildren(...MUSCLE_MODES.filter(([k]) => k !== "next" || next).map(([k, l]) =>
      h("button", { type: "button", class: mode === k && "active", onclick: () => setMode(k) }, t(l))));
    weeksSel.hidden = mode !== "volume";
    figBtn.textContent = figure === "female" ? t("female figure") : t("male figure");
    const garmin = state.garmin_recovery_h != null && mode === "recovery"
      ? h("div", { class: "muted small" }, t("Garmin's recovery time: {t}", { t: state.garmin_recovery_h > 0 ? hoursText(state.garmin_recovery_h) : t("recovered") })) : null;
    body.replaceChildren(...[
      h("div", { class: "toolbar" }, modes, weeksSel, h("span", { class: "grow" }), figBtn),
      mode === "next" && next ? h("div", { class: "muted small" }, t("What {day} trains (sets × muscle share)", { day: next.plan_day })) : null,
      bodyMap({ fill: fills[mode](), title: titleOf, onPick: (m) => { picked = picked === m ? null : m; render(); }, selected: picked, figure }),
      h("div", { class: "legend" }, legends[mode]().map(([c, l]) => h("span", {}, h("i", { class: "swatch", style: { background: c } }), t(l)))),
      garmin,
      picked ? detail(picked) : h("div", { class: "muted small" }, t("Tap a muscle for details."))].filter(Boolean));
  };
  const setMode = async (k) => {
    mode = k;
    try {
      if (k === "volume" && !volume[weeks]) await loadVolume();
      if (k === "next" && !nextDay) {
        const r = await api(`${T}/muscles/of`, { params: { plan_day_id: next.plan_day_id } });
        nextDay = Object.fromEntries(r.muscles.map((x) => [x.key, x.hard_sets]));
      }
    } catch (e) { body.append(h("div", { class: "error" }, e.message)); }
    render();
  };
  const loadVolume = async () => {
    const r = await api(`${T}/muscles/volume`, { params: { weeks } });
    volume[weeks] = Object.fromEntries(r.muscles.map((x) => [x.key, x.hard_sets]));
  };
  weeksSel.addEventListener("change", async () => { weeks = Number(weeksSel.value); await setMode("volume"); });
  figBtn.addEventListener("click", () => { figure = figure === "male" ? "female" : "male"; setBodyFigure(figure); render(); });
  api(`${T}/muscles/state`).then((r) => { state = r; render(); },
    (e) => body.replaceChildren(h("div", { class: "error" }, e.message)));
  return card({ title: t("Muscle map"), icon: "body-scan" }, null, body);
}

const localDay = (ts) => new Intl.DateTimeFormat("en-CA", { timeZone: f.timezone() }).format(new Date(ts));

function nextCard(ctx, next) {
  if (!next) {
    return card({ title: t("Next workout"), icon: "player-play" }, null, h("div", { class: "muted small" },
      t("No active plan. Create a plan with split days; VitaLocal then suggests the next day in the rotation. "),
      h("a", { href: "#/training/plan/new" }, t("New plan"))));
  }
  const take = async (e) => {
    await api(`${T}/plan-exercises/${e.plan_exercise_id}/target`, { method: "PUT",
      json: { target_sets: e.next.sets, target_reps: e.next.reps, target_weight_kg: e.next.weight_kg, duration_s: e.next.duration_s } });
    ctx.rerender();
  };
  // declined: the target stays; null offers the suggestion again
  const decline = async (e, suggestion) => {
    await api(`${T}/plan-exercises/${e.plan_exercise_id}/declined`, { method: "PUT", json: { suggestion } });
    ctx.rerender();
  };
  const skip = async () => {
    const note = await ask({ title: t("Skip {day}?", { day: next.plan_day }), icon: "player-skip-forward",
      label: t("Optional note (e.g. ill, travelling)"), confirm: t("Skip") });
    if (note === null) return;
    await api(`${T}/skips`, { method: "POST", json: { plan_day_id: next.plan_day_id, note: note || null } });
    ctx.rerender();
  };
  return card({ title: t("Next workout"), icon: "player-play", cls: "next-card" }, null,
    deloadNotice(ctx, next.deload),
    h("div", { class: "next-hero" },
      h("div", {}, h("div", { class: "next-day" }, next.plan_day), h("div", { class: "muted small" }, next.plan)),
      h("div", { class: "toolbar" },
        h("button", { class: "ghost", title: t("The rotation goes on with the next day; the skip shows in the history."), onclick: skip },
          icon("player-skip-forward", { size: 16 }), t("Skip")),
        h("button", { class: "primary", onclick: () => ctx.navigate("#/training/new") }, icon("pencil", { size: 16 }), t("Enter workout")))),
    next.exercises.length ? h("div", { class: "rows next-rows" }, next.exercises.map((e) => h("div", { class: "row" },
      h("div", { class: "grow" },
        h("div", { class: "habit-name" }, e.exercise),
        h("div", { class: "muted small" }, [
          e.kind !== "time" && e.reps_min ? `${e.reps_min}–${e.reps_max || e.reps_min} ${t("reps")}` : null,
          e.last ? t("last: {sets} ({rating})", { sets: loc(e.last.sets.join(", ")), rating: ratingLabel(e.last.rating) }) : t("not done yet"),
        ].filter(Boolean).join(" · ")),
        e.differs && e.next.notes?.length ? h("div", { class: "small ok" }, e.next.notes.map(noteText).join(" · ")) : null,
        e.declined ? h("div", { class: "small muted" }, t("Suggestion {s} declined", { s: loc(e.declined) }), " · ",
          h("button", { class: "link-btn", onclick: () => decline(e, null) }, t("show again"))) : null),
      h("div", { class: "toolbar", style: { justifyContent: "flex-end" } },
        h("b", { class: "num" }, loc(e.current)),
        e.deload ? [icon("arrow-right", { size: 14, cls: "muted" }),
          h("b", { class: "num", title: t("Deload; otherwise {t}", { t: loc(e.regular || "") }) }, loc(e.next.text)),
          h("span", { class: "badge" }, t("Deload"))]
          : e.differs ? [icon("arrow-right", { size: 14, cls: "muted" }),
            h("b", { class: "num ok", title: nextReason(e.next) }, loc(e.next.text)),
            h("button", { class: "link-btn", onclick: () => take(e) }, t("take over")),
            h("button", { class: "link-btn muted", title: t("Keep the current target; this suggestion is not offered again"),
              onclick: () => decline(e, e.next.text) }, t("decline"))] : null))))
      : h("div", { class: "muted small" }, t("This day has no exercises yet.")));
}

const DELOAD_REASON = {
  scheduled: (r) => t("{n} weeks since the last deload (planned every few weeks)", { n: r.weeks }),
  stalls: (r) => t("rated too hard twice in a row: {list}", { list: r.exercises.join(", ") }),
  hard_ratings: (r) => t("{n} of {m} ratings in two weeks were too hard", { n: r.hard, m: r.of }),
};

/** In a deload week: until when and how to end it early; when one is suggested: why, and a start. */
function deloadNotice(ctx, d) {
  if (!d || (!d.active && !d.suggested)) return null;
  const call = async (method) => {
    try { await api(`${T}/deload`, { method, json: method === "POST" ? {} : undefined }); ctx.rerender(); } catch (e) { alert(e.message); }
  };
  if (d.active) {
    return h("div", { class: "notice", style: { marginBottom: "12px" } },
      h("b", {}, t("Deload week until {d}", { d: f.mediumDate(d.until) })), " ",
      t("One set less at the same weight, reps at the bottom of the range; these workouts do not count for the progression."), " ",
      h("button", { type: "button", class: "link-btn", onclick: () => call("DELETE") }, t("End deload")));
  }
  return h("div", { class: "notice warn", style: { marginBottom: "12px" } },
    h("b", {}, t("A deload week is suggested")), ": ", d.reasons.map((r) => DELOAD_REASON[r.key]?.(r) || r.key).join("; "), ". ",
    h("button", { type: "button", onclick: () => call("POST") }, t("Start a deload week")));
}

function nextReason(n) {
  const why = { easy: t("last time too easy"), good: t("last time good"), hard: t("last time too hard"), plan: t("from the plan") }[n.reason] || "";
  return [why, n.unrated ? t("not rated, counted as good") : null, ...(n.notes || []).map(noteText)].filter(Boolean).join(" · ");
}

/** A progression note from the server ("volume variant: 4×7 instead of 3×9") in the UI language. */
function noteText(x) {
  let m;
  if ((m = x.match(/^(\d+) of (\d+) sets done$/))) return t("{a} of {b} sets done", { a: m[1], b: m[2] });
  if ((m = x.match(/^volume variant: (\S+) instead of (\S+)$/))) return t("volume variant: {a} instead of {b}", { a: m[1], b: m[2] });
  if ((m = x.match(/^back to (\S+)$/))) return t("back to {a}", { a: m[1] });
  if ((m = x.match(/^(\d+) weeks without it: start at the last level$/))) return t("{n} weeks without it: start at the last level", { n: m[1] });
  return t(x);
}

const PAGE = 20;

/** All workouts, one page at a time, optionally within a date range. */
function historyCard(ctx) {
  let offset = 0;
  const from = h("input", { type: "date", "aria-label": t("from"), onchange: () => { offset = 0; load(); } });
  const to = h("input", { type: "date", "aria-label": t("until"), onchange: () => { offset = 0; load(); } });
  const reset = h("button", { class: "link-btn", onclick: () => { from.value = ""; to.value = ""; offset = 0; load(); } }, t("all"));
  const body = h("div", { class: "muted small" }, t("Loading …"));
  const pager = h("div", { class: "toolbar", style: { justifyContent: "flex-end", marginTop: "8px" } });
  const load = async () => {
    reset.hidden = !from.value && !to.value;
    let page;
    try {
      page = await api(`${T}/history`, { params: { from: from.value, to: to.value, offset, limit: PAGE } });
    } catch (e) {
      body.replaceChildren(h("div", { class: "error" }, e.message));
      return;
    }
    body.replaceChildren(page.items.length ? h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, t("Date")), h("th", {}, t("Workout")), h("th", {}, t("Plan day")), h("th", { class: "r" }, t("Sets")),
        h("th", { class: "r" }, t("Duration")), h("th", {}))),
      h("tbody", {}, page.items.map((s) => s.kind === "skip" ? skipRow(s, () => ctx.rerender()) : h("tr", { class: "link", onclick: () => { location.hash = `#/training/session/${s.id}`; } },
        h("td", {}, f.mediumDate(localDay(s.start_ts))),
        h("td", {}, s.display_title, s.manual ? h("span", { class: "muted small" }, ` · ${t("manual")}`) : null),
        h("td", { class: "muted" }, s.plan_day || "–"),
        h("td", { class: "r num" }, s.sets),
        h("td", { class: "r num" }, f.seconds(s.elapsed_s)),
        h("td", {}, h("span", { class: ["badge", s.reviewed && !s.unassigned ? "ok" : "warn"] },
          !s.reviewed ? t("open") : s.unassigned ? t("{n} without exercise", { n: s.unassigned }) : t("reviewed"))))))))
      : h("div", { class: "muted small" }, from.value || to.value ? t("No workouts in this period.") : t("No strength workouts yet.")));
    pager.replaceChildren(...(page.total > PAGE ? [
      h("button", { class: "icon-only", "aria-label": t("Newer"), disabled: offset === 0, onclick: () => { offset = Math.max(0, offset - PAGE); load(); } }, icon("chevron-left", { size: 16 })),
      h("span", { class: "muted small" }, t("{a}–{b} of {n}", { a: offset + 1, b: Math.min(offset + PAGE, page.total), n: page.total })),
      h("button", { class: "icon-only", "aria-label": t("Older"), disabled: offset + PAGE >= page.total, onclick: () => { offset += PAGE; load(); } }, icon("chevron-right", { size: 16 })),
    ] : page.total ? [h("span", { class: "muted small" }, t("{n} workouts", { n: page.total }))] : []));
  };
  load();
  return card({ title: t("Workouts"), icon: "history" },
    h("span", { class: "toolbar" }, h("span", { class: "muted small" }, t("from")), from, h("span", { class: "muted small" }, t("until")), to, reset),
    body, pager);
}

/** A skipped plan day in the history, with undo. */
function skipRow(s, reload) {
  const undo = async (ev) => {
    ev.target.disabled = true;
    await api(`${T}/skips/${s.id}`, { method: "DELETE" });
    reload();
  };
  return h("tr", { class: "muted" },
    h("td", {}, f.mediumDate(localDay(s.start_ts))),
    h("td", {}, t("skipped"), s.note ? h("span", { class: "small" }, ` · ${s.note}`) : null),
    h("td", {}, s.plan_day || "–"),
    h("td", {}), h("td", {}),
    h("td", {}, h("button", { class: "link-btn", title: t("Undo the skip: the rotation goes back to this day"), onclick: undo }, t("undo"))));
}

function hardSetsCard(stats) {
  const weeks = [...stats.weeks].reverse(); // newest first: the current week stays visible on small screens
  const used = stats.muscles.filter((m) => weeks.some((w) => w.hard_sets[m.key]));
  if (!used.length) return card(t("Hard sets per muscle group"), null, h("div", { class: "muted small" }, t("Appears once reviewed sets are assigned to exercises.")));
  const max = Math.max(...weeks.flatMap((w) => Object.values(w.hard_sets)), 1);
  const cells = (value, scale) => weeks.map((w) => {
    const v = value(w);
    return h("td", { class: "r num", style: v ? { background: `color-mix(in srgb, var(--cat-train) ${Math.round((v / scale) * 35)}%, transparent)` } : {} }, v ? f.num(v, v % 1 ? 1 : 0) : "");
  });
  // a group with more than one trained part gets a row of its sum above its parts
  const rows = byGroup(used).flatMap((g) => {
    if (g.items.length < 2) return g.items.map((m) => h("tr", {}, h("td", {}, t(m.label)), cells((w) => w.hard_sets[m.key], max)));
    const sum = (w) => g.items.reduce((a, m) => a + (w.hard_sets[m.key] || 0), 0);
    const gmax = Math.max(...weeks.map(sum), 1);
    return [h("tr", { class: "group-row" }, h("td", {}, h("b", {}, t(g.label))), cells(sum, gmax)),
      ...g.items.map((m) => h("tr", {}, h("td", { class: "indent" }, t(m.label)), cells((w) => w.hard_sets[m.key], max)))];
  });
  return card({ title: t("Hard sets per muscle group"), icon: "flame" }, h("span", { class: "muted small" }, t("per week; secondary muscles ½")),
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, t("Muscle")), weeks.map((w) => h("th", { class: "r" }, f.shortDate(w.week))))),
      h("tbody", {}, rows))));
}

const RANGES = [[13, "3 months"], [26, "6 months"], [52, "1 year"], [104, "2 years"], [520, "all"]];
const RANGE_KEY = "vitalocal.training.range";

/** Estimated 1RM per rep exercise and the best hold per time exercise, over a chosen period. */
function progressCard() {
  let weeks = 26;
  try { weeks = Number(localStorage.getItem(RANGE_KEY)) || weeks; } catch { /* storage blocked */ }
  const range = h("select", { "aria-label": t("Period"), onchange: () => {
    weeks = Number(range.value);
    try { localStorage.setItem(RANGE_KEY, String(weeks)); } catch { /* storage blocked */ }
    load();
  } }, RANGES.map(([w, label]) => h("option", { value: w, selected: w === weeks }, t(label))));
  const body = h("div", { class: "muted small" }, t("Loading …"));
  const row = (name, last, points, format) => h("div", { class: "habit-row" },
    h("div", { style: { minWidth: "140px" } }, h("div", { class: "habit-name" }, name), h("div", { class: "muted small" }, last)),
    points.length < 2 ? h("span", { class: "muted small" }, t("1 workout")) : responsive(h("div", { style: { flex: 1 } }), (w) => labChart({ width: w, height: 40, mini: true, points, format })));
  const holdText = (p) => loc(`${Math.round(p.duration_s)} s${p.weight_kg ? ` @ ${p.weight_kg} kg` : ""}`);
  const load = async () => {
    let st;
    try {
      st = await api(`${T}/stats`, { params: { weeks: 1, progress_weeks: weeks } });
    } catch (e) {
      body.replaceChildren(h("div", { class: "error" }, e.message));
      return;
    }
    const at = (d) => Date.parse(`${d}T00:00:00Z`);
    const rows = [
      ...st.e1rm.map((e) => row(e.exercise, `${kg(e.points.at(-1).e1rm_kg)}${e.bodyweight ? ` ${t("incl. body weight")}` : ""} · ${f.numDate(e.points.at(-1).date)}`,
        e.points.map((p) => ({ t: at(p.date), date: p.date, y: p.e1rm_kg, label: `${f.numDate(p.date)}: ${kg(p.e1rm_kg)}` })), kg)),
      ...(st.holds || []).map((e) => row(e.exercise, `${holdText(e.points.at(-1))} · ${f.numDate(e.points.at(-1).date)}`,
        e.points.map((p) => ({ t: at(p.date), date: p.date, y: p.duration_s, label: `${f.numDate(p.date)}: ${holdText(p)}` })), (v) => `${Math.round(v)} s`)),
    ];
    body.replaceChildren(rows.length ? h("div", { class: "stack-sm" }, rows)
      : h("div", { class: "muted small" }, t("Appears once reviewed sets with weight are assigned to exercises.")));
  };
  load();
  return card({ title: t("Progression"), icon: "chart-line" }, h("span", { class: "toolbar" }, h("span", { class: "muted small" }, t("1RM (Epley) · holds: best set")), range), body);
}

function plansCard(ctx, plans) {
  const activate = async (p) => { await api(`${T}/plans/${p.id}`, { method: "PUT", json: { ...planBody(p), active: true } }); ctx.rerender(); };
  return h("div", {}, card({ title: t("Plans"), icon: "clipboard-list" }, null,
    plans.length ? h("div", { class: "stack-sm" }, plans.map((p) => h("div", { class: "habit-row" },
      h("div", {}, h("div", { class: "habit-name" }, p.name, " ", p.active ? h("span", { class: "badge ok" }, t("active")) : p.archived ? h("span", { class: "badge" }, t("archived")) : null),
        h("div", { class: "muted small" }, p.days.map((d) => `${d.name} · ${t(d.exercises.length === 1 ? "1 exercise" : "{n} exercises", { n: d.exercises.length })}`).join(" → ") || t("no days yet"))),
      h("div", { class: "toolbar" },
        !p.active && !p.archived ? h("button", { onclick: () => activate(p) }, t("Activate")) : null,
        h("button", { onclick: () => ctx.navigate(`#/training/plan/${p.id}`) }, t("Edit"))))))
      : h("div", { class: "muted small" }, t("No plan yet."))));
}

const planBody = (p) => ({
  name: p.name, note: p.note, active: p.active, archived: p.archived, deload_every_weeks: p.deload_every_weeks ?? null,
  days: p.days.map((d) => ({ id: d.id, name: d.name, note: d.note, exercises: d.exercises.map((e) => ({
    id: e.id, exercise_id: e.exercise_id, sets: e.sets, reps_min: e.reps_min, reps_max: e.reps_max, duration_s: e.duration_s,
    duration_min_s: e.duration_min_s, duration_max_s: e.duration_max_s, rest_s: e.rest_s, target_reps: e.target_reps, target_weight_kg: e.target_weight_kg, note: e.note })) })),
});

function exercisesCard(ctx, exercises, muscles, equipment, suggestions, openExercise, openLibrary) {
  const label = Object.fromEntries(muscles.map((m) => [m.key, t(m.label)]));
  const gear = Object.fromEntries(equipment.map((x) => [x.key, t(x.label)]));
  const active = exercises.filter((e) => !e.archived);
  const all = suggestions.length ? h("button", { onclick: async () => { await createFromWatch(suggestions); ctx.rerender(); } },
    t("Create all from the watch ({n})", { n: suggestions.length })) : null;
  return h("div", {}, card({ title: t("Exercises"), icon: "barbell" }, h("div", { class: "toolbar" }, all,
    h("button", { onclick: openLibrary }, icon("book", { size: 16 }), t("From the library")), h("button", { onclick: () => openExercise(null) }, t("New exercise"))),
    active.length ? h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, t("Exercise")), h("th", {}, t("Watch")), h("th", {}, t("Muscles")), h("th", {}, t("Equipment")), h("th", { class: "r" }, t("Step")), h("th", {}))),
      h("tbody", {}, active.map((e) => h("tr", {},
        h("td", {}, e.name, e.kind === "time" ? h("span", { class: "muted small" }, ` · ${t("time")}`) : null),
        h("td", { class: "muted small" }, e.garmin_category ? [f.humanize(e.garmin_category), e.garmin_name ? ` · ${f.humanize(e.garmin_name)}` : ""] : "–"),
        h("td", { class: "small" }, [...e.muscles.map((m) => label[m]), ...e.muscles_secondary.map((m) => `${label[m]} ½`)].join(", ") || "–"),
        h("td", { class: "small muted" }, (e.equipment || []).map((x) => gear[x] || x).join(", ") || "–"),
        h("td", { class: "r num" }, e.dumbbells ? t("plates") : kg(e.increment_kg)),
        h("td", {}, h("button", { class: "link-btn", onclick: () => openExercise(e) }, t("Edit"))))))))
      : h("div", { class: "muted small" }, t("No exercises yet. Take them from the library, from what the watch recorded (below) or create them by hand.")),
    suggestions.length ? h("div", { style: { marginTop: "12px" } },
      h("div", { class: "muted small" }, t("Recorded by the watch, not yet an exercise:")),
      h("div", { class: "chips", style: { marginTop: "6px" } }, suggestions.map((s) => h("button", { class: "chip", onclick: () => openExercise({ suggestion: true, name: f.humanize(s.garmin_name || s.garmin_category), garmin_category: s.garmin_category, garmin_name: s.garmin_name }) },
        `${f.humanize(s.garmin_name || s.garmin_category)} · ${t("{n} sets", { n: s.sets })}`)))) : null));
}

/** Handle and plates of the adjustable dumbbells; exercises loaded with them step to these weights. */
function platesCard() {
  const body = h("div", { class: "muted small" }, t("Loading …"));
  const list = (p) => p.weights.filter((w) => w > 0).map((w) => loc(f.num(w, w % 1 ? 1 : 0))).join(" · ");
  const load = async () => {
    let p;
    try {
      p = await api(`${T}/plates`);
    } catch (e) {
      body.replaceChildren(h("div", { class: "error" }, e.message));
      return;
    }
    const handle = h("input", { type: "number", min: 0, max: 50, step: "any", value: p.handle_kg, class: "num-input" });
    const rows = h("div", { class: "stack-sm" });
    const plateRow = (pl = { kg: "", count: "" }) => {
      const w = h("input", { type: "number", min: 0.25, max: 50, step: "any", value: pl.kg, class: "num-input", "aria-label": t("kg") });
      const c = h("input", { type: "number", min: 0, max: 40, step: 1, value: pl.count, class: "num-input", "aria-label": t("pieces") });
      const row = h("div", { class: "toolbar" }, w, h("span", { class: "muted small" }, t("kg ×")), c, h("span", { class: "muted small" }, t("pieces")),
        iconBtn("x", t("Remove"), () => row.remove()));
      row.read = () => ({ kg: num(w.value), count: num(c.value) });
      return row;
    };
    rows.replaceChildren(...p.plates.map(plateRow));
    const weights = h("div", { class: "small" }, list(p));
    const err = h("div", { class: "error", hidden: true });
    const submit = async (e) => {
      e.preventDefault();
      const plates = [...rows.children].map((r) => r.read()).filter((x) => x.kg && x.count != null);
      try {
        p = await api(`${T}/plates`, { method: "PUT", json: { handle_kg: num(handle.value) ?? 0, plates } });
        weights.textContent = list(p);
        err.hidden = true;
      } catch (ex) { err.hidden = false; err.textContent = ex.message; }
    };
    body.replaceChildren(h("form", { class: "form", onsubmit: submit },
      h("div", { class: "muted small" }, t("Exercises set to plates step to the weights these allow, both dumbbells loaded alike (each side takes a quarter of every size).")),
      h("label", { class: "form-row" }, h("span", {}, t("Handle with collars (kg)")), handle),
      h("div", { class: "form-row" }, h("span", {}, t("Plates for both dumbbells")), rows,
        h("div", {}, h("button", { type: "button", class: "link-btn", onclick: () => rows.append(plateRow()) }, t("Add plate")))),
      h("div", { class: "form-row" }, h("span", {}, t("Weights of one dumbbell (kg)")), weights),
      err,
      h("div", { class: "toolbar" }, h("button", { class: "primary", type: "submit" }, t("Save")))));
  };
  load();
  return card({ title: t("Dumbbells & plates"), icon: "barbell" }, null, body);
}

/** Toggle chips for `items` ({key, label}); the active ones carry `.active` and `data-key`. */
function chipPicker(items, sel, onToggle = null) {
  return h("div", { class: "chips" }, items.map((m) => {
    const b = h("button", { type: "button", class: ["chip", sel.includes(m.key) && "active"], "data-key": m.key, title: m.hint || null }, t(m.label));
    b.addEventListener("click", () => { b.classList.toggle("active"); onToggle?.(); });
    return b;
  }));
}

/** Muscle chips under their group's name (chest: upper, mid/lower …). */
function muscleChips(muscles, sel) {
  return h("div", { class: "chip-groups" }, byGroup(muscles).map((g) =>
    h("div", { class: "chip-group" }, h("span", { class: "muted small" }, t(g.label)), chipPicker(g.items, sel))));
}

const EQUIPMENT_KEY = "vitalocal.training.equipment";
/** The equipment this browser last filtered the library by; null: show everything. */
function ownEquipment() {
  try { return JSON.parse(localStorage.getItem(EQUIPMENT_KEY)); } catch { return null; }
}

/** The built-in library, filtered by muscle, available equipment and name; "Add" makes an entry an
 * own exercise. `close(changed)`: whether an exercise was added. */
function libraryCard(muscles, equipment, close) {
  const mLabel = Object.fromEntries(muscles.map((m) => [m.key, t(m.label)]));
  const eLabel = Object.fromEntries(equipment.map((x) => [x.key, t(x.label)]));
  let changed = false;
  const saved = ownEquipment();
  const groups = byGroup(muscles);
  const muscle = h("select", {}, h("option", { value: "" }, t("all muscles")), groups.map((g) => h("optgroup", { label: t(g.label) },
    g.items.length > 1 ? h("option", { value: g.key }, t("{group}: all", { group: t(g.label) })) : null,
    g.items.map((m) => h("option", { value: m.key }, t(m.label))))));
  const wanted = () => groups.find((g) => g.key === muscle.value)?.items.map((m) => m.key) || [muscle.value];
  const q = h("input", { type: "search", class: "wide", placeholder: t("name, e.g. Curl, Kniebeuge"), "aria-label": t("Name") });
  const any = h("input", { type: "checkbox", checked: saved == null });
  const gear = chipPicker(equipment, saved || [], () => { any.checked = false; update(); });
  const have = () => (any.checked ? null : new Set([...gear.querySelectorAll(".chip.active")].map((b) => b.dataset.key)));
  const body = h("div", { class: "muted small" }, t("Loading …"));
  let entries = [];
  const row = (e) => {
    const add = e.exercise_id ? h("span", { class: "muted small" }, icon("check", { size: 14 }), " ", t("in my exercises"))
      : h("button", { type: "button", class: "link-btn", onclick: async () => {
        try {
          const ex = await api(`${T}/library/${e.key}/adopt`, { method: "POST" });
          e.exercise_id = ex.id;
          changed = true;
          libraryPromise = catalogPromise = null;
          tr.replaceWith(row(e));
        } catch (err) { alert(err.message); }
      } }, t("Add"));
    const tr = h("tr", {},
      h("td", {}, e.name, e.kind === "time" ? h("span", { class: "muted small" }, ` · ${t("time")}`) : null,
        e.note ? h("div", { class: "muted small" }, e.note) : null),
      h("td", { class: "small" }, [...e.muscles.map((m) => mLabel[m]), ...e.muscles_secondary.map((m) => `${mLabel[m]} ½`)].join(", ")),
      h("td", { class: "small muted" }, e.equipment.map((x) => eLabel[x]).join(", ") || t("body weight")),
      h("td", { class: "r" }, add));
    return tr;
  };
  const update = () => {
    const g = have();
    try { localStorage.setItem(EQUIPMENT_KEY, JSON.stringify(g && [...g])); } catch { /* ignore */ }
    const needle = q.value.trim().toLowerCase();
    const w = wanted();
    const found = entries.filter((e) => (!muscle.value || e.muscles.some((m) => w.includes(m)))
      && (!g || e.equipment.every((x) => g.has(x))) && e.name.toLowerCase().includes(needle));
    body.replaceChildren(found.length ? h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, t("Exercise")), h("th", {}, t("Muscles")), h("th", {}, t("Equipment")), h("th", {}))),
      h("tbody", {}, found.map(row))))
      : h("div", { class: "muted small" }, t("No exercise matches the filter.")));
  };
  for (const el of [muscle, q, any]) el.addEventListener("input", update);
  libraryEntries().then((lib) => { entries = lib.map((e) => ({ ...e })); update(); },
    (err) => body.replaceChildren(h("div", { class: "error" }, err.message)));
  return card({ title: t("Exercise library"), icon: "book" }, h("button", { type: "button", onclick: () => close(changed) }, t("Close")),
    h("div", { class: "form" },
      h("div", { class: "toolbar" }, h("label", { class: "form-row" }, h("span", {}, t("Muscle")), muscle), h("label", { class: "form-row" }, h("span", {}, t("Name")), q)),
      h("div", { class: "form-row" }, h("span", {}, t("My equipment")),
        h("label", { class: "form-check" }, any, t("show all, regardless of equipment")), gear)),
    body);
}

function exerciseForm(ex, catalog, muscles, equipment, saved, close) {
  const edit = ex && !ex.suggestion ? ex : null;
  const name = h("input", { type: "text", maxlength: 80, required: true, value: ex?.name || "", class: "wide", placeholder: t("e.g. dumbbell curl") });
  name.addEventListener("input", () => { delete name.dataset.auto; });
  const cat = h("select", {}, h("option", { value: "" }, t("– none –")), catalog.map((c) => h("option", { value: c.category, selected: c.category === ex?.garmin_category }, f.humanize(c.category))));
  const gname = h("select");
  const fillNames = () => {
    const c = catalog.find((x) => x.category === cat.value);
    gname.replaceChildren(h("option", { value: "" }, t("any in this category")), ...(c ? c.names : []).map((n) => h("option", { value: n, selected: n === ex?.garmin_name }, f.humanize(n))));
    gname.disabled = !c;
  };
  cat.addEventListener("change", fillNames);
  fillNames();
  const kind = h("select", {}, [["reps", "repetitions"], ["time", "time (s)"]].map(([k, l]) => h("option", { value: k, selected: k === (ex?.kind || "reps") }, t(l))));
  const inc = h("input", { type: "number", min: 0.25, max: 50, step: "any", value: ex?.increment_kg ?? 2.5, class: "num-input" });
  const dumb = h("select", {}, [[0, "fixed step"], [1, "one dumbbell (plates)"], [2, "sum of two dumbbells (plates)"]]
    .map(([k, l]) => h("option", { value: k, selected: k === (ex?.dumbbells ?? 0) }, t(l))));
  const stepFor = () => { inc.disabled = dumb.value !== "0"; };
  dumb.addEventListener("change", stepFor);
  stepFor();
  const bw = h("input", { type: "checkbox", checked: !!ex?.bodyweight });
  const note = h("input", { type: "text", maxlength: 500, value: ex?.note || "", class: "wide", placeholder: t("optional") });
  const pick = (sel) => muscleChips(muscles, sel);
  const chosen = (el) => [...el.querySelectorAll(".chip.active")].map((b) => b.dataset.key);
  const prim = pick(edit ? edit.muscles : []), sec = pick(edit ? edit.muscles_secondary : []);
  const gear = chipPicker(equipment, ex?.equipment || []);
  const preview = h("div");
  const drawPreview = () => {
    const vals = {};
    for (const m of chosen(sec)) vals[m] = 0.5;
    for (const m of chosen(prim)) vals[m] = 1;
    preview.replaceChildren(setsMap(vals, muscleLabels(muscles), t("Pick muscles to see them on the body.")));
  };
  for (const el of [prim, sec]) el.addEventListener("click", () => setTimeout(drawPreview));
  setTimeout(drawPreview);
  const setChips = (el, keys) => { for (const b of el.querySelectorAll(".chip")) b.classList.toggle("active", keys.includes(b.dataset.key)); };
  let libraryKey = null;
  const err = h("div", { class: "error", hidden: true });
  const submit = async (e) => {
    e.preventDefault();
    const p = chosen(prim), s = chosen(sec);
    const body = { id: edit?.id || uuid4(), name: name.value.trim(), garmin_category: cat.value || null, garmin_name: cat.value && gname.value || null,
      kind: kind.value, increment_kg: Number(inc.value), dumbbells: Number(dumb.value), bodyweight: bw.checked, note: note.value.trim() || null, archived: false,
      equipment: chosen(gear), ...(edit ? {} : { library_key: libraryKey }),
      // nothing picked on a new exercise: the server fills in the category's muscles
      muscles: edit || p.length || s.length ? p : null, muscles_secondary: edit || p.length || s.length ? s : null };
    try {
      await api(edit ? `${T}/exercises/${edit.id}` : `${T}/exercises`, { method: edit ? "PUT" : "POST", json: body });
      saved();
    } catch (ex2) { err.hidden = false; err.textContent = ex2.message; }
  };
  return card(edit ? t("Edit exercise") : t("New exercise"), null, h("form", { class: "form", onsubmit: submit },
    edit ? null : h("div", { class: "form-row" }, h("span", {}, t("From the library or the Garmin catalog (fills in the form)")),
      catalogSearch((e) => {
        cat.value = e.category || "";
        fillNames();
        gname.value = e.name || "";
        libraryKey = e.library?.key ?? null;
        if (e.library) {
          const l = e.library;
          name.value = l.name;
          name.dataset.auto = "1";
          kind.value = l.kind;
          inc.value = l.increment_kg;
          bw.checked = l.bodyweight;
          note.value = l.note || "";
          setChips(prim, l.muscles);
          setChips(sec, l.muscles_secondary);
          setChips(gear, l.equipment);
          drawPreview();
        } else if (!name.value.trim() || name.dataset.auto) { name.value = f.humanize(e.name || e.category); name.dataset.auto = "1"; }
      })),
    h("label", { class: "form-row" }, h("span", {}, t("Name")), name),
    h("div", { class: "toolbar" },
      h("label", { class: "form-row" }, h("span", {}, t("Watch category")), cat),
      h("label", { class: "form-row" }, h("span", {}, t("Watch exercise")), gname),
      h("label", { class: "form-row" }, h("span", {}, t("Measured in")), kind),
      h("label", { class: "form-row" }, h("span", {}, t("Weights")), dumb),
      h("label", { class: "form-row" }, h("span", {}, t("Weight step (kg)")), inc),
      h("label", { class: "form-check" }, bw, t("bodyweight (weight = extra load)"))),
    h("div", { class: "form-row" }, h("span", {}, edit ? t("Primary muscles") : t("Primary muscles (empty: from the watch category)")), prim),
    h("div", { class: "form-row" }, h("span", {}, t("Secondary muscles (count ½)")), sec),
    h("div", { class: "form-row" }, h("span", {}, t("Equipment (none: body weight only)")), gear),
    h("div", { class: "form-row" }, h("span", {}, t("On the body (primary full, secondary lighter)")), preview),
    h("label", { class: "form-row" }, h("span", {}, t("Note")), note),
    err,
    h("div", { class: "toolbar" }, h("button", { class: "primary", type: "submit" }, t("Save")), h("button", { type: "button", onclick: close }, t("Cancel")),
      edit ? h("button", { type: "button", onclick: async () => { await api(`${T}/exercises/${edit.id}`, { method: "PUT", json: { ...edit, archived: true } }); saved(); } }, t("Archive")) : null)));
}

// --- plan editor ---------------------------------------------------------------------------------

export async function planView(ctx, id = null) {
  const from = (ctx.params.get("from") || "").split(",").filter(Boolean);
  const [plan, exercises, sessions, templates, muscles] = await Promise.all([
    id ? api(`${T}/plans`).then((ps) => ps.find((p) => p.id === id)) : null,
    api(`${T}/exercises`, { params: { include_archived: false } }),
    api(`${T}/sessions`),
    Promise.all(from.map((aid) => api(`${T}/sessions/${aid}/as-plan-day`))),
    api(`${T}/muscles`),
  ]);
  if (id && !plan) throw new Error(t("plan not found"));
  const labels = muscleLabels(muscles);
  const name = h("input", { type: "text", maxlength: 80, required: true, value: plan?.name || "", class: "wide", placeholder: t("e.g. Push/Pull/Legs") });
  const note = h("input", { type: "text", maxlength: 1000, value: plan?.note || "", class: "wide", placeholder: t("optional") });
  const active = h("input", { type: "checkbox", checked: plan ? plan.active : true });
  const deloadEvery = h("select", {}, [["", "only when needed"], ...[4, 5, 6, 8, 10, 12].map((w) => [w, t("every {n} weeks", { n: w })])]
    .map(([v, l]) => h("option", { value: v, selected: String(plan?.deload_every_weeks ?? "") === String(v) }, t(l))));
  const days = h("div", { class: "stack" });
  const exOptions = (sel) => exercises.map((e) => h("option", { value: e.id, selected: e.id === sel }, e.name));
  const exRow = (pe = {}) => {
    const numIn = (v, attrs = {}) => h("input", { type: "number", class: "num-input", value: v ?? "", ...attrs });
    const ex = withCatalog(h("select", { required: true }, h("option", { value: "" }, t("choose …")), exOptions(pe.exercise_id)),
      exercises, (id) => id);
    const sets = numIn(pe.sets ?? 3, { min: 1, max: 20, required: true, "aria-label": t("Sets") });
    const rmin = numIn(pe.reps_min, { min: 1, max: 100, "aria-label": t("reps from") });
    const rmax = numIn(pe.reps_max, { min: 1, max: 100, "aria-label": t("reps to") });
    // time exercises: a range in seconds like the reps; the current target (duration_s) stays as it is
    const dmin = numIn(pe.duration_min_s ?? (pe.duration_max_s ? null : pe.duration_s), { min: 1, max: 3600, "aria-label": t("seconds from") });
    const dmax = numIn(pe.duration_max_s, { min: 1, max: 3600, "aria-label": t("seconds to") });
    const dunit = h("span", { class: "muted small" }, "s");
    const w = numIn(pe.target_weight_kg, { min: 0, max: 1000, step: "any", "aria-label": t("weight") });
    const rest = numIn(pe.rest_s, { min: 0, max: 1800, "aria-label": t("rest (s)") });
    const isTime = () => exercises.find((e) => e.id === ex.value)?.kind === "time";
    const sync = () => { rmin.hidden = rmax.hidden = isTime(); dmin.hidden = dmax.hidden = dunit.hidden = !isTime(); };
    ex.addEventListener("change", sync);
    sync();
    const row = h("div", { class: "toolbar" }, ex, sets, h("span", { class: "muted small" }, "×"), rmin, dmin, h("span", { class: "muted small" }, "–"), rmax, dmax, dunit,
      h("span", { class: "muted small" }, "@"), w, h("span", { class: "muted small" }, "kg ·"), rest, h("span", { class: "muted small" }, t("s rest")),
      h("button", { type: "button", class: "link-btn", onclick: () => row.remove() }, "×"));
    row.value = () => ({ id: pe.id, exercise_id: ex.value, sets: num(sets.value), reps_min: isTime() ? null : num(rmin.value),
      reps_max: isTime() ? null : num(rmax.value),
      duration_s: isTime() ? (pe.duration_max_s || pe.duration_min_s ? pe.duration_s : null) ?? num(dmin.value) : null,
      duration_min_s: isTime() ? num(dmin.value) : null, duration_max_s: isTime() ? num(dmax.value) : null, rest_s: num(rest.value),
      target_reps: pe.target_reps ?? null, target_weight_kg: num(w.value), note: pe.note ?? null });
    return row;
  };
  const dayBox = (d = { exercises: [] }) => {
    const mapBox = h("div", { class: "muscle-detail" });
    const dname = h("input", { type: "text", maxlength: 60, required: true, value: d.name || "", placeholder: t("e.g. Push") });
    const rows = h("div", { class: "lab-rows" }, d.exercises.map(exRow));
    const box = h("div", { class: "card" },
      h("div", { class: "toolbar" }, h("b", {}, t("Day")), dname,
        h("button", { type: "button", class: "link-btn", "aria-label": t("Move up"), onclick: () => box.previousElementSibling?.before(box) }, icon("arrow-up", { size: 14 })),
        h("button", { type: "button", class: "link-btn", "aria-label": t("Move down"), onclick: () => box.nextElementSibling?.after(box) }, icon("arrow-down", { size: 14 })),
        h("span", { style: { flex: 1 } }),
        h("button", { type: "button", class: "link-btn", onclick: () => box.remove() }, t("remove day"))),
      h("div", { class: "muted small" }, t("Exercise · sets × rep range (or seconds) @ weight · rest")),
      rows,
      h("div", {}, h("button", { type: "button", onclick: () => rows.append(exRow()) }, t("+ Exercise"))),
      mapBox);
    box.value = () => ({ id: d.id, name: dname.value.trim(), note: d.note ?? null, exercises: [...rows.children].map((r) => r.value()) });
    // what the day trains: sets × muscle share (secondary ½), redrawn as the day is edited
    const drawMap = () => {
      const vals = {};
      for (const r of rows.children) {
        const v = r.value();
        const ex = exercises.find((e) => e.id === v.exercise_id);
        if (!ex || !v.sets) continue;
        for (const m of ex.muscles) vals[m] = (vals[m] || 0) + v.sets;
        for (const m of ex.muscles_secondary) vals[m] = (vals[m] || 0) + v.sets / 2;
      }
      mapBox.replaceChildren(h("div", { class: "muted small" }, t("Trains (sets × muscle share)")),
        setsMap(vals, labels, t("Add exercises to see which muscles the day trains.")));
    };
    box.addEventListener("change", drawMap);
    box.addEventListener("click", () => setTimeout(drawMap));
    drawMap();
    return box;
  };
  (plan?.days || (templates.length ? templates : [{ name: "A", exercises: [] }])).forEach((d) => days.append(dayBox(d)));
  // a finished workout as a template for another day
  const usable = sessions.filter((s) => s.sets - (s.unassigned || 0) > 0);
  const fromWorkout = usable.length ? h("select", { onchange: async (ev) => {
    const aid = ev.target.value;
    ev.target.value = "";
    if (!aid) return;
    try {
      const box = dayBox(await api(`${T}/sessions/${aid}/as-plan-day`));
      // fills the untouched empty day a new plan starts with instead of adding one next to it
      const blank = [...days.children].find((d) => { const v = d.value(); return !v.exercises.length && !v.id; });
      if (blank) blank.replaceWith(box); else days.append(box);
    } catch (ex) { err.hidden = false; err.textContent = ex.message; }
  } }, h("option", { value: "" }, t("+ Day from a workout …")), usable.map((s) => h("option", { value: s.id }, `${f.numDate(localDay(s.start_ts))} · ${s.display_title}`))) : null;
  const err = h("div", { class: "error", hidden: true });
  const submit = async (e) => {
    e.preventDefault();
    const all = [...days.children].map((d) => d.value());
    if (all.some((d) => !d.exercises.length) && !confirm(t("Days without exercises are skipped in the rotation. Save anyway?"))) return;
    const body = { id: plan?.id || uuid4(), name: name.value.trim(), note: note.value.trim() || null, active: active.checked, archived: false,
      deload_every_weeks: num(deloadEvery.value), days: all };
    try {
      await api(plan ? `${T}/plans/${plan.id}` : `${T}/plans`, { method: plan ? "PUT" : "POST", json: body });
      ctx.navigate("#/training");
    } catch (ex) { err.hidden = false; err.textContent = ex.message; }
  };
  return h("div", {},
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, plan ? t("Edit plan") : t("New plan")),
      h("div", { class: "muted small" }, t("Days run in rotation: after the last reviewed day the next one is suggested."))), h("a", { href: "#/training" }, t("Back"))),
    templates.length ? h("div", { class: "notice", style: { marginBottom: "12px" } }, t("Filled in from the workout: sets, rep range and weight as you did them. Adjust the rep range to what you aim for, then save.")) : null,
    h("form", { class: "form", onsubmit: submit },
      card(t("Plan"), null,
        h("label", { class: "form-row" }, h("span", {}, t("Name")), name),
        h("label", { class: "form-row" }, h("span", {}, t("Note")), note),
        h("label", { class: "form-check" }, active, t("active plan (its rotation is suggested)")),
        h("label", { class: "form-row" }, h("span", {}, t("Deload week")), deloadEvery,
          h("span", { class: "muted small" }, t("A lighter week (one set less, same weight, reps at the bottom of the range) that the progression leaves out. Suggested when due, when two exercises were rated too hard twice in a row or most ratings of two weeks were too hard.")))),
      days,
      h("div", { class: "toolbar" }, h("button", { type: "button", onclick: () => days.append(dayBox({ name: String.fromCharCode(65 + days.children.length), exercises: [] })) }, t("+ Day")), fromWorkout),
      err,
      h("div", { class: "toolbar" }, h("button", { class: "primary", type: "submit" }, t("Save")), h("button", { type: "button", onclick: () => ctx.navigate("#/training") }, t("Cancel")),
        h("span", { style: { flex: 1 } }),
        plan ? h("button", { type: "button", onclick: async () => { await api(`${T}/plans/${plan.id}`, { method: "PUT", json: { ...planBody(plan), active: false, archived: true } }); ctx.navigate("#/training"); } }, t("Archive")) : null,
        plan ? h("button", { type: "button", class: "danger", onclick: async () => {
          if (!confirm(t("Delete this plan? Workouts keep their sets but lose the link to its days."))) return;
          await api(`${T}/plans/${plan.id}`, { method: "DELETE" });
          ctx.navigate("#/training");
        } }, t("Delete")) : null)));
}

// --- session review and manual workouts ----------------------------------------------------------

function dayOptions(plans, selected) {
  return [h("option", { value: "" }, t("– no plan day –")), ...plans.filter((p) => !p.archived || p.days.some((d) => d.id === selected)).map((p) =>
    h("optgroup", { label: p.name }, p.days.map((d) => h("option", { value: d.id, selected: d.id === selected }, d.name))))];
}

/** The entries of a plan day as the exercise picker shows them: "Curl · 3×8 @ 11,5 kg". */
function dayEntries(plans, dayId, exercises) {
  const d = plans.flatMap((p) => p.days).find((x) => x.id === dayId);
  const byId = Object.fromEntries(exercises.map((e) => [e.id, e]));
  return (d?.exercises || []).map((pe) => {
    const reps = pe.target_reps ?? pe.reps_min;
    const w = pe.target_weight_kg ? ` @ ${f.num(pe.target_weight_kg, pe.target_weight_kg % 1 ? 1 : 0)} kg` : "";
    const n = pe.target_sets ?? pe.sets;
    const text = byId[pe.exercise_id]?.kind === "time" ? `${n}×${pe.duration_s ?? 30} s${w}` : `${n}×${reps ?? "?"}${w}`;
    return { ...pe, label: `${byId[pe.exercise_id]?.name || "?"} · ${text}`, text };
  });
}

const slotKey = (exId, peId) => `${exId}|${peId || ""}`;

/** Rating proposed from the work sets against the planned target (as training.suggest_rating on the
 * server): a missed set, rep, weight or time → too hard; every set ≥ 3 reps (15 s) over → too easy.
 * Reps count the planned number of best sets at the weight. */
export function suggestRating(target, sets, kind) {
  const n = target?.sets || 0;
  if (!target) return null;
  if (kind === "time") {
    const done = sets.map((x) => x.duration_s || 0).filter((d) => d > 0);
    const goal = target.duration_s;
    if (!done.length || !goal) return null;
    if (done.length < n) return { rating: "hard", why: "fewer sets" };
    if (Math.min(...done) < goal) return { rating: "hard", why: "target missed" };
    return Math.min(...done) >= goal + 15 ? { rating: "easy", why: "target exceeded" } : { rating: "good", why: "target met" };
  }
  const work = sets.filter((x) => (x.repetitions || 0) > 0);
  if (!work.length || !target.reps) return null;
  // as the progression counts it: the planned number of sets at the weight; an extra set is a bonus
  const atWeight = work.filter((x) => !target.weight_kg || (x.weight_kg || 0) >= target.weight_kg - 1e-9);
  const best = atWeight.map((x) => x.repetitions).sort((a, b) => b - a).slice(0, n || undefined);
  if (atWeight.length < n && atWeight.length < work.length) return { rating: "hard", why: "lighter weight" };
  if (best.length < n) return { rating: "hard", why: "fewer sets" };
  const least = Math.min(...best);
  if (least < target.reps) {
    // as the progression counts it: one set more than planned, every set at the reps most of them reached
    const done = atWeight.map((x) => x.repetitions);
    if (n && done.length > n && Math.min(...done) === aimed(done) && aimed(done) < target.reps) return { rating: "good", why: "volume variant" };
    return { rating: "hard", why: "target missed" };
  }
  return least >= target.reps + 3 ? { rating: "easy", why: "target exceeded" } : { rating: "good", why: "target met" };
}
const WHY = { "fewer sets": "fewer sets than planned", "target missed": "target not reached", "lighter weight": "lighter than planned",
  "target met": "target reached", "target exceeded": "target clearly exceeded", "volume variant": "volume variant done" };
/** The reps a session went for: what most of its sets reached (a tie: the higher), as on the server. */
function aimed(reps) {
  const count = new Map();
  for (const r of reps) count.set(r, (count.get(r) || 0) + 1);
  return [...count].sort((a, b) => b[1] - a[1] || b[0] - a[0])[0][0];
}

/** Editable set rows with a live per-exercise summary and the ratings. The exercise picker offers
 * the plan day's entries first (value "pe:<id>"), then every exercise ("ex:<id>"). */
function setEditor(exercises, sets, { ratings = [], entries = [], planned = [] } = {}) {
  const byId = Object.fromEntries(exercises.map((e) => [e.id, e]));
  const entryById = Object.fromEntries(entries.map((e) => [e.id, e]));
  // what the progression prescribed for this session, else the plan entry's stored target
  const plannedBy = Object.fromEntries(planned.map((p) => [p.plan_exercise_id, p.next]));
  const targetOf = (pe) => plannedBy[pe.id] || { sets: pe.target_sets ?? pe.sets, reps: pe.target_reps || pe.reps_min, weight_kg: pe.target_weight_kg,
    duration_s: pe.duration_s, text: pe.text };
  // ratings the user chose; the others follow the suggestion while the sets are edited
  const chosen = new Set(ratings.map((r) => slotKey(r.exercise_id, r.plan_exercise_id)));
  const tbody = h("tbody");
  const summary = h("div", { class: "stack-sm" });
  const rating = Object.fromEntries(ratings.map((r) => [slotKey(r.exercise_id, r.plan_exercise_id), r.rating]));
  const numIn = (v, attrs = {}) => h("input", { type: "number", class: "num-input", value: v ?? "", step: "any", min: 0, ...attrs });
  const pick = (value) => {
    if (value?.startsWith("pe:")) { const e = entryById[value.slice(3)]; return { exercise_id: e?.exercise_id || null, plan_exercise_id: e ? e.id : null }; }
    if (value?.startsWith("ex:")) return { exercise_id: value.slice(3), plan_exercise_id: null };
    return { exercise_id: null, plan_exercise_id: null };
  };
  const row = (s) => {
    const current = s.plan_exercise_id && entryById[s.plan_exercise_id] ? `pe:${s.plan_exercise_id}` : s.exercise_id ? `ex:${s.exercise_id}` : "";
    const ex = h("select", {}, h("option", { value: "" }, "–"),
      entries.length ? h("optgroup", { label: t("Plan day") }, entries.map((e) => h("option", { value: `pe:${e.id}`, selected: current === `pe:${e.id}` }, e.label))) : null,
      h("optgroup", { label: t("All exercises"), "data-all": true }, exercises.filter((e) => !e.archived || e.id === s.exercise_id).map((e) =>
        h("option", { value: `ex:${e.id}`, selected: current === `ex:${e.id}` }, e.name))));
    withCatalog(ex, exercises, (id) => `ex:${id}`, (added) => { byId[added.id] = added; });
    const reps = numIn(s.repetitions, { step: 1, max: 1000, "aria-label": t("reps") });
    const w = numIn(s.weight_kg, { max: 1000, "aria-label": t("weight") });
    const dur = numIn(s.duration_s != null ? Math.round(s.duration_s) : null, { max: 36000, "aria-label": t("seconds") });
    const warm = h("input", { type: "checkbox", checked: !!s.warmup, "aria-label": t("warm-up") });
    const hide = h("input", { type: "checkbox", checked: !!s.hidden, disabled: s.source === "manual", "aria-label": t("hide") });
    const o = s.original;
    const watch = s.source === "watch"
      ? [s.garmin_category ? f.humanize(s.garmin_name || s.garmin_category) : t("not recognised"),
        h("span", { class: "lab-sub" }, `${o.repetitions ?? "–"} × ${o.weight_kg != null ? f.num(o.weight_kg, o.weight_kg % 1 ? 1 : 0) : "–"} kg · ${f.seconds(o.duration_s)}`)]
      : h("span", { class: "muted small" }, t("added"));
    const tr = h("tr", {}, h("td", { class: "small" }, watch), h("td", {}, ex), h("td", {}, reps), h("td", {}, w), h("td", {}, dur),
      h("td", {}, warm), h("td", {}, hide),
      h("td", {}, s.source === "manual" ? h("button", { type: "button", class: "link-btn", onclick: () => { tr.remove(); update(); } }, "×") : null));
    const mark = () => { tr.style.opacity = hide.checked ? 0.4 : warm.checked ? 0.7 : 1; };
    for (const el of [ex, reps, w, dur, warm, hide]) el.addEventListener("input", () => { mark(); update(); });
    tr.touched = !!s.touched;  // exercise picked by hand: a change of the plan day keeps it
    ex.addEventListener("input", () => { tr.touched = true; });
    mark();
    tr.value = () => ({ key: s.key, position: s.position, ...pick(ex.value), repetitions: num(reps.value), weight_kg: num(w.value),
      duration_s: num(dur.value), warmup: warm.checked, hidden: hide.checked });
    tr.src = s;
    return tr;
  };
  const update = () => {
    const by = new Map();
    for (const r of tbody.children) {
      const v = r.value();
      if (!v.exercise_id || v.hidden || v.warmup) continue;
      const k = slotKey(v.exercise_id, v.plan_exercise_id);
      if (!by.has(k)) by.set(k, { exercise_id: v.exercise_id, plan_exercise_id: v.plan_exercise_id, sets: [] });
      by.get(k).sets.push(v);
    }
    summary.replaceChildren(...[...by.entries()].map(([k, g]) => {
      const e = byId[g.exercise_id];
      const plan = entryById[g.plan_exercise_id];
      const text = g.sets.map((x) => (e?.kind === "time" ? `${x.duration_s ?? "?"} s` : `${x.repetitions ?? "?"}${x.weight_kg ? ` @ ${f.num(x.weight_kg, x.weight_kg % 1 ? 1 : 0)} kg` : ""}`)).join(", ");
      const target = plan ? targetOf(plan) : null;
      const sug = target ? suggestRating(target, g.sets, e?.kind) : null;
      if (!chosen.has(k)) rating[k] = sug?.rating || null;
      const seg = h("div", { class: "segmented" }, RATINGS.map(([key, l]) => {
        const b = h("button", { type: "button", class: [rating[k] === key && "active", !chosen.has(k) && rating[k] === key && "suggested"] }, t(l));
        b.addEventListener("click", () => { rating[k] = rating[k] === key ? null : key; chosen.add(k); update(); });
        return b;
      }));
      const hint = sug ? h("div", { class: "small", style: { color: "var(--muted)" } },
        chosen.has(k) && rating[k] !== sug.rating
          ? t("suggested from the sets: {r} ({why})", { r: ratingLabel(sug.rating), why: t(WHY[sug.why]) })
          : !chosen.has(k) ? t("suggestion from the sets ({why}), change it if it felt different", { why: t(WHY[sug.why]) }) : null) : null;
      return h("div", { class: "habit-row" },
        h("div", {}, h("div", { class: "habit-name" }, e?.name || "?"),
          h("div", { class: "muted small" }, text, target ? ` · ${t("planned")} ${loc(target.text)}` : ""), hint),
        seg);
    }));
    if (!by.size) summary.append(h("div", { class: "muted small" }, t("Assign sets to exercises to rate them.")));
  };
  sets.forEach((x) => tbody.append(row(x)));
  update();
  const add = () => {
    const last = [...tbody.children].at(-1);
    const prev = last?.value() || {};
    tbody.append(row({ key: null, source: "manual", position: (last?.src.position ?? 0) + 0.5, exercise_id: prev.exercise_id,
      plan_exercise_id: prev.plan_exercise_id, repetitions: prev.repetitions, weight_kg: prev.weight_kg }));
    update();
  };
  const node = h("div", { class: "stack" },
    h("div", { class: "table-wrap" }, h("table", { class: "set-table" },
      h("thead", {}, h("tr", {}, h("th", {}, t("Watch")), h("th", {}, t("Exercise")), h("th", {}, t("Reps")), h("th", {}, "kg"), h("th", {}, "s"),
        h("th", { title: t("warm-up set: not counted") }, t("Warm-up")), h("th", { title: t("hidden: an accidental or duplicate set") }, t("Hide set")), h("th", {}))),
      tbody)),
    h("div", {}, h("button", { type: "button", onclick: add }, t("+ Set"))),
    h("h3", {}, t("Per exercise")), summary);
  const ratingList = (onlyChosen = false) => Object.entries(rating).filter(([k, r]) => r && (!onlyChosen || chosen.has(k))).map(([k, r]) => {
    const [exercise_id, pe] = k.split("|");
    return { exercise_id, plan_exercise_id: pe || null, rating: r };
  });
  node.value = () => ({ sets: [...tbody.children].map((r) => r.value()), ratings: ratingList() });
  /** Current rows with their watch data, to rebuild the editor for another plan day. */
  node.snapshot = () => ({ sets: [...tbody.children].map((r) => ({ ...r.src, ...r.value(), touched: r.touched })), ratings: ratingList(true) });
  return node;
}

export async function sessionView(ctx, id) {
  const [s, exercises, plans, suggestions, muscles] = await Promise.all([api(`${T}/sessions/${id}`), api(`${T}/exercises`), api(`${T}/plans`),
    api(`${T}/exercises/suggestions`), api(`${T}/muscles`)]);
  const labels = muscleLabels(muscles);
  const prop = s.proposal;
  const sets = s.sets.map((x) => {
    const p = prop?.sets.find((y) => y.key === x.key);
    if (!p || x.exercise_id) return x;
    // a reviewed workout only gets the missing exercises, its warm-up/hidden choices stay
    return prop.only_unassigned ? { ...x, exercise_id: p.exercise_id, plan_exercise_id: p.plan_exercise_id }
      : { ...x, exercise_id: p.exercise_id, plan_exercise_id: p.plan_exercise_id, warmup: x.warmup || p.warmup, hidden: x.hidden || p.hidden };
  });
  // exercises the watch recognised in this workout that do not exist yet
  const cats = new Set(s.sets.map((x) => x.garmin_category).filter(Boolean));
  const missing = suggestions.filter((x) => cats.has(x.garmin_category));
  const noExercises = !exercises.some((e) => !e.archived);
  const createBox = missing.length || noExercises ? h("div", { class: "notice warn", style: { marginBottom: "12px" } },
    noExercises ? t("There are no exercises yet, so sets cannot be assigned. ") : t("The watch recognised exercises you have not created yet. "),
    missing.length ? h("button", { class: "primary", onclick: async (ev) => {
      ev.target.disabled = true;
      await createFromWatch(missing);
      ctx.rerender();
    } }, t("Create exercises from the watch ({n})", { n: missing.length })) : null,
    " ", h("a", { href: "#/training" }, t("Exercises"))) : null;
  const title = h("input", { type: "text", maxlength: 80, value: s.title || "", placeholder: s.plan_day || prop?.plan_day || s.activity.name || t("Workout") });
  const day = h("select", {}, dayOptions(plans, s.plan_day_id || prop?.plan_day_id));
  const note = h("input", { type: "text", maxlength: 4000, value: s.note || "", class: "wide", placeholder: t("optional") });
  // omitted unless changed: the server marks workouts in the plan's deload week itself
  const deload = h("input", { type: "checkbox", checked: !!s.deload });
  let deloadTouched = false;
  deload.addEventListener("change", () => { deloadTouched = true; });
  const box = h("div");
  let planned = s.planned || [];
  let editor = setEditor(exercises, sets, { ratings: s.ratings, entries: dayEntries(plans, day.value, exercises), planned });
  box.append(editor);
  const musclesBox = h("div");
  const drawMuscles = () => musclesBox.replaceChildren(setsMap(hardSetsOf(editor.value().sets, exercises), labels,
    t("Appears once sets are assigned to exercises.")));
  box.addEventListener("change", drawMuscles);
  box.addEventListener("click", () => setTimeout(drawMuscles));
  drawMuscles();
  day.addEventListener("change", async () => {
    const snap = editor.snapshot();
    let rows = snap.sets;
    // the sets the user has not assigned by hand follow VitaLocal's proposal for the picked day
    const d = day.value ? await api(`${T}/sessions/${id}`, { params: { plan_day_id: day.value } }) : null;
    planned = d?.planned || [];
    const by = Object.fromEntries((d?.proposal?.sets || []).map((p) => [p.key, p]));
    rows = rows.map((x) => {
      const p = by[x.key];
      return p && !x.touched && !x.warmup && !x.hidden ? { ...x, exercise_id: p.exercise_id, plan_exercise_id: p.plan_exercise_id } : x;
    });
    editor = setEditor(exercises, rows, { ratings: snap.ratings, entries: dayEntries(plans, day.value, exercises), planned });
    box.replaceChildren(editor);
    drawMuscles();
  });
  const err = h("div", { class: "error", hidden: true });
  const save = async (e) => {
    e.preventDefault();
    try {
      await api(`${T}/sessions/${id}`, { method: "PUT", json: { title: title.value.trim() || null, note: note.value.trim() || null, plan_day_id: day.value || null, reviewed: true,
        ...(deloadTouched ? { deload: deload.checked } : {}), ...editor.value() } });
      ctx.navigate("#/training");
    } catch (ex) { err.hidden = false; err.textContent = ex.message; }
  };
  const a = s.activity;
  return h("div", {},
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, s.display_title),
        h("div", { class: "muted small" }, [f.dateTime(a.start_ts), f.seconds(a.elapsed_s), a.avg_hr ? `Ø ${a.avg_hr} bpm` : null, a.manual ? t("manual") : null].filter(Boolean).join(" · "))),
      h("div", { class: "toolbar" },
        s.exercises.length ? h("button", { onclick: () => ctx.navigate(`#/training/plan/new?from=${id}`) }, t("Use as template for a new plan")) : null,
        a.manual ? null : h("a", { href: `#/training/general/${id}` }, t("Activity")), h("a", { href: "#/training" }, t("Back")))),
    createBox,
    (s.overlaps || []).map((o) => {
      const [hand, watch] = a.manual ? [id, o.id] : [o.id, id];
      return h("div", { class: "notice", style: { marginBottom: "12px" } },
        a.manual ? t("The watch recorded this workout too ({when}).", { when: f.dateTime(o.start_ts) })
          : t("You also entered this workout by hand ({when}).", { when: f.dateTime(o.start_ts) }),
        " ", t("Merge: the watch workout keeps heart rate and energy and takes the sets, ratings and plan day entered by hand; its own sets are hidden."),
        " ", h("button", { type: "button", onclick: async () => {
          try {
            await api(`${T}/sessions/${hand}/merge`, { method: "POST", json: { into: watch } });
            ctx.navigate(`#/training/session/${watch}`);
          } catch (e) { alert(e.message); }
        } }, t("Merge")));
    }),
    prop?.only_unassigned ? (noExercises ? null : h("div", { class: "notice", style: { marginBottom: "12px" } },
      t("{n} sets have no exercise yet; VitaLocal has filled in what the watch recognised. Check them and save.", { n: s.unassigned_sets })))
    : prop ? h("div", { class: "notice", style: { marginBottom: "12px" } },
      t("Proposal: plan day {day} ({why}); exercises, warm-up and hidden sets are pre-filled. Check them against your workout, rate each exercise and confirm.",
        { day: prop.plan_day || t("none"), why: t(prop.reason) })) : null,
    h("form", { class: "form", onsubmit: save },
      card(t("Workout"), s.reviewed ? h("span", { class: "badge ok" }, t("reviewed")) : h("span", { class: "badge warn" }, t("open")),
        h("div", { class: "toolbar" },
          h("label", { class: "form-row" }, h("span", {}, t("Title")), title),
          h("label", { class: "form-row" }, h("span", {}, t("Plan day")), day)),
        h("label", { class: "form-row" }, h("span", {}, t("Note")), note),
        h("label", { class: "form-check" }, deload, t("Deload workout (does not count for the progression)"))),
      card(t("Sets"), null, box),
      card({ title: t("Trained muscles"), icon: "body-scan" }, h("span", { class: "muted small" }, t("hard sets; secondary muscles ½")), musclesBox),
      s.exercises.length && aiOn("coach") ? debriefBox(id) : null,
      err,
      h("div", { class: "toolbar" },
        h("button", { class: "primary", type: "submit" }, s.reviewed ? t("Save") : t("Confirm")),
        h("button", { type: "button", onclick: () => ctx.navigate("#/training") }, t("Cancel")),
        h("span", { style: { flex: 1 } }),
        a.manual || s.reviewed ? h("button", { type: "button", class: "danger", onclick: async () => {
          if (!confirm(a.manual ? t("Delete this workout?") : t("Remove the review (title, assignments, added sets, ratings)? The watch data stays."))) return;
          await api(`${T}/sessions/${id}`, { method: "DELETE" });
          ctx.navigate("#/training");
        } }, a.manual ? t("Delete") : t("Reset review")) : null)));
}

export async function manualView(ctx) {
  const [exercises, plans, next] = await Promise.all([api(`${T}/exercises`), api(`${T}/plans`), api(`${T}/next`)]);
  const now = new Date();
  const date = h("input", { type: "date", required: true, value: localDay(now) });
  const time = h("input", { type: "time", required: true, value: f.clockOf(now) });
  const dur = h("input", { type: "number", min: 1, max: 600, required: true, value: 60, class: "num-input" });
  const title = h("input", { type: "text", maxlength: 80, placeholder: t("optional") });
  const day = h("select", {}, dayOptions(plans, next?.plan_day_id));
  const box = h("div");
  const fill = () => {
    const entries = dayEntries(plans, day.value, exercises);
    // the suggestion for the next workout where there is one, else the stored target
    const nx = next && next.plan_day_id === day.value ? Object.fromEntries(next.exercises.map((e) => [e.plan_exercise_id, e.next])) : {};
    const sets = entries.flatMap((pe) => Array.from({ length: nx[pe.id]?.sets ?? pe.sets }, (_, i) => ({
      key: null, source: "manual", position: pe.position * 100 + i, exercise_id: pe.exercise_id, plan_exercise_id: pe.id,
      repetitions: nx[pe.id]?.reps ?? pe.target_reps ?? pe.reps_min, weight_kg: nx[pe.id]?.weight_kg ?? pe.target_weight_kg,
      duration_s: nx[pe.id]?.duration_s ?? pe.duration_s })));
    box.replaceChildren(setEditor(exercises, sets.length ? sets : [{ key: null, source: "manual", position: 0 }], { entries }));
  };
  day.addEventListener("change", fill);
  fill();
  const err = h("div", { class: "error", hidden: true });
  const save = async (e) => {
    e.preventDefault();
    const v = box.firstChild.value();
    try {
      const s = await api(`${T}/sessions`, { method: "POST", json: { manual_key: uuid4(), start_ts: f.zonedIso(date.value, time.value), duration_min: Number(dur.value),
        title: title.value.trim() || null, plan_day_id: day.value || null, reviewed: true, sets: v.sets.map(({ key, ...x }) => x), ratings: v.ratings } });
      ctx.navigate(`#/training/session/${s.activity_id}`);
    } catch (ex) { err.hidden = false; err.textContent = ex.message; }
  };
  return h("div", {},
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, t("Enter workout")),
      h("div", { class: "muted small" }, t("A workout without the watch. Choosing a plan day fills in its sets with the current targets."))), h("a", { href: "#/training" }, t("Back"))),
    h("form", { class: "form", onsubmit: save },
      card(t("Workout"), null, h("div", { class: "toolbar" },
        h("label", { class: "form-row" }, h("span", {}, t("Date")), date), h("label", { class: "form-row" }, h("span", {}, t("Start")), time),
        h("label", { class: "form-row" }, h("span", {}, t("Minutes")), dur), h("label", { class: "form-row" }, h("span", {}, t("Plan day")), day),
        h("label", { class: "form-row" }, h("span", {}, t("Title")), title))),
      card(t("Sets"), null, box),
      err,
      h("div", { class: "toolbar" }, h("button", { class: "primary", type: "submit" }, t("Save")), h("button", { type: "button", onclick: () => ctx.navigate("#/training") }, t("Cancel")))));
}
