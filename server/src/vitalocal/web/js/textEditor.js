// Autosaving text editor for the day journal and the weekly/monthly reviews.
// Writes carry the version they are based on; when someone else (an agent, another browser, the app)
// changed the text meanwhile, the server answers 409 and the editor lets the user choose.

import { ApiError, api } from "./api.js";
import { h } from "./dom.js";
import * as f from "./format.js";
import { t } from "./i18n.js";

const SOURCE = "webui";
const IDLE_MS = 1200;

/**
 * `url`: GET/PUT endpoint of the text; `state`: its current {text, version, updated_at, source};
 * `revisions`: {scope, day} for the history list. Returns {node, status}.
 */
export function textEditor({ url, state, revisions, placeholder, rows = 8, onSaved }) {
  const status = h("span", { class: "small muted" }, savedLabel(state));
  const area = h("textarea", { rows, placeholder, class: "journal-text" });
  const conflictBox = h("div");
  area.value = state.text;
  let version = state.version;
  let lastSaved = state.text;
  let timer = null;
  let inConflict = false;

  const persist = async (force = false) => {
    clearTimeout(timer);
    if (inConflict || (!force && area.value === lastSaved)) return;
    const text = area.value;
    status.textContent = t("Saving …");
    try {
      const r = await api(url, { method: "PUT", json: { text, version, source: SOURCE } });
      version = r.version;
      lastSaved = text;
      status.textContent = r.text ? t("Saved {time}", { time: f.time(r.updated_at) }) : t("Empty entry removed");
      onSaved?.(r);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) showConflict(e.detail.current, text);
      else status.textContent = t("Not saved: {msg}", { msg: e.message });
    }
  };

  function showConflict(current, mine) {
    inConflict = true;
    status.textContent = t("Not saved: changed elsewhere");
    const resolve = (text, ver, save) => {
      inConflict = false;
      conflictBox.replaceChildren();
      area.value = text;
      version = ver;
      if (save) persist(true);
      else { lastSaved = text; status.textContent = savedLabel(current); }
    };
    conflictBox.replaceChildren(h("div", { class: "notice warn conflict" },
      h("div", {}, t("This text was changed elsewhere ({source}, {time}) while you were editing.", {
        source: sourceLabel(current.source), time: current.updated_at ? f.dateTime(current.updated_at) : "–" })),
      h("details", {}, h("summary", { class: "small" }, t("Show the other version")),
        h("pre", { class: "conflict-text" }, current.text || t("(empty)"))),
      h("div", { class: "toolbar", style: { marginTop: "8px" } },
        h("button", { class: "primary", onclick: () => resolve(mine, current.version, true) }, t("Keep mine")),
        h("button", { onclick: () => resolve(current.text, current.version, false) }, t("Take theirs")),
        h("button", { onclick: () => resolve(joinTexts(current.text, mine), current.version, true) }, t("Combine both")))));
  }

  area.addEventListener("input", () => {
    if (inConflict) return;
    status.textContent = t("Unsaved changes");
    clearTimeout(timer);
    timer = setTimeout(persist, IDLE_MS);
  });
  area.addEventListener("blur", () => persist());

  const node = h("div", { class: "text-editor" }, conflictBox, area,
    revisions ? revisionList(revisions, (text) => { area.value = text; persist(); }) : null);
  return { node, status };
}

function joinTexts(a, b) {
  if (!a.trim()) return b;
  if (!b.trim() || a.includes(b)) return a;
  return `${a.trimEnd()}\n\n---\n\n${b.trimStart()}`;
}

export function sourceLabel(source) {
  return { webui: t("WebUI"), android: t("Android app"), agent: t("Agent"), ai: t("AI"), api: "API" }[source] || source || "–";
}

function savedLabel(state) {
  if (!state.updated_at || !state.text) return "";
  const who = state.source && state.source !== SOURCE ? ` · ${sourceLabel(state.source)}` : "";
  return `${t("Saved {time}", { time: f.dateTime(state.updated_at) })}${who}`;
}

/** Earlier versions, loaded when opened. */
function revisionList({ scope, day }, restore) {
  const list = h("div", { class: "small muted" }, t("Loading …"));
  const details = h("details", { class: "revisions" }, h("summary", { class: "small muted" }, t("Earlier versions")), list);
  details.addEventListener("toggle", async () => {
    if (!details.open) return;
    try {
      const revs = await api("/api/v1/journal/revisions", { params: { scope, day } });
      list.replaceChildren(revs.length ? h("ul", { class: "revision-list" }, revs.map((r) => h("li", {},
        h("div", { class: "small muted" }, `${f.dateTime(r.written_at)} · ${sourceLabel(r.source)} · v${r.version}`),
        h("div", { class: "revision-text" }, r.text.length > 300 ? `${r.text.slice(0, 300)} …` : r.text),
        h("button", { class: "link-btn", onclick: () => { restore(r.text); details.open = false; } }, t("Restore")))))
        : h("div", { class: "small muted" }, t("No earlier versions.")));
    } catch (e) {
      list.replaceChildren(h("div", { class: "error" }, e.message));
    }
  });
  return details;
}
