import { api, token } from "../api.js";
import { h } from "../dom.js";
import { lang, languages, setLang, t } from "../i18n.js";
import { icon } from "../icons.js";
import { applyTheme, currentTheme } from "../prefs.js";
import { card, pageHead } from "../ui.js";
import { appCard, dataSection } from "./data.js";
import { profileSection } from "./profile.js";

const SECTIONS = [
  ["appearance", "Appearance", "palette"],
  ["profile", "Profile & areas", "user"],
  ["ai", "AI (optional)", "robot"],
  ["app", "Android app", "device-mobile"],
  ["data", "Data & import", "database"],
];

/** Settings: one page with a section list; the gear in the top bar leads here. */
export async function settingsView(ctx, section = "appearance") {
  const content = section === "ai" ? await aiSection(ctx)
    : section === "profile" ? await profileSection(ctx)
    : section === "app" ? appCard()
    : section === "data" ? await dataSection(ctx)
    : appearanceSection(ctx);
  return h("div", {},
    pageHead({ icon: "settings", title: t("Settings") }),
    h("div", { class: "settings-layout" },
      h("nav", { class: "settings-nav", "aria-label": t("Settings") },
        SECTIONS.map(([k, label, ic]) => h("a", { href: `#/settings/${k}`, class: k === section ? "active" : null,
          "aria-current": k === section ? "page" : null }, icon(ic), t(label))),
        h("hr"),
        h("button", { type: "button", onclick: () => { token.clear(); location.hash = "#/"; ctx.rerender(); } }, icon("logout"), t("Log out"))),
      h("div", { class: "stack" }, content)));
}

function appearanceSection(ctx) {
  const seg = (options, value, onpick) => {
    const el = h("div", { class: "segmented", role: "radiogroup" });
    const draw = (v) => el.replaceChildren(...options.map(([k, label]) =>
      h("button", { type: "button", role: "radio", "aria-checked": String(k === v), class: k === v ? "active" : null,
        onclick: () => { onpick(k); draw(k); } }, label)));
    draw(value);
    return el;
  };
  return card({ title: t("Appearance"), icon: "palette" }, null,
    h("div", { class: "pref-row" }, h("div", { class: "grow" }, h("b", {}, t("Language"))),
      // the server's texts (meal and exercise names, labels) follow the language too
      seg(languages, lang(), (v) => { setLang(v); api("/api/v1/profile", { method: "PUT", json: { lang: v } }).catch(() => {}); ctx.rerender(); })),
    h("div", { class: "pref-row" }, h("div", { class: "grow" }, h("b", {}, t("Theme")),
      h("div", { class: "small muted" }, t("System follows the light or dark mode of this device."))),
      seg([["system", t("System")], ["light", t("Light")], ["dark", t("Dark")]], currentTheme(), applyTheme)));
}

// [name, base URL, external?]; local ones need the host filled in
const PRESETS = [
  ["Ollama", "http://<host>:11434/v1", false],
  ["LM Studio", "http://<host>:1234/v1", false],
  ["llama.cpp server", "http://<host>:8080/v1", false],
  ["OpenAI", "https://api.openai.com/v1", true],
  ["Anthropic", "https://api.anthropic.com/v1", true],
  ["Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai", true],
  ["Mistral", "https://api.mistral.ai/v1", true],
  ["OpenRouter", "https://openrouter.ai/api/v1", true],
];
// what an external model may be used for / see (local models get everything)
const SHARE = [
  ["summary", "Day and week summaries", "Health, sleep, training and nutrition values of the day or week."],
  ["coach", "AI coach", "Your training plan, workouts with sets and weights, questionnaire answers."],
  ["journal", "Journal text and notes", "What you write in the journal and check-in notes, inside summaries."],
  ["labs", "Lab reports", "Pages of uploaded lab reports as images, and lab values inside summaries."],
];

/** Mirrors the server's check: a host outside the local network. */
function isExternal(url) {
  let host;
  try { host = new URL(url).hostname.toLowerCase(); } catch { return false; }
  if (!host || host === "localhost" || !host.includes(".")) return false;
  if ([".local", ".lan", ".arpa", ".home", ".internal", ".localhost", ".localdomain", ".test"].some((s) => host.endsWith(s))) return false;
  const ip = host.match(/^(\d+)\.(\d+)\.\d+\.\d+$/);
  if (ip) {
    const [a, b] = [Number(ip[1]), Number(ip[2])];
    return !(a === 10 || a === 127 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) || (a === 169 && b === 254));
  }
  return !host.startsWith("[");
}

async function aiSection(ctx) {
  const s = await api("/api/v1/settings/ai");
  const enabled = h("input", { type: "checkbox", checked: s.enabled });
  const vision = h("input", { type: "checkbox", checked: s.vision });
  const share = Object.fromEntries(SHARE.map(([k]) => [k, h("input", { type: "checkbox", checked: !!s.share?.[k] })]));
  const privacy = h("div", { class: "notice warn privacy-notice", hidden: true },
    h("div", {}, h("b", {}, t("External AI provider")), " ",
      t("This address is outside your network. Whatever you allow below is sent to this provider and handled under its privacy terms. Journal text and lab reports stay at home unless you allow them.")),
    h("div", { class: "stack-sm", style: { marginTop: "8px" } }, SHARE.map(([k, label, what]) =>
      h("label", { class: "form-check" }, share[k], " ", h("span", {}, h("b", {}, t(label)), h("span", { class: "muted small" }, ` – ${t(what)}`))))));
  const baseUrl = h("input", { type: "text", value: s.base_url, placeholder: "http://localhost:11434/v1", class: "wide" });
  const syncPrivacy = () => { privacy.hidden = !isExternal(baseUrl.value.trim()); };
  baseUrl.addEventListener("input", syncPrivacy);
  syncPrivacy();
  const presets = h("div", { class: "toolbar small" }, PRESETS.map(([n, u, ext]) => h("button", { type: "button", class: "ghost",
    title: u, onclick: () => { baseUrl.value = u; syncPrivacy(); baseUrl.focus(); } }, ext ? `${n} ↗` : n)));
  const model = h("input", { type: "text", value: s.model, list: "ai-models", placeholder: t("e.g. qwen3:14b, gpt-5-mini, mistral-small-latest"), class: "wide" });
  const models = h("datalist", { id: "ai-models" });
  const apiKey = h("input", { type: "password", placeholder: s.has_api_key ? t("stored – leave empty to keep") : t("optional"), autocomplete: "off", class: "wide" });
  const clearKey = h("input", { type: "checkbox" });
  const temperature = h("input", { type: "number", min: 0, max: 2, step: 0.1, value: s.temperature, class: "num-input" });
  const timeout = h("input", { type: "number", min: 5, max: 900, step: 5, value: s.timeout_s, class: "num-input" });
  const focus = h("textarea", { rows: 2, maxlength: 500, class: "journal-text", placeholder: t("e.g. muscle gain, sleep better, lose a little fat") });
  focus.value = s.focus || "";
  const testOut = h("div", { class: "small" });
  const saveOut = h("span", { class: "small muted" });

  const test = async () => {
    testOut.className = "small muted";
    testOut.textContent = t("Connecting …");
    const r = await api("/api/v1/ai/models", { method: "POST", json: { base_url: baseUrl.value.trim(), api_key: apiKey.value || null } });
    if (r.ok) {
      models.replaceChildren(...r.models.map((m) => h("option", { value: m })));
      testOut.className = "small";
      testOut.replaceChildren(h("span", { class: "badge ok" }, t("connected")), " ",
        t("{n} models available: {list}", { n: r.models.length, list: r.models.join(", ") || "–" }));
    } else {
      testOut.className = "small";
      testOut.replaceChildren(h("span", { class: "badge warn" }, t("not reachable")), ` ${r.error}`);
    }
  };

  const save = async (e) => {
    e.preventDefault();
    saveOut.textContent = t("Saving …");
    try {
      await api("/api/v1/settings/ai", { method: "PUT", json: {
        enabled: enabled.checked, base_url: baseUrl.value.trim(), model: model.value.trim(),
        api_key: clearKey.checked ? "" : apiKey.value ? apiKey.value : null,
        temperature: Number(temperature.value), timeout_s: Number(timeout.value), focus: focus.value.trim(),
        vision: vision.checked, share: Object.fromEntries(Object.entries(share).map(([k, box]) => [k, box.checked])),
      } });
      apiKey.value = "";
      saveOut.textContent = t("Saved");
      await ctx.reloadCaps();
    } catch (err) {
      saveOut.textContent = t("Not saved: {msg}", { msg: err.message });
    }
  };

  const row = (label, input, hint) => h("label", { class: "form-row" }, h("span", {}, label), input, hint ? h("small", { class: "muted" }, hint) : null);

  return card({ title: t("AI model"), icon: "robot" }, null,
      h("p", { class: "muted small" }, t("Optional. Without an AI model VitaLocal works fully; AI features (summaries, coach, reading lab reports) are then hidden.")),
      h("p", { class: "muted small" }, t("VitaLocal talks to any OpenAI-compatible endpoint: a model in your network (Ollama, LM Studio, llama.cpp, vLLM) or a hosted provider. The server calls the model; your data only goes to the address configured here.")),
      h("form", { class: "form", onsubmit: save },
        h("label", { class: "form-check" }, enabled, ` ${t("Use AI features")}`),
        row(t("Endpoint (base URL)"), baseUrl), presets,
        privacy,
        h("div", {}, h("button", { type: "button", onclick: test }, t("Test connection")), testOut),
        row(t("Model"), model, t("Test the connection to pick from the installed models.")), models,
        h("label", { class: "form-check" }, vision, ` ${t("The model can read images (needed to import lab reports)")}`),
        row(t("API key"), apiKey, t("Needed for hosted providers; usually not for local servers. Stored encrypted.")),
        s.has_api_key ? h("label", { class: "form-check small" }, clearKey, ` ${t("remove stored key")}`) : null,
        row(t("Personal focus"), focus, t("Optional. The summary weights its observations towards this.")),
        h("div", { class: "toolbar" }, row(t("Temperature"), temperature), row(t("Timeout (s)"), timeout)),
        h("div", { class: "toolbar" }, h("button", { class: "primary", type: "submit" }, t("Save")), saveOut)));
}
