// Small shared building blocks for views.

import { h, s } from "./dom.js";
import { icon } from "./icons.js";
import { t } from "./i18n.js";

const store = {
  get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* storage unavailable */ } },
};

/**
 * A card. `title` is a string or { title, icon, summary, collapsible, id, open, cls }:
 * collapsible cards are <details> whose open state is remembered per `id` in this browser.
 */
export function card(title, extra, ...body) {
  const o = typeof title === "object" && title !== null && !(title instanceof Node) ? title : { title };
  const head = h("div", { class: "card-head" },
    h("h2", {}, o.icon ? icon(o.icon) : null, o.title,
      o.summary != null ? h("span", { class: "summary" }, o.summary) : null),
    h("div", { class: "head-extra" }, extra, o.collapsible ? icon("chevron-down", { cls: "chev" }) : null));
  if (!o.collapsible) return h("section", { class: ["card", o.cls] }, head, ...body);
  const key = o.id ? `vitalocal.open.${o.id}` : null;
  const saved = key ? store.get(key) : null;
  const el = h("details", { class: ["card", o.cls], open: saved != null ? saved === "1" : !!o.open },
    h("summary", {}, head), ...body);
  if (key) el.addEventListener("toggle", () => store.set(key, el.open ? "1" : "0"));
  // buttons in the head act on their own instead of toggling the card
  head.querySelector(".head-extra").addEventListener("click", (e) => { if (e.target.closest("button, a, input, select")) e.preventDefault(); });
  return el;
}

/** The page head: area icon, title, a muted subtitle, actions on the right. */
export function pageHead({ icon: ic, title, sub, actions = [] }) {
  return h("div", { class: "page-head" },
    h("h1", { class: "page-title" }, ic ? icon(ic, { size: 22 }) : null, title, sub ? h("span", { class: "sub" }, sub) : null),
    h("div", { class: "toolbar" }, ...actions));
}

/** Tabs below the page head: [{ href, label, icon, active, count }]. */
export function subnav(items) {
  return h("nav", { class: "subnav" }, items.map((it) =>
    h("a", { href: it.href, class: it.active ? "active" : null, "aria-current": it.active ? "page" : null },
      it.icon ? icon(it.icon, { size: 16 }) : null, it.label,
      it.count ? h("span", { class: "count" }, it.count) : null)));
}

/** An icon button. */
export const iconBtn = (name, label, onclick, cls = "ghost icon-only") =>
  h("button", { type: "button", class: cls, title: label, "aria-label": label, onclick }, icon(name));

/**
 * ⋯ menu: items are { label, icon, onclick, href, danger } or "-" (separator) or { heading }.
 * `trigger` replaces the default dots button.
 */
export function menu(items, { trigger, label = t("More"), left = false } = {}) {
  const panel = h("div", { class: ["menu", left && "left"], role: "menu", hidden: true });
  const close = () => { panel.hidden = true; btn.setAttribute("aria-expanded", "false"); document.removeEventListener("click", outside, true); };
  const outside = (e) => { if (!wrap.contains(e.target)) close(); };
  for (const it of items.filter(Boolean)) {
    if (it === "-") { panel.append(h("hr")); continue; }
    if (it.heading) { panel.append(h("div", { class: "menu-label" }, it.heading)); continue; }
    const content = [it.icon ? icon(it.icon, { size: 16 }) : null, it.label];
    panel.append(it.href
      ? h("a", { href: it.href, role: "menuitem", class: it.danger ? "danger" : null, onclick: close }, ...content)
      : h("button", { type: "button", role: "menuitem", class: it.danger ? "danger" : null,
        onclick: (e) => { close(); it.onclick?.(e); } }, ...content));
  }
  const btn = trigger || iconBtn("dots", label);
  btn.setAttribute("aria-haspopup", "menu");
  btn.setAttribute("aria-expanded", "false");
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (!panel.hidden) return close();
    panel.hidden = false; btn.setAttribute("aria-expanded", "true");
    document.addEventListener("click", outside, true);
  });
  panel.addEventListener("keydown", (e) => { if (e.key === "Escape") { close(); btn.focus(); } });
  const wrap = h("div", { class: "menu-wrap" }, btn, panel);
  return wrap;
}

/**
 * A modal sheet (native <dialog>): opens at once, removes itself when closed.
 * Returns { el, body, close }. `foot` holds the action buttons.
 */
export function sheet({ title, icon: ic, wide = false, body = [], foot = null, onClose } = {}) {
  const content = h("div", { class: "sheet-body" }, body);
  const dlg = h("dialog", { class: ["sheet", wide && "wide"] },
    h("div", { class: "sheet-head" }, h("h2", {}, ic ? icon(ic) : null, title),
      iconBtn("x", t("Close"), () => dlg.close())),
    content,
    foot ? h("div", { class: "sheet-foot" }, foot) : null);
  dlg.addEventListener("close", () => { dlg.remove(); onClose?.(); });
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
  document.body.append(dlg);
  dlg.showModal();
  return { el: dlg, body: content, close: () => dlg.close() };
}

/** Ask for a short text in a sheet (replaces prompt()); resolves to the text or null. */
export function ask({ title, icon: ic, label, placeholder = "", confirm = t("Save"), value = "" }) {
  return new Promise((resolve) => {
    let result = null;
    const input = h("input", { type: "text", value, placeholder, class: "wide", maxlength: 500 });
    const form = h("form", { class: "form", onsubmit: (e) => { e.preventDefault(); result = input.value.trim(); sh.close(); } },
      h("label", { class: "form-row" }, label, input));
    const sh = sheet({ title, icon: ic, body: [form], onClose: () => resolve(result),
      foot: [h("button", { type: "button", onclick: () => sh.close() }, t("Cancel")),
        h("button", { type: "button", class: "primary", onclick: () => form.requestSubmit() }, confirm)] });
    requestAnimationFrame(() => input.focus());
  });
}

/** A short message at the bottom; `action` = [label, fn] adds a button (e.g. undo). */
export function toast(message, { action, timeout = 5000, icon: ic } = {}) {
  let host = document.querySelector(".toasts");
  if (!host) { host = h("div", { class: "toasts", role: "status", "aria-live": "polite" }); document.body.append(host); }
  const el = h("div", { class: "toast" }, ic ? icon(ic, { size: 16 }) : null, h("span", {}, message),
    action ? h("button", { type: "button", onclick: () => { action[1](); leave(); } }, action[0]) : null);
  const leave = () => { el.classList.add("leaving"); setTimeout(() => el.remove(), 200); };
  host.append(el);
  setTimeout(leave, timeout);
  return leave;
}

/** A small line chart of `values` (nulls are gaps) filling its box. */
export function sparkline(values, { width = 120, height = 28 } = {}) {
  const v = values.map((x) => (x == null || Number.isNaN(x) ? null : x));
  const nums = v.filter((x) => x != null);
  if (nums.length < 2) return null;
  const lo = Math.min(...nums), hi = Math.max(...nums), span = hi - lo || 1;
  const x = (i) => (i / (v.length - 1)) * width, y = (n) => height - 2 - ((n - lo) / span) * (height - 4);
  let d = "", pen = false, first = null, last = null;
  v.forEach((n, i) => {
    if (n == null) { pen = false; return; }
    d += `${pen ? "L" : "M"}${x(i).toFixed(1)} ${y(n).toFixed(1)}`;
    pen = true; first ??= i; last = i;
  });
  return s("svg", { class: "spark", viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: "none", "aria-hidden": "true" },
    s("path", { class: "fill", d: `${d}L${x(last)} ${height}L${x(first)} ${height}Z` }), s("path", { d }));
}

/**
 * Stat tile: { label, icon, value, unit, delta, good (true/false/null colours the delta), spark,
 * onclick, active }.
 */
export function statTile({ label, icon: ic, value, unit, delta, good = null, spark, onclick, active, flat = false }) {
  const body = [
    h("div", { class: "tile-label" }, ic ? icon(ic, { size: 14 }) : null, label),
    h("div", { class: "tile-value" }, value ?? "–", unit && value != null ? h("small", {}, unit) : null),
    delta ? h("div", { class: ["tile-delta", good === true && "good", good === false && "bad"] }, delta) : null,
    spark ? sparkline(spark) : null,
  ];
  return onclick
    ? h("button", { type: "button", class: ["tile", flat && "flat", active && "active"], onclick }, body)
    : h("div", { class: ["tile", flat && "flat"] }, body);
}

/** Empty state: an invitation, not an apology. */
export const emptyState = ({ icon: ic = "circle-plus", title, text, action }) =>
  h("div", { class: "empty" }, icon(ic, { size: 28 }), title ? h("b", {}, title) : null, text ? h("span", { class: "small" }, text) : null, action);

export function legend(items) {
  return h("div", { class: "legend" }, items.map(([label, color]) =>
    h("span", {}, h("i", { class: "swatch", style: { background: color } }), label)));
}

export const stat = (label, value) => h("span", {}, h("span", { class: "muted" }, `${label} `), h("b", {}, value));

/** Longest run of consecutive ISO days ending at `end` (or the day before) that satisfy `has`. */
export function streak(end, has, addDays) {
  let d = has(end) ? end : addDays(end, -1);
  let n = 0;
  while (has(d)) { n++; d = addDays(d, -1); }
  return n;
}

/** "Today", "Yesterday", "Day before yesterday", else a short date with weekday. */
export function relativeDay(day, today, addDays, t, mediumDate) {
  if (day === today) return t("Today");
  if (day === addDays(today, -1)) return t("Yesterday");
  if (day === addDays(today, -2)) return t("Day before yesterday");
  return mediumDate(day);
}

/**
 * ← [label] → day switcher. The label names the day relative to today and opens the native date
 * picker; `go(day)` navigates.
 */
export function dayNav({ day, today, min, go, addDays, mediumDate, extra = [] }) {
  const input = h("input", { type: "date", value: day, min, max: today, tabindex: -1, "aria-hidden": "true",
    onchange: (e) => e.target.value && go(e.target.value) });
  const open = () => { try { input.showPicker(); } catch { input.focus(); input.click(); } };
  const prev = addDays(day, -1), next = addDays(day, 1);
  return h("div", { class: "toolbar" },
    h("button", { disabled: !!min && prev < min, onclick: () => go(prev), "aria-label": t("Previous day"), class: "icon-only" }, icon("chevron-left")),
    h("span", { class: "date-pick" },
      h("button", { class: "date-btn", onclick: open, title: t("Pick a date") }, relativeDay(day, today, addDays, t, mediumDate)),
      input),
    h("button", { disabled: next > today, onclick: () => go(next), "aria-label": t("Next day"), class: "icon-only" }, icon("chevron-right")),
    day !== today ? h("button", { onclick: () => go(today) }, t("Today")) : null,
    ...extra);
}

/**
 * Saves only the latest value: call the returned function on every change; after a short pause the
 * newest value is sent with `send(v)`, one write at a time, so fast clicks never race or jump back.
 * `onSaved(v)` runs once the newest value is stored.
 */
export function latestWriter(send, { delay = 350, onSaved, onError } = {}) {
  let timer = null, latest, chain = Promise.resolve();
  return (value) => {
    latest = value;
    clearTimeout(timer);
    timer = setTimeout(() => {
      const v = latest;
      chain = chain.then(() => send(v)).then(() => { if (v === latest) onSaved?.(v); }, (e) => onError?.(e));
    }, delay);
  };
}
