// Body map: front and back outline (MuscleMap by Melih Colpan, MIT – /body-paths.json) with each
// muscle filled by the caller. Shapes without a muscle (head, hands, feet …) are the silhouette.

import { h, s } from "./dom.js";
import { t } from "./i18n.js";

const FIGURE_KEY = "vitalocal.bodyFigure";
let pathsPromise = null;
const bodyPaths = () => (pathsPromise ??= fetch("/body-paths.json").then((r) => {
  if (!r.ok) throw new Error(`body-paths.json: ${r.status}`);
  return r.json();
}).catch((e) => { pathsPromise = null; throw e; }));

/** "male" or "female", per browser. */
export function bodyFigure() {
  try { return localStorage.getItem(FIGURE_KEY) === "female" ? "female" : "male"; } catch { return "male"; }
}

export function setBodyFigure(v) {
  try { localStorage.setItem(FIGURE_KEY, v); } catch { /* ignore */ }
}

/**
 * fill(muscle) → a CSS colour (or { color, opacity }) or null for the plain muscle colour;
 * title(muscle) → tooltip; onPick(muscle) makes muscles clickable; `selected` is outlined.
 * `size`: "md" (default) or "sm" for the small maps next to exercises and plan days.
 */
export function bodyMap({ fill = () => null, title = null, onPick = null, selected = null, size = "md", figure = bodyFigure() } = {}) {
  const box = h("div", { class: ["body-map", `body-map-${size}`, onPick && "pickable"] });
  bodyPaths().then((data) => {
    const fig = data.figures[figure] || data.figures.male;
    box.replaceChildren(...["front", "back"].map((side) => {
      const v = fig[side];
      return s("svg", { viewBox: v.viewBox.join(" "), role: "img", "aria-label": side === "front" ? t("front view") : t("back view") },
        v.shapes.map((sh) => {
          const m = sh.muscle;
          const f = m ? fill(m) : null;
          const color = typeof f === "string" ? f : f?.color;
          const g = s("g", {
            class: ["shape", m ? "muscle" : "skin", m && m === selected && "selected"],
            "data-muscle": m || null,
            style: color ? { fill: color, fillOpacity: f?.opacity ?? 1 } : null,
          }, sh.d.map((d) => s("path", { d })), m && title ? s("title", {}, title(m)) : null);
          if (m && onPick) g.addEventListener("click", () => onPick(m));
          return g;
        }));
    }));
  }, (e) => box.replaceChildren(h("div", { class: "error small" }, e.message)));
  return box;
}

/** Hard sets per muscle of sets ({exercise_id, repetitions, duration_s, warmup, hidden}): primary 1,
 * secondary ½ – the same counting as the server's stats. */
export function hardSetsOf(sets, exercises) {
  const byId = new Map(exercises.map((e) => [e.id, e]));
  const out = {};
  for (const x of sets) {
    const ex = byId.get(x.exercise_id);
    if (!ex || x.warmup || x.hidden) continue;
    if (!(x.repetitions || (ex.kind === "time" && x.duration_s))) continue;
    for (const m of ex.muscles || []) out[m] = (out[m] || 0) + 1;
    for (const m of ex.muscles_secondary || []) out[m] = (out[m] || 0) + 0.5;
  }
  return out;
}

/** Muscles ({key, label, group, group_label}, in display order) by group: [{key, label, items}]. */
export function byGroup(muscles) {
  const out = [];
  for (const m of muscles) {
    let g = out.at(-1);
    if (!g || g.key !== m.group) out.push(g = { key: m.group, label: m.group_label || m.group, items: [] });
    g.items.push(m);
  }
  return out;
}

/** Fill for a value map ({muscle: number}): the accent, stronger the larger the value. */
export const scaleFill = (values, color = "var(--cat-train)") => {
  const max = Math.max(0, ...Object.values(values));
  return (m) => (values[m] > 0 && max > 0 ? { color, opacity: 0.25 + 0.75 * (values[m] / max) } : null);
};
