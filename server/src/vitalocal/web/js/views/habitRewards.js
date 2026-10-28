// Habit gamification: flames, level, records, perfect days, achievements and habit templates.

import { api } from "../api.js";
import { uuid4 } from "../checkin.js";
import { h } from "../dom.js";
import * as f from "../format.js";
import { plant } from "../garden.js";
import { t } from "../i18n.js";
import { glyph, icon } from "../icons.js";
import { card, iconBtn, sheet } from "../ui.js";

const span = (n, unit) => t(unit === "weeks" ? (n === 1 ? "{n} week" : "{n} weeks") : (n === 1 ? "{n} day" : "{n} days"), { n });
const nameOf = (x) => x.name;
/** A habit's (or routine's) name with its icon. */
export const named = (x, size = 16) => h("span", { class: "named" }, x.icon ? glyph(x.icon, { size }) : null, x.name);
const habitNamed = (name, ic) => named({ name, icon: ic }, 14);
const flame = (size = 14) => icon("flame", { size, cls: "flame-ic" });

/** What an achievement asks for, from its group and parameters. */
function describe(a) {
  const p = a.params || {};
  switch (a.group) {
    case "flames": return p.n === 1 ? t("Earn your first flame") : t("Collect {n} flames in total", p);
    case "perfect": return t(p.n === 1 ? "Do every daily habit on one day" : "Do every daily habit on {n} days", p);
    case "perfect_streak": return t("Every daily habit, {n} days in a row", p);
    case "weekend": case "perfect_weekend": return t("A perfect Saturday and Sunday");
    case "day": return t("{n} flames on one day", p);
    case "juggler": return t("{n} habits at {days}+ days in a row at the same time", p);
    case "comeback": return t("Back to {n} days in a row after a break of {gap}+ days", p);
    case "streak": return t(p.unit === "weeks" ? "{n} weeks in a row" : "{n} days in a row", p);
    case "habit_flames": return t("{n} flames with this habit", p);
    case "challenges": return t(p.n === 1 ? "Complete a weekly challenge" : "Complete {n} weekly challenges", p);
    case "clean_sweep": return t("Complete every challenge of a week");
    case "routines": return t(p.n === 1 ? "Complete a routine" : "Complete routines {n} times", p);
    case "routine_streak": return t("A routine {n} days in a row", p);
    case "joker": return t("A joker saved your streak");
    case "garden_bloom": return t("A plant in your garden blooms");
    case "garden_tree": return t("A plant in your garden grows into a tree");
    default: return "";
  }
}

function levelBar(level) {
  return h("div", { class: "level-track", role: "progressbar", "aria-valuemin": level.floor, "aria-valuemax": level.next, "aria-valuenow": level.flames },
    h("span", { style: { width: `${Math.round(level.progress * 100)}%` } }));
}

/** The "new badge" notice; `onSeen` after the user acknowledged it. */
export function newBadges(r, onSeen) {
  if (!r.new.length) return null;
  const box = h("section", { class: "card reward-new", role: "status" },
    h("div", { class: "reward-new-head" },
      h("div", {}, h("h2", {}, r.new.length === 1 ? t("New achievement!") : t("{n} new achievements!", { n: r.new.length })),
        h("div", { class: "muted small" }, t("Earned with your habits – keep the flames burning."))),
      h("button", { class: "primary", onclick: async () => {
        await api("/api/v1/habits/rewards/seen", { method: "POST" });
        box.classList.add("leaving");
        setTimeout(() => { box.remove(); onSeen?.(); }, 250);
      } }, t("Great!"))),
    h("div", { class: "badge-grid compact" }, r.new.slice(0, 12).map((a) => badge(a))),
    r.new.length > 12 ? h("div", { class: "muted small" }, t("… and {n} more below", { n: r.new.length - 12 })) : null);
  return box;
}

/** Flames and level as a colourful banner, with today's flames and the garden in small. */
export function rewardHero(r, status, { garden = true } = {}) {
  const { level, flames } = r;
  const toNext = level.next - level.flames;
  const openNames = r.perfect.open_today.map((id) => status.find((x) => x.id === id)).filter(Boolean).map(nameOf);
  return h("section", { class: "reward-banner" },
    h("div", { class: "banner-main" },
      h("div", { class: "banner-flames" }, h("span", { class: "flame-icon" }, icon("flame", { size: 34 })),
        h("div", {}, h("div", { class: "reward-big" }, f.num(flames.total)), h("div", { class: "small" }, t("Flames")))),
      h("div", { class: "banner-level" },
        h("div", { class: "small" }, h("b", {}, t("Level {n}", { n: level.level })), " · ", t(level.title)),
        levelBar(level),
        h("div", { class: "small banner-sub" }, t("{n} more to level {next}", { n: toNext, next: level.level + 1 }), " · ",
          t("Today +{today} · this week +{week}", flames))),
      h("div", { class: "banner-stat" }, icon("star", { size: 20 }),
        h("div", {}, h("b", {}, f.num(r.perfect.current)), h("div", { class: "small" }, t("perfect days in a row")))),
      h("div", { class: "banner-stat" }, icon("medal-2", { size: 20 }),
        h("div", {}, h("b", {}, f.num(r.unlocked)), h("div", { class: "small" }, t("achievements")))),
      garden && r.garden.length ? h("a", { class: "banner-garden", href: "#/habits/garden", title: t("Habit garden") },
        r.garden.slice(0, 6).map((g) => h("span", { class: ["mini-plant", `health-${g.health}`] }, plant(g.stage, { size: 44 })))) : null),
    r.perfect.today ? h("div", { class: "banner-note" }, icon("sparkles", { size: 16 }), t("Today is perfect!"))
      : openNames.length ? h("div", { class: "banner-note" }, t("Still open today: {list}", {
        list: openNames.length > 3 ? `${openNames.slice(0, 3).join(", ")} ${t("and {n} more", { n: openNames.length - 3 })}` : openNames.join(", "),
      })) : null);
}

/** Longest streak, record, perfect days and achievements as tiles (garden tab). */
export function recordTiles(r) {
  const best = r.best_current, record = r.best_ever;
  return h("div", { class: "tiles" },
    h("div", { class: "tile" }, h("div", { class: "tile-label" }, flame(), t("Longest running streak")),
      h("div", { class: "tile-value" }, best ? span(best.streak, best.unit) : "–"),
      h("div", { class: "tile-delta" }, best ? named(best, 14) : t("Tick something off today to start a streak."))),
    h("div", { class: "tile" }, h("div", { class: "tile-label" }, icon("trophy", { size: 14 }), t("Record")),
      h("div", { class: "tile-value" }, record ? span(record.best_streak, record.unit) : "–"),
      record ? h("div", { class: "tile-delta" }, named(record, 14), record.best_from ? ` · ${f.shortDate(record.best_from)} – ${f.shortDate(record.best_to)}` : "") : null),
    h("div", { class: "tile" }, h("div", { class: "tile-label" }, icon("star", { size: 14 }), t("Perfect days")),
      h("div", { class: "tile-value" }, f.num(r.perfect.total)),
      h("div", { class: "tile-delta" }, t("Best run {best} · {total} in total", r.perfect))),
    h("div", { class: "tile" }, h("div", { class: "tile-label" }, icon("medal-2", { size: 14 }), t("Achievements")),
      h("div", { class: "tile-value" }, f.num(r.unlocked)),
      h("div", { class: "tile-delta" }, flames_bonus(r))));
}
const flames_bonus = (r) => (r.flames.bonus ? t("including {n} bonus flames from routines and challenges", { n: f.num(r.flames.bonus) }) : "");

function badge(a) {
  const locked = !a.unlocked_on;
  const pct = Math.round((a.progress / a.target) * 100);
  return h("div", { class: ["ach", `tier-${a.tier}`, locked && "locked", a.new && "new"], "data-new": a.new ? t("NEW") : null,
    title: `${t(a.title)} – ${describe(a)}${a.unlocked_on ? ` · ${f.mediumDate(a.unlocked_on)}` : ""}` },
    h("div", { class: "ach-icon", "aria-hidden": "true" }, glyph(a.icon, { size: 24 })),
    h("div", { class: "ach-title" }, t(a.title)),
    a.ranks > 1 ? h("div", { class: "ach-rank" }, rankName(a)) : null,
    h("div", { class: "ach-desc" }, describe(a)),
    locked
      ? h("div", { class: "ach-progress" }, h("div", { class: "level-track thin" }, h("span", { style: { width: `${pct}%` } })),
        h("span", {}, `${f.num(a.progress)}/${f.num(a.target)}`))
      : h("div", { class: "ach-date" }, a.habit ? h("div", { class: "ach-habit" }, habitNamed(a.habit, a.habit_icon)) : null, f.mediumDate(a.unlocked_on)));
}

const RANK_NAMES = { bronze: "Bronze", silver: "Silver", gold: "Gold", platinum: "Platinum", diamond: "Diamond",
  master: "Master", grandmaster: "Grandmaster", legend: "Legend" };
const rankName = (a) => t(RANK_NAMES[a.tier] || a.tier);

/** One series as a card: the highest rank reached and the next one with its progress. */
function seriesCard(steps) {
  const reached = steps.filter((a) => a.unlocked_on);
  const cur = reached[reached.length - 1];
  const next = steps.find((a) => !a.unlocked_on);
  const pct = next ? Math.round((next.progress / next.target) * 100) : 100;
  return h("div", { class: ["series", `tier-${cur.tier}`, cur.new && "new"], "data-new": cur.new ? t("NEW") : null },
    h("div", { class: "ach-icon", "aria-hidden": "true" }, glyph(cur.icon, { size: 24 })),
    h("div", { class: "series-body" },
      cur.habit ? h("div", { class: "ach-habit small" }, habitNamed(cur.habit, cur.habit_icon)) : null,
      h("div", { class: "series-title" }, t(cur.title)),
      h("div", { class: "series-rank" }, steps.length > 1 ? `${rankName(cur)} · ${t("rank {n} of {m}", { n: cur.rank + 1, m: steps.length })}` : rankName(cur)),
      h("div", { class: "ach-desc" }, describe(cur), " · ", f.mediumDate(cur.unlocked_on)),
      next ? h("div", { class: "series-next" },
        h("span", { class: "muted small next-line" }, t("next:"), glyph(next.icon, { size: 14 }), `${t(next.title)} (${rankName(next)})`),
        h("div", { class: "ach-progress" }, h("div", { class: "level-track thin" }, h("span", { style: { width: `${pct}%` } })),
          h("span", {}, `${f.num(next.progress)}/${f.num(next.target)}`)),
        h("div", { class: "ach-desc" }, describe(next)))
        : h("div", { class: "small ok" }, steps.length > 1 ? t("Highest rank reached!") : "")));
}

/** Achievements: series appear once their first rank is earned; "Earned" lists every badge. */
export function achievementsCard(r) {
  const bySeries = new Map();
  for (const a of r.achievements) {
    if (!bySeries.has(a.series)) bySeries.set(a.series, []);
    bySeries.get(a.series).push(a);
  }
  const all = [...bySeries.values()].map((steps) => steps.sort((a, b) => a.rank - b.rank));
  const visible = all.filter((steps) => steps.some((a) => a.unlocked_on));
  const hidden = all.length - visible.length;
  const global = visible.filter((s) => !s[0].habit_id), perHabit = visible.filter((s) => s[0].habit_id);
  const earned = r.achievements.filter((a) => a.unlocked_on).sort((a, b) => b.unlocked_on.localeCompare(a.unlocked_on) || b.rank - a.rank);
  const filter = h("div", { class: "segmented" });
  const body = h("div");
  let mode = "progress";
  const paint = () => {
    if (mode === "earned") {
      body.replaceChildren(earned.length ? h("div", { class: "badge-grid" }, earned.map(badge))
        : h("div", { class: "muted small" }, t("Nothing earned yet – tick off your first habit.")));
    } else {
      body.replaceChildren(...[
        global.length ? h("div", { class: "series-grid" }, global.map(seriesCard)) : null,
        perHabit.length ? h("h3", { class: "medal-head" }, t("Per habit")) : null,
        perHabit.length ? h("div", { class: "series-grid" }, perHabit.map(seriesCard)) : null,
        hidden ? h("div", { class: "secret-hint" }, icon("lock", { size: 16 }), " ", t(hidden === 1 ? "One more achievement is waiting to be discovered." : "{n} more achievements are waiting to be discovered.", { n: hidden })) : null,
      ].filter(Boolean));
    }
    for (const b of filter.children) b.classList.toggle("active", b.dataset.mode === mode);
  };
  for (const [m, label] of [["progress", t("Progress")], ["earned", `${t("Earned")} (${earned.length})`]]) {
    filter.append(h("button", { "data-mode": m, onclick: () => { mode = m; paint(); } }, label));
  }
  paint();
  const c = card({ title: t("Achievements"), icon: "medal-2" }, filter, body);
  c.id = "achievements";
  return c;
}

const schedule = (x) => (x.per_week >= 7 ? t("daily") : t("{n}× per week", { n: x.per_week }));

/** Predefined habits, grouped by category; one click adds a habit. */
export async function templatesSheet(existing, onAdded, onClose) {
  const templates = await api("/api/v1/habits/templates");
  const names = new Set(existing.filter((x) => !x.archived).map((x) => x.name.trim().toLowerCase()));
  const groups = new Map();
  for (const tp of templates) {
    if (!groups.has(tp.category)) groups.set(tp.category, []);
    groups.get(tp.category).push(tp);
  }
  const add = async (tp, btn) => {
    btn.disabled = true;
    try {
      await api("/api/v1/habits", { method: "POST", json: {
        id: uuid4(), name: t(tp.name), icon: tp.icon, template: tp.key, kind: tp.kind,
        unit: tp.unit ? t(tp.unit) : null, target: tp.target, per_week: tp.per_week,
        metric: tp.metric, min_value: tp.min_value, max_value: tp.max_value, position: 0, archived: false,
      } });
      btn.replaceChildren(icon("check", { size: 16 }), t("already added"));
      onAdded();
    } catch (e) {
      btn.disabled = false;
      btn.textContent = e.message;
    }
  };
  return sheet({ title: t("Habit templates"), icon: "sparkles", wide: true, onClose, body: [
    h("div", { class: "muted small" },
      t("Pick ready-made habits; you can change name, goal and icon afterwards. Automatic ones are evaluated from your data.")),
    [...groups].map(([cat, list]) => h("div", { class: "tpl-group" },
      h("div", { class: "reward-label" }, t(cat)),
      h("div", { class: "tpl-grid" }, list.map((tp) => {
        const has = tp.added || names.has(t(tp.name).toLowerCase());
        const btn = h("button", { class: has ? "ghost" : "", disabled: has, onclick: (e) => add(tp, e.currentTarget) },
          icon(has ? "check" : "plus", { size: 16 }), has ? t("already added") : t("Add"));
        const rule = tp.metric ? t("automatic") : tp.kind === "count" ? `${f.num(tp.target)} ${t(tp.unit)}` : t("Tick off");
        return h("div", { class: ["tpl", has && "added"] },
          h("div", { class: "tpl-icon", "aria-hidden": "true" }, glyph(tp.icon, { size: 22 })),
          h("div", { class: "tpl-body" },
            h("div", { class: "habit-name" }, t(tp.name)),
            h("div", { class: "muted small" }, `${schedule(tp)} · ${rule}`),
            h("div", { class: "muted small" }, t(tp.why))),
          btn);
      })))),
  ] });
}

/** "+1" with a flame rising from an element when a habit was just completed. */
export function flamePop(el) {
  if (!el?.isConnected || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const pop = h("span", { class: "flame-pop", "aria-hidden": "true" }, "+1", flame());
  el.parentElement.style.position ||= "relative";
  const r = el.getBoundingClientRect(), p = el.parentElement.getBoundingClientRect();
  pop.style.left = `${r.left - p.left + r.width / 2}px`;
  pop.style.top = `${r.top - p.top}px`;
  el.parentElement.append(pop);
  setTimeout(() => pop.remove(), 900);
}

// --- garden --------------------------------------------------------------------------------------

const HEALTH = { healthy: null, thirsty: "droplet", wilting: "droplet-off" };
const healthText = (g) => ({ healthy: t("healthy"), thirsty: t("thirsty – missed yesterday"), wilting: t("wilting – missed {n} times", { n: g.misses }) })[g.health];

/** One pot per active habit: the plant grows with done days and wilts after missed ones. */
export function gardenView(garden, { compact = false } = {}) {
  if (!garden.length) return h("div", { class: "muted small" }, t("No habits yet."));
  return h("div", { class: ["garden", compact && "compact"] }, garden.map((g) => {
    const toNext = g.next_at != null ? Math.ceil(g.next_at - g.growth) : null;
    return h("div", { class: ["pot", `stage-${g.stage_index}`, `health-${g.health}`],
      title: `${nameOf(g)} · ${t(g.stage_title)} · ${healthText(g)}${toNext != null ? ` · ${t("{n} more days to the next stage", { n: toNext })}` : ""}` },
      h("div", { class: "plant", "aria-hidden": "true" }, plant(g.stage, { size: compact ? 44 : 64 }),
        HEALTH[g.health] ? h("span", { class: "plant-health" }, icon(HEALTH[g.health], { size: 14 })) : null),
      h("div", { class: "pot-name" }, named(g, 12)),
      compact ? null : h("div", { class: "pot-sub" }, t(g.stage_title),
        toNext != null ? h("div", { class: "level-track thin" }, h("span", { style: { width: `${Math.round((100 * (g.growth - g.floor)) / (g.next_at - g.floor))}%` } })) : null));
  }));
}

export function gardenCard(r) {
  const thirsty = r.garden.filter((g) => g.health !== "healthy").length;
  return card({ title: t("Habit garden"), icon: "plant-2", cls: "garden-card" }, h("span", { class: ["badge", thirsty ? "warn" : "ok"] },
    thirsty ? t("{n} plants need water", { n: thirsty }) : t("everything is green")),
  gardenView(r.garden),
  h("div", { class: "muted small", style: { marginTop: "12px" } },
    t("Each done day lets a plant grow (seed → sprout → bush → bloom at 21 → tree at 66 days); a missed day costs three days of growth, a joker day costs nothing.")));
}

// --- challenges ----------------------------------------------------------------------------------

function challengeText(c) {
  switch (c.kind) {
    case "habit": return t("{habit} {n} times this week", { habit: c.habit, n: c.target });
    case "perfect": return t(c.target === 1 ? "One perfect day this week" : "{n} perfect days this week", { n: c.target });
    case "routine": return t("Routine {name} {n} times this week", { name: c.routine, n: c.target });
    default: return t("Earn {n} flames this week", { n: c.target });
  }
}

export function challengesCard(r) {
  const list = r.challenges;
  const done = list.filter((c) => c.completed_on).length;
  return card({ title: t("Weekly challenges"), icon: "target" }, h("span", { class: "muted small" }, t("{done} of {n} done", { done, n: list.length })),
    list.length ? h("div", { class: "challenges" }, list.map((c) => h("div", { class: ["challenge", c.completed_on && "done"] },
      h("div", { class: "challenge-icon", "aria-hidden": "true" },
        icon(c.completed_on ? "circle-check" : { habit: "target", perfect: "star", routine: "link", flames: "flame" }[c.kind] || "target", { size: 22 })),
      h("div", { class: "challenge-body" },
        h("div", { class: "habit-name" }, challengeText(c)),
        h("div", { class: "ach-progress" }, h("div", { class: "level-track" }, h("span", { style: { width: `${Math.round((100 * c.progress) / c.target)}%` } })),
          h("span", {}, `${f.num(c.progress)}/${f.num(c.target)}`))),
      h("div", { class: "challenge-bonus" }, `+${c.bonus}`, flame())))) : h("div", { class: "muted small" }, t("Challenges appear once you have habits.")),
    h("div", { class: "muted small", style: { marginTop: "8px" } }, t("New every Monday, based on last week. Bonus flames on completion.")));
}

// --- routines ------------------------------------------------------------------------------------

/** Routines: habits done one after another; the whole chain earns bonus flames. */
export function routinesCard(r, all, { day, onChange }) {
  const habitById = new Map(all.map((x) => [x.id, x]));
  const yesterday = f.addDays(day, -1);
  const formBox = h("div");
  const open = (rt = null) => formBox.replaceChildren(routineForm(rt, all, () => { formBox.replaceChildren(); onChange(); }));
  const live = r.routines.filter((x) => !x.archived);
  return card({ title: t("Routines"), icon: "link" }, h("button", { class: "ghost", onclick: () => open() }, icon("plus", { size: 16 }), t("New routine")),
    live.length ? h("div", { class: "routines" }, live.map((rt) => h("div", { class: ["routine", rt.done_today && "done"] },
      h("div", { class: "routine-head" },
        h("b", {}, named(rt)),
        h("span", { class: "muted small" }, [
          rt.done_today ? t("done today") : t("+{n} flames when complete", { n: rt.bonus }),
          rt.days.includes(yesterday) ? t("yesterday done") : null,
          rt.streak ? t(rt.streak === 1 ? "{n} day in a row" : "{n} days in a row", { n: rt.streak }) : null,
          t("{n}× complete", { n: rt.completions })].filter(Boolean).join(" · ")),
        iconBtn("pencil", t("Edit"), () => open(rt))),
      h("div", { class: "chain" }, rt.habit_ids.map((id, i) => {
        const hb = habitById.get(id);
        if (!hb) return null;
        const doneStep = rt.today.includes(id);
        const manual = !hb.metric && hb.kind === "check" && day;
        return [i ? h("span", { class: "chain-arrow", "aria-hidden": "true" }, icon("chevron-right", { size: 14 })) : null,
          h(manual ? "button" : "span", { class: ["step", doneStep && "done", hb.metric && "auto"],
            title: manual ? t("tick off") : hb.metric ? t("fills in by itself from your data") : t("count it in the list"),
            onclick: manual ? async () => { await api(`/api/v1/habits/${id}/log/${day}`, { method: "PUT", json: { value: doneStep ? 0 : 1 } }); onChange(); } : null },
          doneStep ? icon("check", { size: 14 }) : hb.metric ? icon("device-watch", { size: 14 }) : null, named(hb, 14))];
      }))))) : h("div", { class: "muted small" }, t("Chain habits you do one after another, e.g. Brush teeth → Floss → Stretch. A complete routine earns one bonus flame per habit.")),
    live.length ? h("div", { class: "muted small", style: { marginTop: "8px" } },
      t("A routine completes by itself as soon as all of its habits are done – nothing to tick twice. Steps with a watch come from your data (bedtime counts once the watch has recorded the night).")) : null,
    formBox);
}

function routineForm(rt, all, done) {
  const habits = all.filter((x) => !x.archived);
  let chosen = rt ? [...rt.habit_ids] : [];
  const name = h("input", { type: "text", value: rt?.name || "", maxlength: 80, required: true, class: "wide", placeholder: t("e.g. Evening routine") });
  const iconIn = h("input", { type: "text", value: rt?.icon || "", maxlength: 16, class: "icon-input", placeholder: "🌙", "aria-label": t("Icon") });
  const chain = h("div", { class: "chain" });
  const picks = h("div", { class: "chips" });
  const err = h("div", { class: "error", hidden: true });
  const paint = () => {
    chain.replaceChildren(...(chosen.length ? chosen.flatMap((id, i) => {
      const hb = habits.find((x) => x.id === id) || all.find((x) => x.id === id);
      return [i ? h("span", { class: "chain-arrow" }, icon("chevron-right", { size: 14 })) : null,
        h("span", { class: "step" }, hb ? named(hb, 14) : "?",
          i ? h("button", { type: "button", class: "link-btn", "aria-label": t("earlier"), onclick: () => { [chosen[i - 1], chosen[i]] = [chosen[i], chosen[i - 1]]; paint(); } }, icon("arrow-left", { size: 14 })) : null,
          h("button", { type: "button", class: "link-btn", "aria-label": t("remove"), onclick: () => { chosen.splice(i, 1); paint(); } }, icon("x", { size: 14 })))];
    }) : [h("span", { class: "muted small" }, t("Pick the habits in the order you do them."))]));
    picks.replaceChildren(...habits.filter((x) => !chosen.includes(x.id)).map((x) =>
      h("button", { type: "button", class: "chip", onclick: () => { chosen.push(x.id); paint(); } }, icon("plus", { size: 12 }), named(x, 14))));
  };
  paint();
  const submit = async (e) => {
    e.preventDefault();
    const body = { id: rt?.id || uuid4(), name: name.value.trim(), icon: iconIn.value.trim() || null, habit_ids: chosen, position: rt?.position ?? 0, archived: false };
    try {
      await api(rt ? `/api/v1/habits/routines/${rt.id}` : "/api/v1/habits/routines", { method: rt ? "PUT" : "POST", json: body });
      done();
    } catch (ex) { err.hidden = false; err.textContent = chosen.length < 2 ? t("A routine needs at least two habits.") : ex.message; }
  };
  return h("form", { class: "form routine-form", onsubmit: submit },
    h("div", { class: "toolbar" }, h("label", { class: "form-row" }, h("span", {}, t("Name")), name), h("label", { class: "form-row" }, h("span", {}, t("Icon")), iconIn)),
    h("div", { class: "form-row" }, h("span", {}, t("Steps")), chain),
    picks, err,
    h("div", { class: "toolbar" },
      h("button", { class: "primary", type: "submit" }, t("Save")),
      h("button", { type: "button", onclick: () => done() }, t("Cancel")),
      rt ? h("button", { type: "button", class: "danger", onclick: async () => {
        if (!confirm(t("Delete this routine?"))) return;
        await api(`/api/v1/habits/routines/${rt.id}`, { method: "DELETE" });
        done();
      } }, t("Delete")) : null));
}
