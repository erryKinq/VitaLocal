// Hash router, main navigation and login gate.

import { AuthError, aiOn, api, capabilities, meta, moduleOn, token } from "./api.js";
import { clear, h } from "./dom.js";
import { setTimezone, setUnits } from "./format.js";
import { t } from "./i18n.js";
import { icon } from "./icons.js";
import { sheet } from "./ui.js";
import { activitiesView, activityView } from "./views/activities.js";
import { dayView } from "./views/day.js";
import { habitsView } from "./views/habits.js";
import { healthView } from "./views/health.js";
import { labImportView, labReportView } from "./views/labs.js";
import { journalView } from "./views/journal.js";
import { coachView } from "./views/coach.js";
import { manualView, planView, sessionView, trainingView } from "./views/training.js";
import { foodsView } from "./food.js";
import { nutritionView } from "./views/nutrition.js";
import { settingsView } from "./views/settings.js";
import { setupView } from "./views/setup.js";
import { trendsView } from "./views/trends.js";

const app = document.getElementById("app");
const DAY = "(\\d{4}-\\d{2}-\\d{2})";

// Main navigation, in the app's tab order first. `area` colours the icons and accents of that area;
// `module` hides the entry when that area is switched off (settings → areas).
const NAV_ALL = [
  { route: "day", href: "#/", label: "Overview", icon: "home", area: "day", primary: true },
  { route: "nutrition", href: "#/nutrition", label: "Nutrition", icon: "salad", area: "food", primary: true, module: "nutrition" },
  { route: "habits", href: "#/habits", label: "Habits", icon: "plant-2", area: "habit", primary: true, module: "habits" },
  { route: "training", href: "#/training", label: "Training", icon: "barbell", area: "train", primary: true, module: "training" },
  { route: "health", href: "#/health", label: "Health", icon: "heartbeat", area: "health", module: "health" },
  { route: "journal", href: "#/journal", label: "Journal", icon: "notebook", area: "mind", module: "journal" },
  { route: "stats", href: "#/stats", label: "Statistics", icon: "chart-line", area: "stats" },
];
let NAV = NAV_ALL;

// Old addresses (bookmarks, links in notes) lead to their new place.
const REDIRECTS = [
  [/^#\/activities(\/\d+)?$/, (m) => `#/training/general${m[1] || ""}`],
  [/^#\/trends$/, () => "#/stats"],
  [/^#\/data$/, () => "#/settings/data"],
];

const routes = [
  [/^#?\/?$/, "day", (m, ctx) => dayView(ctx)],
  [new RegExp(`^#/day/${DAY}$`), "day", (m, ctx) => dayView(ctx, m[1])],
  [/^#\/journal$/, "journal", (m, ctx) => journalView(ctx)],
  [/^#\/journal\/reviews$/, "journal", (m, ctx) => journalView(ctx, null, "reviews")],
  [new RegExp(`^#/journal/${DAY}$`), "journal", (m, ctx) => journalView(ctx, m[1])],
  [/^#\/habits$/, "habits", (m, ctx) => habitsView(ctx, "today")],
  [/^#\/habits\/(garden|manage)$/, "habits", (m, ctx) => habitsView(ctx, m[1])],
  [/^#\/settings(?:\/(appearance|profile|ai|app|data))?$/, "settings", (m, ctx) => settingsView(ctx, m[1] || "appearance")],
  [/^#\/setup$/, "settings", (m, ctx) => setupView(ctx)],
  [/^#\/nutrition$/, "nutrition", (m, ctx) => nutritionView(ctx)],
  [/^#\/nutrition\/foods$/, "nutrition", (m, ctx) => foodsView(ctx)],
  [new RegExp(`^#/nutrition/${DAY}$`), "nutrition", (m, ctx) => nutritionView(ctx, m[1])],
  [/^#\/health$/, "health", (m, ctx) => healthView(ctx)],
  [new RegExp(`^#/health/${DAY}$`), "health", (m, ctx) => healthView(ctx, m[1])],
  [/^#\/health\/labs\/new$/, "health", (m, ctx) => labReportView(ctx)],
  [/^#\/health\/labs\/report\/([0-9a-f-]{36})$/, "health", (m, ctx) => labReportView(ctx, m[1])],
  [/^#\/health\/labs\/import\/([0-9a-f-]{36})$/, "health", (m, ctx) => labImportView(ctx, m[1])],
  [/^#\/stats$/, "stats", (m, ctx) => trendsView(ctx)],
  [/^#\/training$/, "training", (m, ctx) => trainingView(ctx)],
  [/^#\/training\/(history|plans|equipment)$/, "training", (m, ctx) => trainingView(ctx, m[1])],
  [/^#\/training\/coach$/, "training", (m, ctx) => (aiOn("coach") ? coachView(ctx) : (ctx.navigate("#/training"), h("div")))],
  [/^#\/training\/new$/, "training", (m, ctx) => manualView(ctx)],
  [/^#\/training\/plan\/new$/, "training", (m, ctx) => planView(ctx)],
  [/^#\/training\/plan\/([0-9a-f-]{36})$/, "training", (m, ctx) => planView(ctx, m[1])],
  [/^#\/training\/session\/(\d+)$/, "training", (m, ctx) => sessionView(ctx, m[1])],
  [/^#\/training\/general$/, "training", (m, ctx) => activitiesView(ctx)],
  [/^#\/training\/general\/(\d+)$/, "training", (m, ctx) => activityView(ctx, m[1])],
];

/** The top navigation (a bottom bar with "More" on phones). */
function renderNav(active) {
  const nav = document.getElementById("nav");
  const more = h("a", { href: "#", class: ["more", !NAV.find((n) => n.route === active)?.primary && active !== "settings" ? "" : null],
    onclick: (e) => { e.preventDefault(); moreSheet(); } }, icon("menu-2"), h("span", {}, t("More")));
  clear(nav).append(...NAV.map((n) => h("a", {
    href: n.href, class: [`area-${n.area}`, n.route === active && "active", !n.primary && "secondary"],
    "aria-current": n.route === active ? "page" : null,
  }, icon(n.icon), h("span", {}, t(n.label)))), more);
  const nonPrimary = NAV.find((n) => n.route === active && !n.primary) || active === "settings";
  more.classList.toggle("active", !!nonPrimary);
  renderSettings();
  const gear = document.querySelector("#settings .icon-only");
  gear?.classList.toggle("active", active === "settings");
  const area = NAV.find((n) => n.route === active)?.area || "day";
  document.body.className = `area-${area}`;
}

function moreSheet() {
  const sh = sheet({ title: t("More"), body: [h("div", { class: "rows" },
    [...NAV.filter((n) => !n.primary), { href: "#/settings", label: "Settings", icon: "settings", area: "day" }].map((n) =>
      h("a", { href: n.href, class: `row link area-${n.area}`, onclick: () => sh.close() },
        h("span", { style: { color: "var(--cat)" } }, icon(n.icon)), h("span", { class: "grow" }, t(n.label)), icon("chevron-right"))))] });
}

let renderId = 0;

/** Draw the current route. `quiet`: replace the page only once the new one is ready, keeping
 * the scroll position (a refresh with new data, not a navigation). */
async function render({ quiet = false } = {}) {
  const id = ++renderId;
  const [hash, query = ""] = location.hash.split("?");
  for (const [re, to] of REDIRECTS) {
    const r = hash.match(re);
    if (r) { location.replace(to(r) + (query ? `?${query}` : "")); return; }
  }
  const match = routes.map(([re, name, fn]) => [hash.match(re), name, fn]).find(([m]) => m);
  if (!match) { location.hash = "#/"; return; }
  const [m, name, view] = match;
  renderNav(name);

  if (!quiet) clear(app).append(h("div", { class: "skeleton" }));
  try {
    const [md, caps] = await Promise.all([meta(), capabilities()]);
    setTimezone(md.timezone);
    setUnits(caps.units);
    // first run: the setup wizard, until it was finished or skipped once
    if (!caps.setup_done && hash !== "#/setup" && !hash.startsWith("#/settings")) { location.replace("#/setup"); return; }
    if (name !== "day" && name !== "settings" && NAV_ALL.find((n) => n.route === name && n.module && !moduleOn(n.module))) {
      location.replace("#/"); return;
    }
    NAV = NAV_ALL.filter((n) => !n.module || moduleOn(n.module));
    renderNav(name);
    const ctx = {
      meta: md,
      caps,
      params: new URLSearchParams(query),
      navigate: (to) => { location.hash = to; },
      rerender: () => render(),
      // after a change to settings that decide what is shown (AI, areas, profile)
      reloadCaps: async () => { await capabilities(true); render(); },
      refresh: () => render({ quiet: true }),
      isCurrent: () => id === renderId,
    };
    const node = await view(m, ctx);
    if (id === renderId) {
      const y = window.scrollY;
      clear(app).append(node);
      if (quiet) window.scrollTo(0, y);
    }
  } catch (e) {
    if (id !== renderId) return;
    if (e instanceof AuthError) { clear(app).append(loginView()); return; }
    console.error(e);
    clear(app).append(h("div", { class: "error" }, t("Something went wrong: {msg}", { msg: e.message })));
  }
}

function loginView() {
  const input = h("input", { type: "password", placeholder: t("API token"), autocomplete: "current-password", required: true });
  const err = h("div", { class: "error", hidden: true });
  const form = h("form", {
    onsubmit: async (ev) => {
      ev.preventDefault();
      token.set(input.value.trim());
      try {
        await api("/api/v1/meta");
        meta(true);
        capabilities(true);
        render();
      } catch (e) {
        token.clear();
        err.hidden = false;
        err.textContent = e instanceof AuthError ? t("That token was not accepted.") : e.message;
      }
    },
  }, input, h("button", { class: "primary", type: "submit" }, t("Unlock")), err);
  requestAnimationFrame(() => input.focus());
  return h("div", { class: "card login" },
    h("h1", {}, "VitaLocal"),
    h("p", { class: "muted" }, t("Enter the API token (VITALOCAL_API_TOKEN from the server's .env). It is remembered in this browser.")),
    form);
}

// --- the gear opens the settings page (language and theme live there) -----------------

function renderSettings() {
  const root = document.getElementById("settings");
  clear(root).append(h("a", { href: "#/settings", class: "btn icon-only", "aria-label": t("Settings"), title: t("Settings") }, icon("settings", { size: 20 })));
}

renderSettings();
window.addEventListener("hashchange", render);
render();
