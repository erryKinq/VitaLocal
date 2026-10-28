// First run: a few steps to make VitaLocal fit the person (language, profile, data sources, goal,
// AI, areas). Shown until it was finished or skipped once; everything can be changed later in the
// settings.

import { api, capabilities } from "../api.js";
import { h } from "../dom.js";
import { lang, languages, setLang, t } from "../i18n.js";
import { icon } from "../icons.js";
import { card } from "../ui.js";
import { energyFields, estimateBox, moduleFields, profileFields, saveWeight } from "./profile.js";

const STEPS = ["Welcome", "About you", "Data sources", "Goal", "AI", "Areas", "Done"];

const row = (label, input, hint) => h("label", { class: "form-row" }, h("span", {}, label), input, hint ? h("small", { class: "muted" }, hint) : null);

export async function setupView(ctx) {
  let p = await api("/api/v1/profile");
  const mods = await api("/api/v1/modules");
  const caps = await capabilities();
  let step = 0;
  const root = h("div", { class: "setup" });

  const pf = profileFields(p, p.estimate?.weight_kg);
  const ef = energyFields(p);
  const mf = moduleFields(mods);
  // data sources decide where the burn comes from and whether watch tiles show
  const source = h("div", { class: "stack-sm" }, [
    ["watch", t("A watch via Gadgetbridge (Garmin, Amazfit, Huami, …)")],
    ["health_connect", t("Health Connect on Android (Samsung Health, Fitbit, Google Fit, …)")],
    ["none", t("No watch: I log food, weight and training myself")],
  ].map(([v, label]) => h("label", { class: "form-check" },
    h("input", { type: "radio", name: "setup-source", value: v, checked: v === (caps.has_watch_data ? "watch" : "none") }), ` ${label}`)));
  const pickedSource = () => source.querySelector("input:checked")?.value || "none";

  const finish = async (skip = false) => {
    if (!skip) {
      await saveWeight(pf.weightKg(), p.estimate?.weight_kg);
      await api("/api/v1/modules", { method: "PUT", json: mf.read() });
    }
    await api("/api/v1/profile", { method: "PUT", json: { ...(skip ? {} : { ...pf.read(), ...ef.read() }), lang: lang(), setup_done: true } });
    await ctx.reloadCaps();
    ctx.navigate("#/");
  };

  /** Saves what was entered so far, so the next step (goal) can show the estimate. */
  const saveProfile = async () => {
    await saveWeight(pf.weightKg(), p.estimate?.weight_kg);
    p = await api("/api/v1/profile", { method: "PUT", json: { ...pf.read(), ...ef.read(), lang: lang() } });
  };

  const body = () => {
    switch (STEPS[step]) {
      case "Welcome": {
        const langSel = h("select", { onchange: (e) => { setLang(e.target.value); draw(); } },
          languages.map(([k, l]) => h("option", { value: k, selected: k === lang() }, l)));
        return [
          h("p", {}, t("VitaLocal keeps your health data on your own server. A few questions make it fit you; everything can be changed later in the settings.")),
          h("div", { class: "form" }, row(t("Language"), langSel), row(t("Units"), pf.units)),
        ];
      }
      case "About you":
        return [
          h("p", { class: "muted small" }, t("Used to estimate how much energy you burn when no watch measures it.")),
          h("div", { class: "form" }, row(t("Sex"), pf.sex), row(t("Birth year"), pf.birth), row(t("Body height"), pf.height),
            row(t("Weight"), pf.weight), row(t("Everyday activity (without training)"), pf.pal)),
        ];
      case "Data sources":
        return [
          h("p", {}, t("Where do your measurements come from?")), source,
          h("p", { class: "muted small" }, t("Without a watch, the daily burn is estimated from your profile; on training days the workout is added (from duration, type and how hard it was).")),
          h("div", { class: "form" }, row(t("Energy burn"), ef.source), ef.tdeeRow),
        ];
      case "Goal":
        return [h("div", { class: "form" }, row(t("Goal"), pf.goal)), estimateBox(p)];
      case "AI":
        return [
          h("p", {}, t("VitaLocal can use an AI model for day summaries, a training coach and reading lab reports. This is optional: without it these features are hidden.")),
          caps.ai.configured
            ? h("div", { class: "notice" }, icon("check"), ` ${t("An AI model is set up.")}`)
            : h("p", { class: "muted small" }, t("You can set up a model in your network (e.g. Ollama) or a hosted provider later under Settings → AI.")),
          h("a", { href: "#/settings/ai" }, t("Open the AI settings")),
        ];
      case "Areas":
        return [h("p", { class: "muted small" }, t("Choose what you want to see. Hidden areas keep their data.")), mf.node];
      default:
        return [
          h("p", {}, t("All set.")),
          h("div", { class: "notice warn" }, h("b", {}, t("Not medical advice.")), " ",
            t("VitaLocal shows and estimates values from your data. Estimates, AI texts and lab status can be wrong; talk to a doctor about health questions.")),
        ];
    }
  };

  // the suggested goal depends on it: recompute on the server
  pf.goal.addEventListener("change", () => saveProfile().then(draw).catch((e) => alert(e.message)));

  const next = async () => {
    const name = STEPS[step];
    if (name === "About you" || name === "Data sources") await saveProfile();
    if (name === "Data sources") {
      mf.set("watch", pickedSource() !== "none");
    }
    if (step === STEPS.length - 1) return finish();
    step += 1;
    draw();
  };

  const draw = () => {
    root.replaceChildren(card({ title: `${t("Setup")} · ${t(STEPS[step])}`, icon: "sparkles" },
      h("span", { class: "muted small" }, `${step + 1} / ${STEPS.length}`),
      ...body(),
      h("div", { class: "toolbar", style: { marginTop: "16px" } },
        step > 0 ? h("button", { type: "button", onclick: () => { step -= 1; draw(); } }, t("Back")) : null,
        h("button", { type: "button", class: "primary", onclick: () => next().catch((e) => alert(e.message)) },
          step === STEPS.length - 1 ? t("Finish") : t("Next")),
        h("span", { style: { flex: 1 } }),
        step < STEPS.length - 1 ? h("button", { type: "button", class: "ghost", onclick: () => finish(true) }, t("Skip setup")) : null)));
  };
  draw();
  return root;
}
