// AI coach in the training area: debriefs of single workouts by the local model. The model only
// reads facts VitaLocal prepared and answers in a fixed shape; it never changes plan or targets.

import { api } from "../api.js";
import { bodyMap, byGroup, scaleFill } from "../bodyMap.js";
import { h } from "../dom.js";
import * as f from "../format.js";
import { lang, t } from "../i18n.js";
import { icon } from "../icons.js";
import { card } from "../ui.js";
import { trainingHead } from "./training.js";

const C = "/api/v1/coach";
let statusPromise = null;
/** {enabled, model}; the coach UI only shows when a model is set up. */
export const coachStatus = () => (statusPromise ??= api(`${C}/status`).catch(() => ({ enabled: false })));

const clock = (ms) => { const s = Math.max(0, Math.round(ms / 1000)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };

/** One debrief: pending (with the time the model has been working), failed (with retry) or done. */
function debriefCard(m, { onRetry = null, showTitle = true, pendingText = "The model is reading the workout …" } = {}) {
  const head = showTitle && m.title ? h("div", { class: "muted small" }, [m.title, m.session_date && f.mediumDate(m.session_date)].filter(Boolean).join(" · ")) : null;
  if (m.status === "pending") {
    const el = h("span", {}, clock(Date.now() - Date.parse(m.created_at)));
    const timer = setInterval(() => { if (!el.isConnected) clearInterval(timer); else el.textContent = clock(Date.now() - Date.parse(m.created_at)); }, 1000);
    return h("div", { class: "coach-msg" }, head, h("div", { class: "muted" }, icon("sparkles", { size: 16 }), " ", t(pendingText), " ", el));
  }
  if (m.status === "error") {
    return h("div", { class: "coach-msg" }, head, h("div", { class: "error" }, t("The debrief failed: {e}", { e: m.error || "?" })),
      onRetry ? h("button", { type: "button", onclick: onRetry }, t("Try again")) : null);
  }
  const r = m.result;
  const list = (title, items, ic) => (items?.length ? h("div", { class: "coach-list" }, h("div", { class: "coach-list-title" }, icon(ic, { size: 15 }), t(title)),
    h("ul", {}, items.map((x) => h("li", {}, x)))) : null);
  return h("div", { class: "coach-msg" }, head,
    h("div", { class: "toolbar" }, h("span", { class: ["badge", r.score >= 7 ? "ok" : r.score >= 5 ? "warn" : "danger"] }, `${r.score}/10`),
      h("span", { class: "grow" }, r.summary)),
    list("Went well", r.highlights, "check"),
    list("Keep an eye on", r.watch, "eye"),
    list("Next time", r.next_time, "arrow-right"),
    h("div", { class: "muted small" }, [m.model, m.duration_ms ? t("{n} s", { n: Math.round(m.duration_ms / 1000) }) : null,
      t("AI reading – the plan and the targets stay with your progression rules.")].filter(Boolean).join(" · ")));
}

/** Waits for pending messages and redraws: `load()` returns the messages to show. */
function polled(box, load, render) {
  let timer = null;
  const tick = async () => {
    if (!box.isConnected && timer) return;
    let msgs;
    try { msgs = await load(); } catch (e) { box.replaceChildren(h("div", { class: "error" }, e.message)); return; }
    const el = render(msgs);
    box.replaceChildren(...(el ? [el] : []));
    clearTimeout(timer);
    if (msgs.some((m) => m.status === "pending")) timer = setTimeout(() => { if (box.isConnected) tick(); }, 3000);
  };
  tick();
  return tick;
}

/** For the workout review: the latest debrief of this workout and a button to ask for one. */
export function debriefBox(activityId) {
  const box = h("div");
  const body = h("div");
  const ask = async () => {
    try {
      await api(`${C}/debrief`, { method: "POST", json: { activity_id: Number(activityId), lang: lang() === "en" ? "en" : "de" } });
      refresh();
    } catch (e) { alert(e.message); }
  };
  const refresh = polled(body, () => api(`${C}/messages`, { params: { activity_id: activityId, limit: 1 } }), (msgs) => {
    const m = msgs[0];
    const button = h("button", { type: "button", disabled: m?.status === "pending", onclick: ask }, icon("sparkles", { size: 16 }), m ? t("Debrief again") : t("Debrief this workout"));
    return h("div", {}, m ? debriefCard(m, { onRetry: ask, showTitle: false }) : h("div", { class: "muted small" },
      t("The local model reads this workout against your last sessions and the plan and says how it went.")), h("div", { class: "toolbar" }, button));
  });
  coachStatus().then((s) => {
    if (s.enabled) box.replaceChildren(card({ title: t("AI coach"), icon: "sparkles" }, null, body));
  });
  return box;
}

// --- plan from the questionnaire ----------------------------------------------------------------------

const GOALS = [["muscle", "Build muscle", "Hypertrophy: 8–12 reps, enough weekly volume"], ["strength", "Get stronger", "Heavier compounds, fewer reps"],
  ["fitness", "Fitness", "All-round, 10–15 reps"], ["fat_loss", "Lose fat", "Keep muscle while losing weight"]];
const LEVELS = [["beginner", "Beginner", "less than a year of regular training"], ["intermediate", "Intermediate", "1–3 years"],
  ["advanced", "Advanced", "more than 3 years"]];
const EQUIPMENT_KEY = "vitalocal.training.equipment";

/** The questionnaire, one question per screen; `done(answers)` gets the answers. */
function questionnaire(prev, muscles, equipment, done) {
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem(EQUIPMENT_KEY)); } catch { /* none */ }
  const a = { goal: "muscle", experience: "intermediate", days_per_week: 3, minutes: 60, equipment: saved || [],
    limitations: "", focus: [], likes: "", dislikes: "", cardio: false, ...(prev || {}) };
  const label = Object.fromEntries(muscles.map((m) => [m.key, t(m.label)]));
  const box = h("div", { class: "wizard" });
  let step = 0;
  const choice = (key, options) => h("div", { class: "choice-list" }, options.map(([k, l, hint]) =>
    h("button", { type: "button", class: ["choice", a[key] === k && "active"], onclick: () => { a[key] = k; step++; draw(); } },
      h("b", {}, t(l)), hint ? h("span", { class: "muted small" }, t(hint)) : null)));
  const seg = (key, values, unit = "") => h("div", { class: "segmented" }, values.map((v) =>
    h("button", { type: "button", class: a[key] === v && "active", onclick: () => { a[key] = v; draw(); } }, `${v}${unit}`)));
  const toggleChips = (key, items, max = 99) => h("div", { class: "chips" }, items.map((x) =>
    h("button", { type: "button", class: ["chip", a[key].includes(x.key) && "active"], onclick: () => {
      a[key] = a[key].includes(x.key) ? a[key].filter((k) => k !== x.key) : a[key].length < max ? [...a[key], x.key] : a[key];
      draw();
    } }, t(x.label))));
  const text = (key, ph, area = false) => {
    const el = h(area ? "textarea" : "input", { class: "wide", maxlength: key === "limitations" ? 500 : 300, placeholder: t(ph), rows: area ? 3 : null });
    el.value = a[key];
    el.addEventListener("input", () => { a[key] = el.value; });
    return el;
  };
  const steps = [
    ["What is your goal?", () => choice("goal", GOALS)],
    ["How much training experience do you have?", () => choice("experience", LEVELS)],
    ["How often and how long?", () => h("div", { class: "stack-sm" },
      h("div", { class: "form-row" }, h("span", {}, t("Training days per week")), seg("days_per_week", [2, 3, 4, 5, 6])),
      h("div", { class: "form-row" }, h("span", {}, t("Minutes per session")), seg("minutes", [30, 45, 60, 75, 90])))],
    ["Which equipment do you have?", () => h("div", { class: "stack-sm" },
      h("div", { class: "check-list" }, equipment.map((x) => {
        const c = h("input", { type: "checkbox", checked: a.equipment.includes(x.key) });
        c.addEventListener("change", () => { a.equipment = c.checked ? [...a.equipment, x.key] : a.equipment.filter((k) => k !== x.key); });
        return h("label", { class: "check-row" }, c, h("span", {}, h("b", {}, t(x.label)), x.hint ? h("span", { class: "muted small" }, t(x.hint)) : null));
      })),
      h("div", { class: "muted small" }, t("Nothing selected: body weight only.")))],
    ["Which muscles should get more attention?", () => h("div", { class: "stack-sm" },
      h("div", { class: "muted small" }, t("Tap up to twelve muscles on the body (or below); none is fine too.")),
      bodyMap({ size: "md", selected: null, fill: (m) => (a.focus.includes(m) ? "var(--accent)" : null),
        title: (m) => label[m], onPick: (m) => { a.focus = a.focus.includes(m) ? a.focus.filter((x) => x !== m) : a.focus.length < 12 ? [...a.focus, m] : a.focus; draw(); } }),
      h("div", { class: "chip-groups" }, byGroup(muscles).map((g) =>
        h("div", { class: "chip-group" }, h("span", { class: "muted small" }, t(g.label)), toggleChips("focus", g.items, 12)))))],
    ["Anything else to consider?", () => h("div", { class: "stack-sm" },
      h("label", { class: "form-row" }, h("span", {}, t("Limitations or complaints")), text("limitations", "e.g. left shoulder sensitive overhead", true)),
      h("label", { class: "form-row" }, h("span", {}, t("Exercises you like")), text("likes", "e.g. pull-ups, hip thrust")),
      h("label", { class: "form-row" }, h("span", {}, t("Exercises to leave out")), text("dislikes", "e.g. lunges")),
      h("label", { class: "form-check" }, (() => { const c = h("input", { type: "checkbox", checked: a.cardio }); c.addEventListener("change", () => { a.cardio = c.checked; }); return c; })(),
        t("I also do cardio (running, cycling …)")))],
    ["All set?", () => h("dl", { class: "summary-list" },
      [["Goal", t(GOALS.find((g) => g[0] === a.goal)[1])], ["Experience", t(LEVELS.find((g) => g[0] === a.experience)[1])],
        ["Days per week", `${a.days_per_week} × ${a.minutes} min`],
        ["Equipment", a.equipment.map((k) => t(equipment.find((x) => x.key === k)?.label || k)).join(", ") || t("body weight")],
        ["Focus", a.focus.map((m) => label[m]).join(", ") || "–"], ["Limitations", a.limitations || "–"],
        ["Likes", a.likes || "–"], ["Leave out", a.dislikes || "–"], ["Cardio", a.cardio ? t("yes") : t("no")]]
        .map(([k, v]) => [h("dt", {}, t(k)), h("dd", {}, v)]))],
  ];
  const draw = () => {
    const [q, body] = steps[step];
    const last = step === steps.length - 1;
    box.replaceChildren(
      h("div", { class: "muted small" }, t("Question {n} of {m}", { n: step + 1, m: steps.length })),
      h("h3", {}, t(q)), body(),
      h("div", { class: "toolbar" },
        step ? h("button", { type: "button", onclick: () => { step--; draw(); } }, t("Back")) : null,
        h("span", { class: "grow" }),
        last ? h("button", { type: "button", class: "primary", onclick: () => done({ ...a }) }, icon("sparkles", { size: 16 }), t("Create plan"))
          : h("button", { type: "button", class: "primary", onclick: () => { step++; draw(); } }, t("Next"))));
  };
  draw();
  return box;
}

const rangeText = (x) => (x.seconds_min != null ? `${x.seconds_min}–${x.seconds_max} s` : `${x.reps_min}–${x.reps_max}`);

/** A plan proposal: days with exercises, weekly hard sets on the body, notes; take over or refine. */
/** Removes a coach message (a proposal or debrief nobody needs any more) after asking. */
async function discard(m, then) {
  if (!confirm(t(m.kind === "plan" ? "Discard this plan proposal?" : "Delete this debrief?"))) return;
  try {
    await api(`${C}/messages/${m.id}`, { method: "DELETE" });
    then();
  } catch (e) { alert(e.message); }
}

function proposalCard(m, { answers, muscles, onChange }) {
  const drop = h("button", { type: "button", class: "ghost", onclick: () => discard(m, onChange) }, icon("trash", { size: 15 }), t("Discard"));
  if (m.status !== "done") {
    return h("div", { class: "stack-sm" }, debriefCard(m, { onRetry: null, showTitle: false, pendingText: "The model is building the plan …" }),
      h("div", {}, drop));
  }
  const p = m.result;
  const label = Object.fromEntries(muscles.map((x) => [x.key, t(x.label)]));
  const active = h("input", { type: "checkbox" });
  const change = h("textarea", { class: "wide", rows: 2, maxlength: 1000, placeholder: t("e.g. more legs, no barbell squats, only 45 minutes on Fridays") });
  const err = h("div", { class: "error", hidden: true });
  const take = async () => {
    try {
      const plan = await api(`${C}/messages/${m.id}/apply`, { method: "POST", json: { active: active.checked } });
      location.hash = `#/training/plan/${plan.id}`;
    } catch (e) { err.hidden = false; err.textContent = e.message; }
  };
  const refine = async () => {
    if (!change.value.trim()) { change.focus(); return; }
    try {
      await api(`${C}/plan`, { method: "POST", json: { answers, refines: m.id, change: change.value.trim(), lang: lang() === "en" ? "en" : "de" } });
      onChange();
    } catch (e) { err.hidden = false; err.textContent = e.message; }
  };
  return h("div", { class: "coach-msg" },
    h("div", { class: "toolbar" }, h("b", {}, p.name), m.refines ? h("span", { class: "badge" }, t("refined")) : null),
    h("div", {}, p.summary),
    h("div", { class: "grid-2" },
      h("div", { class: "stack-sm" }, p.days.map((d) => h("div", { class: "plan-day" },
        h("div", { class: "toolbar" }, h("b", {}, d.name), h("span", { class: "muted small" }, `${d.focus} · ${t("about {n} min", { n: d.minutes })}`)),
        h("table", { class: "compact" }, h("tbody", {}, d.exercises.map((x) => h("tr", {},
          h("td", {}, x.name), h("td", { class: "num" }, `${x.sets} × ${rangeText(x)}`), h("td", { class: "muted small r" }, t("{n} s rest", { n: x.rest_s }))))))))),
      h("div", {}, h("div", { class: "muted small" }, t("Hard sets per week (secondary muscles ½)")),
        bodyMap({ size: "sm", fill: scaleFill(p.hard_sets_per_week, "var(--accent)"), title: (k) => `${label[k] || k}: ${loc(p.hard_sets_per_week[k] || 0)}` }),
        h("div", { class: "muted small" }, Object.entries(p.hard_sets_per_week).slice(0, 8).map(([k, v]) => `${label[k] || k} ${loc(v)}`).join(" · ")))),
    p.notes?.length ? h("ul", {}, p.notes.map((n) => h("li", {}, n))) : null,
    m.applied_plan_id
      ? h("div", { class: "notice" }, t("Taken over as a plan."), " ", h("a", { href: `#/training/plan/${m.applied_plan_id}` }, t("Open plan")))
      : h("div", { class: "stack-sm" },
        h("div", { class: "toolbar" }, h("button", { class: "primary", onclick: take }, t("Create as new plan")),
          h("label", { class: "form-check" }, active, t("make it the active plan right away"))),
        h("div", { class: "muted small" }, t("Weights and targets come from your progression from the first session on; you can edit the plan afterwards like any other.")),
        h("div", { class: "form-row" }, h("span", {}, t("Not quite right? Say what to change")), change),
        h("div", { class: "toolbar" }, h("button", { type: "button", onclick: refine }, t("Refine")), h("span", { class: "grow" }), drop)),
    err,
    h("div", { class: "muted small" }, [m.model, m.duration_ms ? t("{n} s", { n: Math.round(m.duration_ms / 1000) }) : null].filter(Boolean).join(" · ")));
}

const loc = (v) => f.num(v, v % 1 ? 1 : 0);

// --- plan review --------------------------------------------------------------------------------------

const CHANGE_LABEL = {
  swap_exercise: "Swap exercise", add_exercise: "Add exercise", remove_exercise: "Remove exercise", sets: "Sets",
  rep_range: "Rep range", duration_range: "Seconds", rest: "Rest", reorder: "Order", rename_day: "Rename day",
  remove_day: "Remove day", reorder_days: "Order of days",
};

/** A plan review: how the plan went, the proposed changes to tick and take over, undo afterwards. */
function reviewCard(m, onChange) {
  const drop = h("button", { type: "button", class: "ghost", onclick: () => discard(m, onChange) }, icon("trash", { size: 15 }), t("Discard"));
  if (m.status !== "done") {
    return h("div", { class: "stack-sm" }, debriefCard(m, { onRetry: null, showTitle: false, pendingText: "The model is reviewing the plan …" }), h("div", {}, drop));
  }
  const r = m.result;
  const applied = new Set(r.applied || []);
  const taken = applied.size > 0;
  const err = h("div", { class: "error", hidden: true });
  const boxes = r.changes.map((c, i) => h("input", { type: "checkbox", checked: taken ? applied.has(i) : true, disabled: taken }));
  const act = async (path, json) => {
    try {
      await api(`${C}/messages/${m.id}/${path}`, { method: "POST", json });
      onChange();
    } catch (e) { err.hidden = false; err.textContent = e.message; }
  };
  return h("div", { class: "coach-msg" },
    h("div", { class: "toolbar" }, r.nochange ? h("span", { class: "badge ok" }, t("no change needed"))
      : taken ? h("span", { class: "badge ok" }, icon("check", { size: 14 }), " ", applied.size === r.changes.length
        ? t("Changes taken over") : t("{n} of {m} changes taken over", { n: applied.size, m: r.changes.length }))
        : h("span", { class: "badge warn" }, t("{n} changes proposed", { n: r.changes.length }))),
    h("div", {}, r.reading),
    r.changes.length ? h("div", { class: "change-list" }, r.changes.map((c, i) => h("label", { class: ["change-row", taken && applied.has(i) && "applied"] },
      boxes[i],
      h("span", { class: "grow" },
        h("span", { class: "toolbar" }, h("b", {}, t(CHANGE_LABEL[c.type] || c.type)), h("span", { class: "muted small" }, c.where)),
        h("span", {}, c.before != null ? h("s", { class: "muted" }, c.before) : null, c.before != null && c.after != null ? " → " : null, c.after != null ? h("b", {}, c.after) : null),
        h("span", { class: "muted small" }, c.why))))) : null,
    r.notes?.length ? h("ul", {}, r.notes.map((n) => h("li", {}, n))) : null,
    taken
      ? h("div", { class: "notice ok" }, t("Taken over into your plan."), " ", h("a", { href: `#/training/plan/${m.applied_plan_id || r.plan_id}` }, t("Open plan")), " ",
        h("button", { type: "button", onclick: () => act("undo") }, icon("history", { size: 15 }), t("Undo")))
      : r.changes.length ? h("div", { class: "toolbar" },
        h("button", { class: "primary", onclick: () => act("take-over", { changes: boxes.map((b, i) => (b.checked ? i : null)).filter((i) => i !== null) }) }, t("Take over the ticked changes")),
        h("span", { class: "muted small" }, t("Weights and targets stay with your progression.")), h("span", { class: "grow" }), drop)
        : h("div", {}, drop),
    err,
    h("div", { class: "muted small" }, [m.model, m.duration_ms ? t("{n} s", { n: Math.round(m.duration_ms / 1000) }) : null].filter(Boolean).join(" · ")));
}

/** #/training/coach: a plan from the questionnaire, debriefs of workouts. */
export async function coachView(ctx) {
  const [status, sessions, muscles, equipment, profile] = await Promise.all([coachStatus(), api("/api/v1/training/sessions"),
    api("/api/v1/training/muscles"), api("/api/v1/training/equipment"), api(`${C}/profile`).catch(() => null)]);
  const head = trainingHead(ctx, { section: "coach" });
  if (!status.enabled) {
    return h("div", {}, head, card({ title: t("AI coach"), icon: "sparkles" }, null, h("div", { class: "muted" },
      t("No AI model is set up. Connect your local model under Settings → AI; the coach then debriefs your workouts."))));
  }
  let answers = profile;
  // plan: the newest proposal (pending ones are polled); a new one starts the questionnaire
  const planBox = h("div");
  const planList = h("div");
  const startWizard = () => planBox.replaceChildren(questionnaire(answers, muscles, equipment, async (a) => {
    try {
      await api(`${C}/plan`, { method: "POST", json: { answers: a, lang: lang() === "en" ? "en" : "de" } });
      answers = a;
      planBox.replaceChildren();
      refreshPlans();
    } catch (e) { alert(e.message); }
  }));
  const refreshPlans = polled(planList, () => api(`${C}/messages`, { params: { kind: "plan", limit: 5 } }), (msgs) => (msgs.length
    ? h("div", { class: "stack" }, msgs.map((m, i) => (i === 0 || m.status === "pending"
      ? card({ title: t("Plan proposal"), icon: "clipboard-list" }, h("span", { class: "muted small" }, f.dateTime(m.created_at)),
        proposalCard(m, { answers, muscles, onChange: () => refreshPlans() }))
      : h("details", { class: "card" }, h("summary", {}, `${m.result?.name || t("Plan proposal")} · ${f.dateTime(m.created_at)}`),
        proposalCard(m, { answers, muscles, onChange: () => refreshPlans() })))))
    : null));

  // review of the active plan
  const reviewNote = h("textarea", { class: "wide", rows: 2, maxlength: 1000, placeholder: t("optional: what bothers you, e.g. Fridays are too long, shoulders get sore") });
  const reviewList = h("div");
  const refreshReviews = polled(reviewList, () => api(`${C}/messages`, { params: { kind: "review", limit: 3 } }), (msgs) => (msgs.length
    ? h("div", { class: "stack" }, msgs.map((m, i) => (i === 0 || m.status === "pending"
      ? card({ title: t("Plan review"), icon: "list-check" }, h("span", { class: "muted small" }, f.dateTime(m.created_at)), reviewCard(m, () => refreshReviews()))
      : h("details", { class: "card" }, h("summary", {}, `${t("Plan review")} · ${f.dateTime(m.created_at)}`), reviewCard(m, () => refreshReviews())))))
    : null));
  const askReview = async () => {
    try {
      await api(`${C}/review`, { method: "POST", json: { note: reviewNote.value.trim() || null, lang: lang() === "en" ? "en" : "de" } });
      reviewNote.value = "";
      refreshReviews();
    } catch (e) { alert(e.message); }
  };

  const usable = sessions.filter((s) => s.sets - (s.unassigned || 0) > 0).slice(0, 15);
  const pick = h("select", {}, usable.map((s) => h("option", { value: s.id }, `${f.numDate(s.start_ts.slice(0, 10))} · ${s.display_title}${s.reviewed ? "" : ` (${t("not reviewed")})`}`)));
  const list = h("div");
  const refresh = polled(list, () => api(`${C}/messages`, { params: { kind: "debrief", limit: 20 } }), (msgs) => (msgs.length
    ? h("div", { class: "stack" }, msgs.map((m) => card({ title: m.title || t("Workout"), icon: "barbell" },
      h("span", { class: "toolbar" }, h("a", { href: `#/training/session/${m.activity_id}` }, t("Workout")),
        m.status !== "pending" ? h("button", { type: "button", class: "link-btn", title: t("Delete"), onclick: () => discard(m, refresh) }, icon("trash", { size: 15 })) : null),
      debriefCard(m, { showTitle: true }))))
    : h("div", { class: "muted small" }, t("No debriefs yet."))));
  const ask = async () => {
    try {
      await api(`${C}/debrief`, { method: "POST", json: { activity_id: Number(pick.value), lang: lang() === "en" ? "en" : "de" } });
      refresh();
    } catch (e) { alert(e.message); }
  };
  return h("div", {}, head,
    card({ title: t("Create a training plan"), icon: "clipboard-list" }, h("span", { class: "muted small" }, status.model),
      h("div", { class: "muted small" }, t("A few questions on goal, time, equipment and focus; the local model builds a plan from your exercises and the library. You check it, refine it and take it over as a new plan.")),
      h("div", {}, h("button", { class: "primary", onclick: startWizard }, icon("sparkles", { size: 16 }), answers ? t("New plan from the questionnaire") : t("Start the questionnaire")))),
    planBox, planList,
    h("div", { class: "section-title" }, icon("list-check", { size: 16 }), t("Plan review")),
    card({ title: t("Review the active plan"), icon: "list-check" }, null,
      h("div", { class: "muted small" }, t("The model reads the last 12 weeks against the plan: stalls, skipped days, session length, volume per muscle. It proposes changes only where the data says so; you tick what to take over.")),
      reviewNote, h("div", {}, h("button", { class: "primary", onclick: askReview }, icon("sparkles", { size: 16 }), t("Review the plan")))),
    reviewList,
    h("div", { class: "section-title" }, icon("history", { size: 16 }), t("Debriefs")),
    card({ title: t("Debrief a workout"), icon: "sparkles" }, null,
      usable.length ? h("div", { class: "toolbar" }, pick, h("button", { class: "primary", onclick: ask }, t("Debrief")))
        : h("div", { class: "muted small" }, t("No workout with assigned sets yet.")),
      h("div", { class: "muted small" }, t("A reading of one workout: score, what went well, what to watch, what to do next time. Plan and targets stay as your progression sets them."))),
    list);
}
