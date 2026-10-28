import { api } from "../api.js";
import { calendarHeatmap } from "../charts.js";
import { checkinList, quickCheckin, todayIso } from "../checkin.js";
import { h, responsive } from "../dom.js";
import * as f from "../format.js";
import { t } from "../i18n.js";
import { isoWeek, reviewCard, reviewsDue } from "../reviews.js";
import { textEditor } from "../textEditor.js";
import { icon } from "../icons.js";
import { card, dayNav, pageHead, stat, streak, subnav } from "../ui.js";

const HEATMAP_DAYS = 365;

export async function journalView(ctx, requested, tab = "diary") {
  const today = todayIso();
  const day = requested || today;
  const [entry, history, reviews] = await Promise.all([
    api(`/api/v1/journal/${day}`),
    api("/api/v1/journal", { params: { from: f.addDays(today, -HEATMAP_DAYS), to: today } }),
    api("/api/v1/journal/reviews", { params: { from: f.addDays(today, -HEATMAP_DAYS), to: today } }),
  ]);
  const content = h("div");
  const search = searchField(content);
  const head = h("div", {},
    pageHead({ icon: "notebook", title: t("Journal"), sub: tab === "diary" ? f.longDate(day) : null, actions: [
      search,
      tab === "diary" ? dayNav({ day, today, go: (x) => ctx.navigate(x === today ? "#/journal" : `#/journal/${x}`), addDays: f.addDays, t, mediumDate: f.mediumDate }) : null,
    ] }),
    subnav([
      { href: "#/journal", label: t("Diary"), icon: "notebook", active: tab === "diary" },
      { href: "#/journal/reviews", label: t("Reviews"), icon: "calendar-stats", active: tab === "reviews", count: reviewsDue(today).length || null },
    ]));

  if (tab === "reviews") {
    content.append(h("div", { class: "stack" },
      reviewsDue(today).map((scope) => reviewCard(scope, today)),
      reviewsCard(ctx, reviews)));
    return h("div", {}, head, content);
  }

  const listBox = h("div");
  const refresh = async () => {
    const j = await api(`/api/v1/journal/${day}`);
    listBox.replaceChildren(checkinList(j.checkins, refresh, "No check-ins yet."));
  };
  listBox.append(checkinList(entry.checkins, refresh, "No check-ins yet."));
  const editor = textEditor({
    url: `/api/v1/journal/${day}`, state: entry, revisions: { scope: "day", day },
    placeholder: t("How was the day? Anything worth remembering – sleep, food, training, stress …"),
  });
  const due = reviewsDue(day);
  content.append(
    due.length ? h("div", { class: "notice accent", style: { marginBottom: "16px" } }, icon("calendar-stats"),
      h("span", { class: "grow" }, due.includes("month") ? t("Time for the monthly review.") : t("Time for the weekly review.")),
      h("a", { class: "btn", href: "#/journal/reviews" }, t("Write review"))) : null,
    h("div", { class: "grid-main" },
      h("div", { class: "stack" },
        card({ title: t("Journal"), icon: "pencil" }, editor.status, editor.node),
        card({ title: t("Check-in"), icon: "mood-smile" }, h("a", { href: `#/day/${day}`, class: "small" }, t("Day view")),
          quickCheckin(day, refresh), h("div", { class: "checkin-entries" }, listBox))),
      h("div", { class: "stack" },
        consistencyCard(ctx, history, today),
        timelineCard(ctx, history, day))));
  return h("div", {}, head, content);
}

/** Search in the page head: results replace the page until the field is cleared (Esc). */
function searchField(content) {
  const input = h("input", { type: "search", placeholder: t("Search …"), "aria-label": t("Search journal, reviews and notes …"), class: "head-search" });
  let page = null;
  let seq = 0;
  const restore = () => { if (page) { content.replaceChildren(...page); page = null; } };
  const run = async () => {
    const q = input.value.trim();
    const id = ++seq;
    if (q.length < 2) { restore(); return; }
    page ??= [...content.childNodes];
    try {
      const hits = await api("/api/v1/journal/search", { params: { q } });
      if (id !== seq) return;
      content.replaceChildren(card({ title: t("Search"), icon: "search", summary: t("{n} matches", { n: hits.length }) },
        h("span", { class: "muted small" }, t("words, \"exact phrase\", -exclude")),
        hits.length ? h("ul", { class: "search-hits" }, hits.map((x) => h("li", {},
          h("a", { href: hitLink(x) }, hitLabel(x)),
          h("div", { class: "small" }, highlight(x.snippet))))) : h("div", { class: "muted small" }, t("No matches."))));
    } catch (e) {
      if (id === seq) content.replaceChildren(h("div", { class: "error" }, e.message));
    }
  };
  let timer = null;
  input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(run, 300); });
  input.addEventListener("keydown", (e) => { if (e.key === "Escape") { input.value = ""; restore(); } });
  return h("label", { class: "search-wrap" }, icon("search", { size: 16 }), input);
}

// Reviews are edited on the Sunday / last day of their period.
const hitLink = (x) => `#/journal/${x.scope === "week" || x.scope === "month" ? x.end : x.start}`;

function hitLabel(x) {
  if (x.scope === "week") return t("Week {n} ({from} – {to})", { n: isoWeek(x.start), from: f.shortDate(x.start), to: f.shortDate(x.end) });
  if (x.scope === "month") return `${t("Month")} ${f.monthLabel(x.start)} ${x.start.slice(0, 4)}`;
  return `${f.mediumDate(x.start)}${x.scope === "note" ? ` · ${t("Note")}` : ""}`;
}

/** Search snippets mark matches with ⟦ ⟧; turn them into <mark> without using innerHTML. */
function highlight(snippet) {
  return snippet.split(/(⟦[^⟧]*⟧)/).map((part) => (part.startsWith("⟦") ? h("mark", {}, part.slice(1, -1)) : part));
}

function consistencyCard(ctx, rows, today) {
  // value per day: 1 for a journal text + number of check-ins
  const values = {};
  for (const r of rows) {
    const v = (r.excerpt ? 1 : 0) + (r.checkins || 0);
    if (v > 0) values[r.day] = v;
  }
  const active = Object.keys(values).filter((d) => d >= f.addDays(today, -29)).length;
  const run = streak(today, (d) => values[d] > 0, f.addDays);
  const heat = h("div");
  responsive(heat, (w) => calendarHeatmap({
    width: w, from: f.addDays(today, -181), to: today, values,
    describe: (d, v) => `${f.longDate(d)}: ${v ? `${v}` : "–"}`,
    onSelect: (d) => ctx.navigate(`#/journal/${d}`),
  }));
  return card({ title: t("Journal consistency"), icon: "calendar-stats" }, null,
    h("div", { class: "stat-row" },
      stat(t("Current streak"), t(run === 1 ? "{n} day in a row" : "{n} days in a row", { n: run })),
      stat(t("Last 30 days"), t("{n} of the last {days} days", { n: active, days: 30 }))),
    heat);
}

const dots = (v, label) => (v == null ? null : h("span", { class: "dots", title: `${label} ${f.num(v, 1)}` },
  Array.from({ length: 5 }, (_, i) => h("i", { class: i < Math.round(v) ? "on" : null }))));

/** The last days as a timeline: date, mood and energy as dots, symptoms, the start of the text. */
function timelineCard(ctx, rows, current) {
  const shown = rows.filter((r) => r.excerpt || r.mood != null || r.energy != null || (r.symptoms || []).length);
  if (!shown.length) return card({ title: t("Recent days"), icon: "history" }, null, h("div", { class: "muted small" }, t("No journal entries or check-ins yet.")));
  const list = h("div", { class: "timeline" });
  let n = 0;
  const more = h("button", { class: "ghost", onclick: () => add() }, t("Load older"));
  const add = () => {
    list.append(...shown.slice(n, n + 10).map((r) => h("a", { class: ["tl-item", r.day === current && "current"], href: `#/journal/${r.day}` },
      h("div", { class: "tl-date" }, h("b", {}, f.shortDate(r.day))),
      h("div", { class: "tl-body" },
        h("div", { class: "tl-meta" }, dots(r.mood, t("Mood")), dots(r.energy, t("Energy")),
          (r.symptoms || []).map((x) => h("span", { class: "badge" }, x))),
        r.excerpt ? h("div", { class: "tl-text" }, r.excerpt) : null))));
    n += 10;
    more.hidden = n >= shown.length;
  };
  add();
  return card({ title: t("Recent days"), icon: "history" }, h("span", { class: "legend" }, h("span", {}, t("Mood")), h("span", {}, "·"), h("span", {}, t("Energy"))), list, more);
}

function reviewsCard(ctx, reviews) {
  return card({ title: t("Weekly & monthly reviews"), icon: "calendar-stats" }, h("span", { class: "muted small" }, t("written on Sundays and on the last day of a month")),
    reviews.length ? h("ul", { class: "search-hits" }, reviews.slice(0, 20).map((r) => h("li", {},
      h("a", { href: hitLink(r) }, hitLabel(r)),
      h("div", { class: "small muted excerpt" }, r.excerpt))))
      : h("div", { class: "muted small" }, t("No reviews yet. The next weekly review appears on Sunday.")));
}
