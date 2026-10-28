// Weekly (on Sundays) and monthly (on the last day of a month) review cards: the period's numbers
// from the server plus an autosaving text.

import { api } from "./api.js";
import { h } from "./dom.js";
import * as f from "./format.js";
import { locale, t } from "./i18n.js";
import { sourceLabel, textEditor } from "./textEditor.js";

export const isSunday = (day) => new Date(`${day}T00:00:00Z`).getUTCDay() === 0;
export const isMonthEnd = (day) => f.addDays(day, 1).endsWith("-01");

/** Review scopes due on `day`, in display order. */
export const reviewsDue = (day) => [isSunday(day) && "week", isMonthEnd(day) && "month"].filter(Boolean);

export function isoWeek(day) {
  const d = new Date(`${day}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + 3 - ((d.getUTCDay() + 6) % 7));
  const jan4 = new Date(Date.UTC(d.getUTCFullYear(), 0, 4));
  return 1 + Math.round(((d - jan4) / 86_400_000 - 3 + ((jan4.getUTCDay() + 6) % 7)) / 7);
}

function title(r) {
  if (r.scope === "week") {
    return t("Weekly review · week {n} ({from} – {to})", { n: isoWeek(r.start), from: f.shortDate(r.start), to: f.shortDate(r.end) });
  }
  const month = new Intl.DateTimeFormat(locale(), { month: "long", year: "numeric", timeZone: "UTC" }).format(new Date(`${r.start}T00:00:00Z`));
  return t("Monthly review · {month}", { month });
}

const PROMPTS = {
  week: "How was the week? What went well, what didn't, what do you take into next week?",
  month: "Looking back on the month: highlights, lows, progress on your goals, what changes next month?",
};

/** Card for the week or month containing `day`. Loads itself; returns the card at once. */
export function reviewCard(scope, day) {
  const body = h("div", {}, h("div", { class: "muted small" }, t("Loading …")));
  const extra = h("span", { class: "small muted" });
  const head = h("h2", {}, scope === "week" ? t("Weekly review") : t("Monthly review"));
  const node = h("section", { class: ["card", "review-card", `review-${scope}`] }, h("div", { class: "card-head" }, head, extra), body);
  api(`/api/v1/journal/${scope}/${day}`).then((r) => {
    head.textContent = title(r);
    const editor = textEditor({
      url: `/api/v1/journal/${scope}/${day}`, state: r, revisions: { scope, day }, rows: 6, placeholder: t(PROMPTS[scope]),
    });
    extra.replaceWith(editor.status);
    body.replaceChildren(factsSummary(r.facts, scope), editor.node);
    if (r.source && r.source !== "webui" && r.text) {
      body.append(h("div", { class: "small muted" }, t("Written by {who}", { who: sourceLabel(r.source) })));
    }
  }).catch((e) => body.replaceChildren(h("div", { class: "error" }, e.message)));
  return node;
}

const FACTS = [
  ["daily.sleep_score", "Sleep score", 0, "higher"],
  ["daily.hrv_last_night_avg", "HRV", 0, null],
  ["resting_heart_rate", "Resting HR", 0, "lower"],
  ["steps", "Steps", 0, null],
  ["active_energy", "Active energy", 0, null],
  ["stress", "Avg. stress", 0, "lower"],
  ["checkin.mood", "Mood", 1, "higher"],
  ["checkin.energy", "Felt energy", 1, "higher"],
  ["nutrition.energy", "Energy intake", 0, null],
  ["nutrition.drinks", "Drinks", 0, null],
];

/** Averages of the period with the change against the previous period of equal length. */
export function factsSummary(facts, scope) {
  const prevLabel = scope === "week" ? t("vs. previous week") : t("vs. previous month");
  const tiles = FACTS.filter(([k]) => facts.metrics[k]?.avg != null).map(([k, label, dec, better]) => {
    const m = facts.metrics[k];
    const diff = m.previous_avg != null ? m.avg - m.previous_avg : null;
    const good = diff == null || better == null || Math.abs(diff) < 10 ** -dec ? "" : (diff > 0) === (better === "higher") ? "up-good" : "up-bad";
    return h("div", { class: "fact" },
      h("div", { class: "muted small" }, t(label)),
      h("b", { class: "num" }, f.num(m.avg, dec)),
      h("div", { class: ["small", "muted", good], title: prevLabel }, diff == null ? t("{n} days", { n: m.days })
        : `${diff > 0 ? "↑" : diff < 0 ? "↓" : "="} ${f.num(Math.abs(diff), dec)} · ${t("{n} days", { n: m.days })}`));
  });
  const s = facts.sleep;
  if (s.nights) {
    tiles.unshift(h("div", { class: "fact" }, h("div", { class: "muted small" }, t("Sleep window")),
      h("b", { class: "num" }, `${s.bedtime}–${s.wake_time}`),
      h("div", { class: "small muted" }, t("Ø {d} asleep · {n} nights", { d: f.duration(s.asleep_minutes), n: s.nights }))));
  }
  const a = facts.activities;
  tiles.push(h("div", { class: "fact" }, h("div", { class: "muted small" }, t("Workouts")),
    h("b", { class: "num" }, f.num(a.count)),
    h("div", { class: "small muted" }, a.count ? t("{n} min · {kcal} kcal", { n: f.num(a.minutes), kcal: f.num(a.kcal) }) : "–")));
  const rw = facts.rewards;
  if (rw) {
    const diff = rw.flames - rw.previous_flames;
    tiles.push(h("div", { class: "fact" }, h("div", { class: "muted small" }, t("Flames")),
      h("b", { class: "num" }, `🔥 ${f.num(rw.flames)}`),
      h("div", { class: ["small", "muted", diff > 0 ? "up-good" : diff < 0 ? "up-bad" : ""], title: prevLabel },
        `${diff > 0 ? "↑" : diff < 0 ? "↓" : "="} ${f.num(Math.abs(diff))} · ${t("Level {n}", { n: rw.level })}`)));
    tiles.push(h("div", { class: "fact" }, h("div", { class: "muted small" }, t("Perfect days")),
      h("b", { class: "num" }, `⭐ ${f.num(rw.perfect_days)}`),
      h("div", { class: "small muted" }, rw.challenges.total ? t("Challenges {done}/{n}", { done: rw.challenges.done, n: rw.challenges.total }) : "–")));
  }
  tiles.push(h("div", { class: "fact" }, h("div", { class: "muted small" }, t("Journal days")),
    h("b", { class: "num" }, f.num(facts.journal_days)), h("div", { class: "small muted" }, t("of {n}", { n: facts.period.days }))));

  const extras = [];
  if (facts.symptoms.length) {
    extras.push(h("div", { class: "small" }, h("span", { class: "muted" }, `${t("Symptoms")}: `),
      facts.symptoms.map((x) => t("{label} ({n}×)", { label: x.label, n: x.days })).join(", ")));
  }
  if (facts.habits.length) {
    extras.push(h("div", { class: "small" }, h("span", { class: "muted" }, `${t("Habits")}: `),
      facts.habits.map((x) => `${x.name} ${x.done_days}/${x.goal_days}`).join(" · ")));
  }
  if (rw?.achievements.length) {
    extras.push(h("div", { class: "small" }, h("span", { class: "muted" }, `${t("New achievements")}: `),
      rw.achievements.map((a) => `${a.icon} ${t(a.title)}${a.habit ? ` (${a.habit})` : ""}`).join(" · ")));
  }
  if (rw?.routines_completed) {
    extras.push(h("div", { class: "small" }, h("span", { class: "muted" }, `${t("Routines")}: `), t("{n}× complete", { n: rw.routines_completed })));
  }
  if (facts.weight.days) {
    extras.push(h("div", { class: "small" }, h("span", { class: "muted" }, `${t("Weight")}: `),
      facts.weight.days > 1 ? `${f.num(facts.weight.first, 1)} → ${f.num(facts.weight.last, 1)} kg` : `${f.num(facts.weight.last, 1)} kg`));
  }
  return h("div", { class: "review-facts" },
    h("div", { class: "facts-grid" }, tiles),
    extras.length ? h("div", { class: "facts-extra" }, extras) : null,
    h("div", { class: "small muted" }, t("Averages over days with values; arrows compare with the {p}.", {
      p: scope === "week" ? t("previous week") : t("previous month") })));
}

/** Compact reference for the journal list. */
export function reviewBadge(r) {
  return h("span", { class: "badge accent" }, r.scope === "week" ? t("Week {n}", { n: isoWeek(r.start) }) : t("Month"));
}
