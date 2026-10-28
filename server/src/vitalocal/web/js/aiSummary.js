// AI summary card: day or week summary from the configured local model.

import { api } from "./api.js";
import { h } from "./dom.js";
import * as f from "./format.js";
import { lang, t } from "./i18n.js";
import { renderMarkdown } from "./markdown.js";
import { card } from "./ui.js";

export function aiCard(day) {
  let scope = "day";
  const body = h("div", { class: "ai-body" });
  const status = h("span", { class: "small muted" });
  const genBtn = h("button", { class: "primary" }, t("Summarize"));
  const scopeSel = h("div", { class: "segmented" });

  const drawScope = () => scopeSel.replaceChildren(...[["day", t("Day")], ["week", t("Week")]].map(([s, label]) =>
    h("button", { class: s === scope && "active", onclick: () => { scope = s; drawScope(); load(); } }, label)));

  const show = (summary) => {
    if (!summary) { body.replaceChildren(h("div", { class: "muted small" }, t("No summary yet."))); return; }
    body.replaceChildren(
      renderMarkdown(summary.text),
      h("div", { class: "muted small ai-meta" },
        t("{model} · {when} · {secs} s", { model: summary.model, when: f.dateTime(summary.created_at), secs: f.num((summary.duration_ms || 0) / 1000) }),
        " · ", t("generated locally; may contain errors, not medical advice")),
      h("details", { class: "ai-facts" }, h("summary", { class: "small muted" }, t("Data basis")),
        h("pre", {}, JSON.stringify(summary.context, null, 1))));
  };

  async function load() {
    status.textContent = "";
    body.replaceChildren(h("div", { class: "muted small" }, "…"));
    try {
      const r = await api("/api/v1/ai/summary", { params: { day, scope, lang: lang() } });
      if (!r.enabled) {
        genBtn.disabled = true;
        body.replaceChildren(h("div", { class: "muted small" }, t("AI summaries are not set up yet. "),
          h("a", { href: "#/settings/ai" }, t("Configure a local model"))));
        return;
      }
      genBtn.disabled = false;
      genBtn.textContent = r.summary ? t("Regenerate") : t("Summarize");
      show(r.summary);
    } catch (e) {
      body.replaceChildren(h("div", { class: "error" }, e.message));
    }
  }

  genBtn.addEventListener("click", async () => {
    genBtn.disabled = true;
    const started = Date.now();
    const tick = setInterval(() => { status.textContent = t("Model is writing … {s} s", { s: Math.round((Date.now() - started) / 1000) }); }, 500);
    try {
      const summary = await api("/api/v1/ai/summary", { method: "POST", json: { day, scope, lang: lang() } });
      status.textContent = "";
      genBtn.textContent = t("Regenerate");
      show(summary);
    } catch (e) {
      status.textContent = t("Not generated: {msg}", { msg: e.message });
    } finally {
      clearInterval(tick);
      genBtn.disabled = false;
    }
  });

  drawScope();
  load();
  return card({ title: t("AI summary"), icon: "sparkles" }, h("div", { class: "toolbar" }, status, scopeSel, genBtn), body);
}
