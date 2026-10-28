import { api } from "../api.js";
import { calendarHeatmap, timeChart } from "../charts.js";
import { saveCheckin, todayIso } from "../checkin.js";
import { h, responsive } from "../dom.js";
import * as f from "../format.js";
import { locale, t } from "../i18n.js";
import { icon } from "../icons.js";
import { card, stat } from "../ui.js";
import { trainingHead } from "./training.js";

const title = (a) => a.title || a.sport_profile_name || a.name || f.humanize(a.sub_sport !== "generic" ? a.sub_sport : a.sport) || t("Activity");

function sourceBadges(a) {
  return h("span", { class: "toolbar", style: { gap: "4px" } },
    a.from_fit ? h("span", { class: "badge accent", title: t("Session parsed from the ACTIVITY FIT file") }, "FIT") : null,
    a.from_gadgetbridge_db ? h("span", { class: "badge", title: t("Summary from the Gadgetbridge database") }, "Gadgetbridge") : null,
    a.from_fit && !a.from_gadgetbridge_db ? h("span", { class: "badge warn", title: t("No Gadgetbridge summary for this FIT") }, t("FIT only")) : null);
}

// Which sports count as "training" for the consistency heatmap (per browser).
const TRAINING_KEY = "vitalocal.trainingSports";
const DEFAULT_TRAINING = ["training", "running", "cycling"];

function trainingSports() {
  try {
    const v = JSON.parse(localStorage.getItem(TRAINING_KEY));
    if (Array.isArray(v)) return v;
  } catch { /* unset or unavailable */ }
  return DEFAULT_TRAINING;
}

function saveTrainingSports(list) {
  try { localStorage.setItem(TRAINING_KEY, JSON.stringify(list)); } catch { /* ignore */ }
}

export async function activitiesView(ctx) {
  const sport = ctx.params.get("sport") || "";
  const all = await api("/api/v1/activities");
  const sports = [...new Set(all.map((a) => a.sport).filter(Boolean))].sort();
  const list = sport ? all.filter((a) => a.sport === sport) : all;

  const select = h("select", { onchange: (e) => ctx.navigate(e.target.value ? `#/training/general?sport=${e.target.value}` : "#/training/general") },
    h("option", { value: "" }, t("All sports ({n})", { n: all.length })),
    sports.map((s) => h("option", { value: s, selected: s === sport }, `${f.humanize(s)} (${all.filter((a) => a.sport === s).length})`)));

  return h("div", {},
    trainingHead(ctx, { general: true, actions: [select] }),
    h("div", { style: { marginBottom: "16px" } }, consistencyCard(ctx, all)),
    h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, icon("run"), t("Activities"))), list.length ? h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {},
        h("th", {}, t("Date")), h("th", {}, t("Activity")), h("th", {}, t("Type")), h("th", { class: "r" }, t("Duration")),
        h("th", { class: "r" }, t("Distance")), h("th", { class: "r" }, t("Avg HR")), h("th", { class: "r" }, "kcal"),
        h("th", { class: "r" }, t("Sets")), h("th", {}, t("Source")))),
      h("tbody", {}, list.map((a) => h("tr", { class: "link", onclick: () => ctx.navigate(`#/training/general/${a.id}`) },
        h("td", {}, f.dateTime(a.start_ts)),
        h("td", {}, h("b", {}, title(a))),
        h("td", { class: "muted" }, [a.sport, a.sub_sport].filter((x) => x && x !== "generic").map(f.humanize).join(" · ") || "–"),
        h("td", { class: "r" }, f.seconds(a.elapsed_s)),
        h("td", { class: "r" }, a.distance_m ? `${f.num(a.distance_m / 1000, 2)} km` : "–"),
        h("td", { class: "r" }, f.num(a.avg_hr)),
        h("td", { class: "r" }, f.num(a.energy_kcal)),
        h("td", { class: "r" }, a.active_sets || "–"),
        h("td", {}, sourceBadges(a)))))))
      : h("div", { class: "muted" }, t("No activities."))));
}

const localDayOf = (ts) => new Intl.DateTimeFormat("en-CA", { timeZone: f.timezone() }).format(new Date(ts));

function weekStart(iso) {
  const dow = (new Date(iso + "T00:00:00Z").getUTCDay() + 6) % 7;
  return f.addDays(iso, -dow);
}

function consistencyCard(ctx, all) {
  const today = todayIso();
  const from = f.addDays(today, -364);
  const sports = [...new Set([...DEFAULT_TRAINING, ...all.map((a) => a.sport).filter(Boolean)])];
  let selected = new Set(trainingSports());
  const body = h("div");

  const draw = () => {
    const minutes = {};
    for (const a of all) {
      if (!selected.has(a.sport)) continue;
      const d = localDayOf(a.start_ts);
      minutes[d] = (minutes[d] || 0) + (a.elapsed_s || 0) / 60;
    }
    const activeDays = Object.keys(minutes).filter((d) => d >= f.addDays(today, -29)).length;
    // weeks (Mon–Sun) with at least one training day, counted back from this or last week
    const weeks = new Set(Object.keys(minutes).map(weekStart));
    let wk = weeks.has(weekStart(today)) ? weekStart(today) : f.addDays(weekStart(today), -7);
    let weekStreak = 0;
    while (weeks.has(wk)) { weekStreak++; wk = f.addDays(wk, -7); }
    const last12 = Object.entries(minutes).filter(([d]) => d >= f.addDays(weekStart(today), -77));
    const perWeek = last12.reduce((s, [, m]) => s + m, 0) / 12;

    const chips = h("div", { class: "chips" }, sports.map((sp) => h("button", {
      class: ["chip", selected.has(sp) && "active"],
      onclick: () => {
        if (selected.has(sp)) selected.delete(sp); else selected.add(sp);
        saveTrainingSports([...selected]);
        draw();
      },
    }, f.humanize(sp))));
    const heat = h("div");
    responsive(heat, (w) => calendarHeatmap({
      width: w, from, to: today, values: minutes,
      describe: (d, v) => `${f.longDate(d)}: ${v ? t("{n} min", { n: f.num(v) }) : "–"}`,
      onSelect: (d) => ctx.navigate(`#/day/${d}`),
    }));
    body.replaceChildren(
      h("div", { class: "chip-group" }, h("span", {}, t("Counts as training:")), chips),
      h("div", { class: "stat-row" },
        stat(t("Active days"), t("{n} of the last {days} days", { n: activeDays, days: 30 })),
        stat(t("Weekly average"), t("{n} min", { n: f.num(perWeek) })),
        stat(t("Current streak"), t(weekStreak === 1 ? "{n} week" : "{n} weeks", { n: weekStreak }))),
      heat);
  };
  draw();
  return card({ title: t("Training consistency"), icon: "calendar-stats" }, h("span", { class: "muted small" }, t("minutes of training")), body);
}

export async function activityView(ctx, id) {
  const [a, exercises] = await Promise.all([api(`/api/v1/activities/${id}`), exerciseCatalog()]);
  const start = Date.parse(a.start_ts);
  const end = a.end_ts ? Date.parse(a.end_ts) : start + (a.elapsed_s || 0) * 1000;
  const pad = 5 * 60_000;
  const range = { from: new Date(start - pad).toISOString(), to: new Date(end + pad).toISOString() };
  const [hr, whr, ...extra] = await Promise.all([
    api("/api/v1/samples", { params: { metric: "heart_rate", ...range } }),
    api("/api/v1/samples", { params: { metric: "workout_heart_rate", ...range } }),
    ...EXTRA_SERIES.map(([metric]) => api("/api/v1/samples", { params: { metric, ...range } }).catch(() => ({ data: [] }))),
  ]);
  const hrPoints = hr.data.map((p) => [Date.parse(p.ts), p.value]);
  const workoutPoints = bucketMean(whr.data.map((p) => [Date.parse(p.ts), p.value]), 5000);
  const localDay = new Intl.DateTimeFormat("en-CA", { timeZone: f.timezone() }).format(new Date(start));

  const stat = (label, value) => h("div", { class: "card kpi" }, h("div", { class: "label" }, label), h("div", { class: "value" }, value));
  const stats = [
    [t("Duration"), f.seconds(a.elapsed_s)],
    a.timer_s && Math.abs(a.timer_s - a.elapsed_s) > 5 ? [t("Moving / timer"), f.seconds(a.timer_s)] : null,
    a.distance_m ? [t("Distance"), `${f.num(a.distance_m / 1000, 2)} km`] : null,
    [t("Avg / max HR"), a.avg_hr ? `${f.num(a.avg_hr)} / ${f.num(a.max_hr)}` : "–"],
    [t("Energy"), a.energy_kcal ? `${f.num(a.energy_kcal)} kcal` : "–"],
    a.fit_attrs?.total_training_effect != null ? [t("Aerobic TE"), f.num(a.fit_attrs.total_training_effect, 1)] : null,
    a.fit_attrs?.total_anaerobic_training_effect != null ? [t("Anaerobic TE"), f.num(a.fit_attrs.total_anaerobic_training_effect, 1)] : null,
    a.fit_attrs?.avg_spo2 != null ? [t("Avg. SpO₂"), `${f.num(a.fit_attrs.avg_spo2)} %`] : null,
    a.fit_attrs?.avg_stress != null ? [t("Avg. stress"), f.num(a.fit_attrs.avg_stress)] : null,
  ].filter(Boolean);

  const setBands = a.sets.filter((s) => s.set_type === "active" && s.start_ts && s.duration_s).map((s) => ({
    from: Date.parse(s.start_ts), to: Date.parse(s.start_ts) + s.duration_s * 1000,
    label: `${exerciseLabel(s)} · ${s.repetitions ?? "?"} ${t("Reps")}`,
  }));
  const reload = () => window.dispatchEvent(new HashChangeEvent("hashchange"));

  return h("div", {},
    h("div", { class: "page-head" },
      h("div", {},
        h("div", { class: "muted small" }, h("a", { href: "#/training/general", class: "back-link" }, icon("arrow-left", { size: 14 }), t("Activities")), " · ", h("a", { href: `#/day/${localDay}` }, t("Day view")),
          a.sub_sport === "strength_training" || a.title || a.active_sets
            ? [" · ", h("a", { href: `#/training/session/${a.id}` }, a.reviewed ? t("Training review") : t("Review training"))] : null),
        h("h1", {}, title(a)),
        h("div", { class: "muted" }, `${f.dateTime(a.start_ts)} · `,
          [a.sport, a.sub_sport].filter((x) => x && x !== "generic").map(f.humanize).join(" · "))),
      sourceBadges(a)),
    h("div", { class: "kpis" }, stats.map(([l, v]) => stat(l, v))),
    h("div", { class: "stack" },
      rpeCard(a, end, reload),
      card({ title: t("Heart rate"), icon: "heart" },
        h("div", { class: "legend" },
          workoutPoints.length ? h("span", {}, h("i", { class: "swatch", style: { background: "var(--c-hr)" } }), t("Workout recording (5 s)")) : null,
          h("span", {}, h("i", { class: "swatch", style: { background: "var(--c-hr-soft)" } }), t("Daily monitoring (1 min)")),
          setBands.length ? h("span", {}, h("i", { class: "swatch", style: { background: "var(--band)" } }), t("Active sets")) : null),
        responsive(h("div"), (w) => timeChart({ width: w, height: 210, xMin: start - pad, xMax: end + pad,
          bands: setBands.length ? setBands : [{ from: start, to: end, label: t("Activity") }],
          series: [
            { label: t("Monitoring"), color: "var(--c-hr-soft)", points: hrPoints, gap: 5 * 60_000, format: (v) => `${f.num(v)} bpm`, strokeWidth: 1.2 },
            { label: t("Workout"), color: "var(--c-hr)", points: workoutPoints, gap: 30_000, format: (v) => `${f.num(v)} bpm` },
          ] }))),
      zonesCard(a.fit_attrs?.hr_zones),
      ...EXTRA_SERIES.map(([, label, unit, color, dec], i) => extra[i].data.length ? card(t(label), null,
        responsive(h("div"), (w) => timeChart({ width: w, height: 140, xMin: start - pad, xMax: end + pad,
          series: [{ label: t(label), color, points: extra[i].data.map((p) => [Date.parse(p.ts), p.value]), gap: 2 * 60_000,
            format: (v) => `${f.num(v, dec)}${unit}` }] }))) : null),
      lapsCard(a.fit_attrs?.laps),
      a.sets.length ? setsCard(a, exercises, reload) : null,
      detailsCard(a)));
}

// per-workout series besides heart rate: [metric, title, unit suffix, color, decimals]
const EXTRA_SERIES = [
  ["workout_respiration_rate", "Respiration during the workout", " brpm", "var(--c-resp)", 1],
  ["workout_stress", "Stress during the workout", "", "var(--c-stress)", 0],
  ["workout_temperature", "Temperature at the watch", " °C", "var(--c-spo2)", 1],
];
const ZONE_COLORS = ["#7c8790", "#8fb3c9", "#5fa8d3", "#5dbb63", "#f0a43a", "#e0533d"];

/** Time per heart rate zone as the watch counted it (zone limits from the watch's settings). */
function zonesCard(z) {
  if (!z?.seconds?.length) return null;
  const total = z.seconds.reduce((a, b) => a + (b || 0), 0);
  if (!total) return null;
  const label = (i) => (i === 0 ? t("below zone 1") : t("Zone {n}", { n: i }));
  const range = (i) => (i === 0 ? `< ${z.bounds[0]}` : i < z.bounds.length ? `${z.bounds[i - 1]}–${z.bounds[i]}` : `> ${z.bounds[i - 1]}`);
  const rows = z.seconds.map((sec, i) => [i, sec || 0]).filter(([i]) => i <= z.bounds.length - 1);
  return card({ title: t("Heart rate zones"), icon: "heartbeat" }, h("span", { class: "muted small" },
    t("max. HR {max} · resting HR {rest}", { max: z.max_hr ?? "–", rest: z.resting_hr ?? "–" })),
    h("div", { class: "zone-bar" }, rows.map(([i, sec]) => sec ? h("span", { title: `${label(i)}: ${f.seconds(sec)}`,
      style: { width: `${(sec / total) * 100}%`, background: ZONE_COLORS[i] || ZONE_COLORS.at(-1) } }) : null)),
    h("div", { class: "score-list", style: { gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))" } }, rows.map(([i, sec]) => h("div", { class: "score-row wide" },
      h("span", {}, h("i", { class: "swatch", style: { background: ZONE_COLORS[i] || ZONE_COLORS.at(-1) } }), " ", label(i),
        h("span", { class: "muted small" }, ` · ${range(i)} bpm`)),
      h("b", { class: "num" }, `${f.seconds(sec)} · ${Math.round((sec / total) * 100)} %`)))));
}

function lapsCard(laps) {
  if (!laps?.length) return null;
  const pace = (ms) => { if (!ms) return "–"; const sPerKm = 1000 / ms; return `${Math.floor(sPerKm / 60)}:${String(Math.round(sPerKm % 60)).padStart(2, "0")} /km`; };
  return card({ title: t("Laps"), icon: "repeat" }, h("span", { class: "muted small" }, t("{n} laps", { n: laps.length })), h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, h("th", {}, "#"), h("th", { class: "r" }, t("Duration")), h("th", { class: "r" }, t("Distance")),
      h("th", { class: "r" }, t("Pace")), h("th", { class: "r" }, t("Avg HR")), h("th", { class: "r" }, t("Max HR")), h("th", { class: "r" }, "kcal"))),
    h("tbody", {}, laps.map((l, i) => h("tr", {},
      h("td", {}, i + 1), h("td", { class: "r" }, f.seconds(l.elapsed_s)),
      h("td", { class: "r" }, l.distance_m ? `${f.num(l.distance_m / 1000, 2)} km` : "–"),
      h("td", { class: "r" }, pace(l.avg_speed_ms)), h("td", { class: "r" }, f.num(l.avg_hr)), h("td", { class: "r" }, f.num(l.max_hr)),
      h("td", { class: "r" }, f.num(l.kcal))))))));
}

let catalogPromise = null;
const exerciseCatalog = () => (catalogPromise ??= api("/api/v1/exercises").catch((e) => { catalogPromise = null; throw e; }));

function bucketMean(points, ms) {
  const out = new Map();
  for (const [t, v] of points) {
    const k = Math.floor(t / ms) * ms;
    const b = out.get(k) || [0, 0];
    b[0] += v; b[1]++;
    out.set(k, b);
  }
  return [...out].map(([t, [sum, n]]) => [t, sum / n]);
}

// [key, label] pairs sorted by the label the user sees (in the current UI language).
const collator = () => new Intl.Collator(locale(), { sensitivity: "base", numeric: true });
function byLabel(keys) {
  const c = collator();
  return keys.map((k) => [k, f.humanize(k)]).sort((a, b) => c.compare(a[1], b[1]));
}

const exerciseLabel = (s) => s.exercise_name ? f.humanize(s.exercise_name) : s.exercise_category ? f.humanize(s.exercise_category) : t("Unrecognized");

function rpeCard(a, end, reload) {
  const current = a.rpe?.value;
  const set = async (value) => {
    if (a.rpe) await api(`/api/v1/checkins/${a.rpe.id}`, { method: "DELETE" });
    if (value != null) await saveCheckin({ kind: "rpe", value, activity_id: a.id, ts: new Date(end).toISOString() });
    reload();
  };
  return card({ title: t("How hard was it?"), icon: "gauge" }, h("span", { class: "muted small" }, t("RPE 1 = very easy · 10 = maximal effort")),
    h("div", { class: "scale" },
      Array.from({ length: 10 }, (_, i) => i + 1).map((v) =>
        h("button", { class: ["chip", current === v && "active"], onclick: () => set(v) }, String(v))),
      current != null ? h("button", { class: "link-btn", onclick: () => set(null) }, t("clear")) : null));
}

function setsCard(a, catalog, reload) {
  const sets = a.sets;
  const active = sets.filter((s) => s.set_type === "active");
  const byExercise = new Map();
  for (const s of active) {
    const k = exerciseLabel(s);
    const e = byExercise.get(k) || { sets: 0, reps: 0, volume: 0, hasWeight: false };
    e.sets++;
    e.reps += s.repetitions || 0;
    if (s.weight_kg) { e.volume += (s.repetitions || 0) * s.weight_kg; e.hasWeight = true; }
    byExercise.set(k, e);
  }
  const edits = active.filter((s) => s.edited).length;
  let showRest = false;
  let editing = false;
  const namesOf = Object.fromEntries(catalog.map((c) => [c.category, c.names]));
  const tbody = h("tbody");
  const status = h("span", { class: "small muted" });

  async function save(s, next) {
    const o = s.original;
    const exerciseChanged = next.category !== o.exercise_category || next.name !== o.exercise_name;
    const body = {
      exercise_category: exerciseChanged ? next.category : null,
      exercise_name: exerciseChanged ? next.name : null,
      repetitions: next.reps !== o.repetitions ? next.reps : null,
      weight_kg: next.kg !== o.weight_kg ? next.kg : null,
    };
    status.textContent = t("Saving …");
    try {
      await api(`/api/v1/activities/${a.id}/sets/${s.set_index}`, { method: "PUT", json: body });
      Object.assign(s, { exercise_category: next.category, exercise_name: next.name, repetitions: next.reps, weight_kg: next.kg,
        edited: Object.values(body).some((v) => v != null) });
      status.textContent = t("Saved");
    } catch (e) {
      status.textContent = t("Not saved: {msg}", { msg: e.message });
    }
  }

  function editRow(s, n) {
    const cat = h("select", { "aria-label": t("Exercise") },
      s.original.exercise_category == null && s.exercise_category == null ? h("option", { value: "" }, t("— unrecognized —")) : null,
      byLabel(catalog.map((c) => c.category)).map(([c, label]) =>
        h("option", { value: c, selected: c === s.exercise_category }, label)));
    const name = h("select", { "aria-label": t("Exercise") });
    const fillNames = () => {
      const names = namesOf[cat.value] || [];
      name.replaceChildren(h("option", { value: "" }, t("— any —")),
        ...byLabel(names).map(([nm, label]) => h("option", { value: nm, selected: nm === s.exercise_name }, label)));
      name.disabled = !names.length;
    };
    fillNames();
    const reps = h("input", { type: "number", min: 0, max: 1000, step: 1, value: s.repetitions ?? "", class: "num-input", "aria-label": t("Reps") });
    const kg = h("input", { type: "number", min: 0, max: 1000, step: 0.5, value: s.weight_kg ?? "", class: "num-input", "aria-label": t("Weight") });
    const commit = () => {
      save(s, { category: cat.value || null, name: cat.value ? name.value || null : null,
        reps: reps.value === "" ? s.original.repetitions : Number(reps.value),
        kg: kg.value === "" ? s.original.weight_kg : Number(kg.value) });
    };
    cat.addEventListener("change", () => { fillNames(); commit(); });
    name.addEventListener("change", commit);
    reps.addEventListener("change", commit);
    kg.addEventListener("change", commit);
    return h("tr", { class: s.edited && "edited" },
      h("td", {}, n), h("td", { class: "edit-cell" }, cat, name),
      h("td", { class: "r" }, reps), h("td", { class: "r" }, kg),
      h("td", { class: "r" }, f.seconds(s.duration_s)),
      h("td", { class: "muted small" }, s.start_ts ? f.time(s.start_ts) : ""),
      h("td", {}, s.edited ? h("button", { class: "link-btn", onclick: async () => {
        await api(`/api/v1/activities/${a.id}/sets/${s.set_index}/edit`, { method: "DELETE" });
        reload();
      } }, t("reset")) : null));
  }

  function viewRow(s, n) {
    const isActive = s.set_type === "active";
    const o = s.original || {};
    const title = s.edited ? t("Watch recorded: {ex}, {reps} reps, {kg} kg", {
      ex: o.exercise_name ? f.humanize(o.exercise_name) : o.exercise_category ? f.humanize(o.exercise_category) : t("unrecognized"),
      reps: o.repetitions ?? "?", kg: o.weight_kg ?? "?" }) : "";
    return h("tr", { class: [!isActive && "rest", s.edited && "edited"] },
      h("td", {}, isActive ? n : ""),
      h("td", { title },
        !isActive ? t("Rest")
          : s.exercise_category ? exerciseLabel(s)
          : h("span", { class: "badge warn", title: t("The watch did not recognize an exercise for this set") }, t("unrecognized")),
        s.edited ? h("span", { class: "badge accent", style: { marginLeft: "6px" } }, t("edited")) : null),
      h("td", { class: "r" }, isActive ? f.num(s.repetitions) : ""),
      h("td", { class: "r" }, isActive && s.weight_kg != null ? f.num(s.weight_kg, s.weight_kg % 1 ? 1 : 0) : ""),
      h("td", { class: "r" }, f.seconds(s.duration_s)),
      h("td", { class: "muted small" }, s.start_ts ? f.time(s.start_ts) : ""),
      h("td", { class: "muted small", title: t("Exercise candidates recorded by the watch (FIT exercise_category)") },
        (o.category_candidates || []).join(", ")));
  }

  const draw = () => {
    let n = 0;
    const rows = sets.filter((s) => (showRest && !editing) || s.set_type === "active").map((s) => {
      if (s.set_type === "active") n++;
      return editing && s.set_type === "active" ? editRow(s, n) : viewRow(s, n);
    });
    tbody.replaceChildren(...rows);
    head.replaceChildren(h("tr", {}, h("th", {}, "#"), h("th", {}, t("Exercise")), h("th", { class: "r" }, t("Reps")),
      h("th", { class: "r" }, "kg"), h("th", { class: "r" }, t("Duration")), h("th", {}, t("Start")),
      h("th", {}, editing ? "" : t("Candidates"))));
  };
  const head = h("thead");
  const restToggle = h("label", { class: "small muted" },
    h("input", { type: "checkbox", onchange: (e) => { showRest = e.target.checked; draw(); } }), ` ${t("show rest periods")}`);
  const editBtn = h("button", { onclick: () => {
    editing = !editing;
    editBtn.textContent = editing ? t("Done") : t("Correct sets");
    restToggle.hidden = editing;
    if (!editing) reload(); else draw();
  } }, t("Correct sets"));
  draw();

  return card({ title: t("Sets ({n})", { n: active.length }), icon: "barbell" }, h("div", { class: "toolbar" }, status, restToggle, editBtn),
    h("div", { class: "chips", style: { marginBottom: "12px" } }, [...byExercise].map(([k, e]) =>
      h("span", { class: "badge", style: { fontSize: "0.8rem", padding: "4px 10px" } },
        `${t("{ex}: {sets}×, {reps} reps", { ex: k, sets: e.sets, reps: e.reps })}${e.hasWeight ? t(", {vol} kg volume", { vol: f.num(e.volume) }) : ""}`))),
    h("div", { class: "table-wrap" }, h("table", {}, head, tbody)),
    h("p", { class: "muted small" },
      t("Volume = reps × weight. Corrections are stored separately; the watch's original values stay untouched and are shown on hover"),
      edits ? t(" ({n} corrected).", { n: edits }) : "."));
}

function detailsCard(a) {
  const rows = [];
  for (const [k, v] of Object.entries(a.fit_attrs || {})) {
    if (typeof v === "number") rows.push(["FIT", f.humanize(k), f.num(v, Number.isInteger(v) ? 0 : 1)]);
  }
  for (const [k, v] of Object.entries(a.gb_summary || {})) {
    if (!v || typeof v !== "object" || v.type === "tableRow" || /^(interval|set)_|_header$/.test(k)) continue;
    if (v.value == null || typeof v.value === "object") continue;
    const unit = v.unit && !["string", "unit_repetitions"].includes(v.unit) ? ` ${v.unit.replace(/^unit_/, "")}` : "";
    rows.push(["Gadgetbridge", f.humanize(k), `${typeof v.value === "number" ? f.num(v.value, Number.isInteger(v.value) ? 0 : 1) : v.value}${unit}`]);
  }
  if (!rows.length) return null;
  const body = h("div", { class: "table-wrap", hidden: true }, h("table", {},
    h("thead", {}, h("tr", {}, h("th", {}, t("Source")), h("th", {}, t("Field")), h("th", { class: "r" }, t("Value")))),
    h("tbody", {}, rows.map(([src, k, v]) => h("tr", {}, h("td", { class: "muted" }, src), h("td", {}, k), h("td", { class: "r" }, v))))));
  const btn = h("button", { onclick: () => { body.hidden = !body.hidden; btn.textContent = body.hidden ? t("Show") : t("Hide"); } }, t("Show"));
  return card({ title: t("All recorded fields"), icon: "list-check" }, btn, body);
}
