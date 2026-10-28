// Lab results on the health page: table per analyte (latest, lab reference, status, previous, trend),
// manual entry with catalog autocomplete, and the review of a report read by the local model.
// No interpretation here: status is the comparison with the printed reference range.

import { aiOn, api, token } from "../api.js";
import { labChart } from "../charts.js";
import { uuid4 } from "../checkin.js";
import { h, responsive } from "../dom.js";
import * as f from "../format.js";
import { lang, t } from "../i18n.js";
import { icon } from "../icons.js";
import { card, menu } from "../ui.js";

const STATUS = { low: ["warn", "↓ low"], high: ["warn", "↑ high"], normal: ["ok", "in range"] };
const loc = (s) => (lang() === "de" ? String(s).replace(".", ",") : String(s));
const decimals = (v) => (v == null ? 0 : Math.abs(v) >= 100 ? 0 : Math.abs(v) >= 10 ? 1 : 2);
const fmtNum = (v) => (v == null ? "–" : f.num(v, Math.min(decimals(v), (String(v).split(".")[1] || "").length)));
const sameUnit = (a, b) => (a || "").toLowerCase().replace("μ", "µ") === (b || "").toLowerCase().replace("μ", "µ");
/** Printed in the display unit or an equivalent one (ng/ml = µg/l): show the printed value and range. */
const asPrinted = (p, unit) => sameUnit(p.unit, unit) || p.value == null || p.y == null || Math.abs(p.y - p.value) < 1e-9 * Math.max(1, Math.abs(p.value));
/** The lab reference in the display unit (converted when printed in another unit). */
function refText(p, unit) {
  if (!p.reference) return "–";
  if (asPrinted(p, unit)) return loc(p.reference);
  const lo = p.y_ref_low, hi = p.y_ref_high;
  if (lo != null && hi != null) return `${fmtNum(lo)} – ${fmtNum(hi)}`;
  if (hi != null) return `< ${fmtNum(hi)}`;
  if (lo != null) return `> ${fmtNum(lo)}`;
  return loc(p.reference);
}

export function statusBadge(status, title = "") {
  if (!status) return h("span", { class: "badge", title: t("cannot be judged against the reference") }, "–");
  const [cls, label] = STATUS[status];
  return h("span", { class: ["badge", cls], title }, t(label));
}

/** A point's value in the row's display unit; the printed value when the unit is the same. */
function shown(p, unit) {
  if (p.value == null) return p.value_text || "–";
  if (asPrinted(p, unit)) return `${p.comparator || ""}${loc(p.printed.replace(/^[<>=]+/, ""))}`;
  return `${p.comparator || ""}${fmtNum(p.y)}`;
}

async function openFile(path) {
  const res = await fetch(path, { headers: { Authorization: `Bearer ${token.get() || ""}` } });
  if (!res.ok) throw new Error(`${res.status}`);
  const url = URL.createObjectURL(await res.blob());
  window.open(url, "_blank", "noopener");
  setTimeout(() => URL.revokeObjectURL(url), 120_000);
}

async function blobUrl(path) {
  const res = await fetch(path, { headers: { Authorization: `Bearer ${token.get() || ""}` } });
  if (!res.ok) throw new Error(`${res.status}`);
  return URL.createObjectURL(await res.blob());
}

// --- section on the health page ------------------------------------------------------------------

export async function labsSection(ctx) {
  const [ov, reports, imports] = await Promise.all([
    api("/api/v1/labs/overview"), api("/api/v1/labs/reports"), api("/api/v1/labs/imports"),
  ]);
  const rows = ov.groups.flatMap((g) => g.rows);
  const fileInput = h("input", { type: "file", accept: "application/pdf,image/*", hidden: true });
  const uploadMsg = h("span", { class: "small muted" });
  fileInput.addEventListener("change", async () => {
    const file = fileInput.files[0];
    if (!file) return;
    const body = new FormData();
    body.append("file", file);
    body.append("client_meta", JSON.stringify({ app: "vitalocal-web" }));
    uploadMsg.textContent = t("Uploading …");
    try {
      const imp = await api("/api/v1/labs/imports", { method: "POST", body });
      ctx.navigate(`#/health/labs/import/${imp.id}`);
    } catch (e) {
      uploadMsg.textContent = e.status === 409 ? t("Set up an AI model that can read images first (Settings → AI).") : t("Not uploaded: {msg}", { msg: e.message });
    }
  });
  const setSystem = async (system) => {
    await api("/api/v1/settings/labs", { method: "PUT", json: { ...ov.settings, system } });
    ctx.rerender();
  };

  const pending = imports.length ? h("div", { class: "stack-sm", style: { marginBottom: "12px" } }, imports.map((i) => h("div", { class: ["notice", i.status === "failed" && "warn"] },
    h("div", { class: "toolbar" },
      h("b", {}, i.filename || t("Report")), h("span", { class: "muted small" }, f.dateTime(i.created_at)),
      i.status === "ready" ? h("span", { class: "badge accent" }, t("{n} values to review", { n: i.n_values }))
        : i.status === "failed" ? h("span", { class: "badge warn" }, t("failed"))
          : h("span", { class: "badge" }, t("model is reading …")),
      h("span", { style: { flex: 1 } }),
      h("a", { href: `#/health/labs/import/${i.id}` }, i.status === "ready" ? t("Review") : t("Open")))))) : null;

  const off = rows.filter((r) => r.latest.status === "low" || r.latest.status === "high").length;
  const lastDate = rows.map((r) => r.latest.date).sort().at(-1);
  const summary = rows.length ? [
    off ? t("{n} outside the reference", { n: off }) : t("all within the reference"),
    lastDate ? t("latest report {date}", { date: f.mediumDate(lastDate) }) : null,
    imports.some((i) => i.status === "ready") ? t("import to review") : null,
  ].filter(Boolean).join(" · ") : t("no values yet");
  // values change rarely: closed by default (remembered per browser), the head says what matters
  return card({ title: t("Lab results"), icon: "flask", collapsible: true, id: "health.labs", open: imports.length > 0, summary },
    h("div", { class: "toolbar" },
      h("div", { class: "segmented", role: "group", "aria-label": t("Units") },
        [["si", "SI units"], ["conventional", "conventional"]].map(([k, l]) =>
          h("button", { class: ov.settings.system === k && "active", onclick: () => setSystem(k) }, t(l)))),
      menu([
        { label: t("Enter report"), icon: "pencil", onclick: () => ctx.navigate("#/health/labs/new") },
        aiOn("labs") ? { label: t("Import report"), icon: "file-import", onclick: () => fileInput.click() } : null,
      ].filter(Boolean), { trigger: h("button", { class: "ghost" }, icon("plus", { size: 16 }), t("Report")) }), fileInput, uploadMsg),
    pending,
    rows.length ? overviewTable(ctx, ov) : h("div", { class: "muted small" },
      aiOn("labs") ? t("No lab results yet. Enter a report by hand or import a PDF or photo; the local model reads it and you confirm every value.")
        : t("No lab results yet. Enter a report by hand.")),
    reports.length ? reportList(ctx, reports) : null,
    h("div", { class: "muted small", style: { marginTop: "10px" } },
      t("Status compares a value with the reference range printed by the lab (a printed flag wins). It is not a medical assessment.")));
}

function overviewTable(ctx, ov) {
  const body = h("tbody");
  for (const g of ov.groups) {
    body.append(h("tr", { class: "rest" }, h("td", { colspan: 6 }, h("b", {}, g.label))));
    for (const r of g.rows) {
      const detail = h("tr", { class: "lab-detail", hidden: true }, h("td", { colspan: 6 }));
      const p = r.latest, prev = r.previous;
      const off = p.status === "low" || p.status === "high";
      const tr = h("tr", { class: "link", onclick: () => {
        detail.hidden = !detail.hidden;
        if (!detail.hidden && !detail.firstChild.firstChild) detail.firstChild.append(analyteDetail(ctx, r));
      } },
        h("td", { class: "lab-name" }, r.label, r.analyte_key ? null : h("span", { class: "lab-sub" }, t("not in the catalog"))),
        h("td", { class: "r" }, h("span", { class: ["lab-value", off && "off"], title: asPrinted(p, r.unit) ? "" : `${t("printed")}: ${loc(p.printed)} ${p.unit || ""}` },
          `${shown(p, r.unit)} `, h("span", { class: "muted small" }, r.unit || "")), h("span", { class: "lab-sub" }, f.numDate(p.date))),
        h("td", {}, refText(p, r.unit), p.reference && !asPrinted(p, r.unit) ? h("span", { class: "lab-sub" }, `${t("printed")}: ${loc(p.reference)} ${p.unit || ""}`) : null,
          r.target ? h("span", { class: "lab-sub" }, `${t("target")} ${targetText(r.target)}`) : null),
        h("td", {}, statusBadge(p.status, p.status_source === "flag" ? t("printed flag: {f}", { f: p.flag }) : ""),
          r.target ? h("div", { style: { marginTop: "3px" } }, targetBadge(r.target_status)) : null),
        h("td", { class: "r" }, prev ? [h("span", { class: "num" }, `${shown(prev, r.unit)} `, trendArrow(r)), h("span", { class: "lab-sub" }, f.numDate(prev.date))] : h("span", { class: "muted" }, "–")),
        h("td", {}, r.count > 1 ? labChart({ width: 96, height: 26, mini: true, points: chartPoints(r), refLow: p.y_ref_low, refHigh: p.y_ref_high })
          : h("span", { class: "muted small" }, t("1 value"))));
      body.append(tr, detail);
    }
  }
  return h("div", { class: "table-wrap" }, h("table", { class: "lab-table" },
    h("thead", {}, h("tr", {}, h("th", {}, t("Value")), h("th", { class: "r" }, t("Current")), h("th", {}, t("Lab reference")),
      h("th", {}, t("Status")), h("th", { class: "r" }, t("Previous")), h("th", {}, t("Course")))), body));
}

const targetText = (tg) => (tg.min != null && tg.max != null ? `${fmtNum(tg.min)}–${fmtNum(tg.max)}`
  : tg.min != null ? `≥ ${fmtNum(tg.min)}` : `≤ ${fmtNum(tg.max)}`);

function targetBadge(status) {
  if (!status) return h("span", { class: "badge" }, t("target –"));
  return h("span", { class: ["badge", status === "normal" ? "ok" : "warn"] },
    status === "normal" ? t("in target") : status === "low" ? t("below target") : t("above target"));
}

function trendArrow(r) {
  if (r.delta == null) return null;
  const name = r.trend === "up" ? "arrow-up-right" : r.trend === "down" ? "arrow-down-right" : "arrow-right";
  return h("span", { class: "muted small", title: `${r.delta > 0 ? "+" : ""}${fmtNum(r.delta)} ${r.unit}` }, icon(name, { size: 14 }));
}

const chartPoints = (r) => r.points.map((p) => ({ t: Date.parse(`${p.date}T00:00:00Z`), date: p.date, y: p.y, status: p.status,
  label: `${f.numDate(p.date)}: ${shown(p, r.unit)} ${r.unit || ""}${p.lab ? ` · ${p.lab}` : ""}` }));

function analyteDetail(ctx, r) {
  const p = r.latest;
  const unitPick = r.analyte_key && r.units.length > 1 ? h("select", { onchange: async (e) => {
    const s = await api("/api/v1/settings/labs");
    await api("/api/v1/settings/labs", { method: "PUT", json: { ...s, units: { ...s.units, [r.analyte_key]: e.target.value } } });
    ctx.rerender();
  } }, r.units.map((u) => h("option", { value: u, selected: u === r.unit }, u))) : null;

  return h("div", { class: "stack" },
    r.count > 1 ? responsive(h("div"), (w) => labChart({ width: w, height: 190, points: chartPoints(r), refLow: p.y_ref_low, refHigh: p.y_ref_high,
      targetMin: r.target?.min, targetMax: r.target?.max, format: fmtNum })) : null,
    r.note ? h("div", { class: "muted small" }, r.note) : null,
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, t("Date")), h("th", {}, t("Lab")), h("th", { class: "r" }, t("Printed")), h("th", { class: "r" }, r.unit || ""),
        h("th", {}, t("Lab reference")), h("th", {}, t("Status")))),
      h("tbody", {}, [...r.points].reverse().map((x) => [
        h("tr", {},
          h("td", {}, h("a", { href: `#/health/labs/report/${x.report_id}` }, f.numDate(x.date))),
          h("td", { class: "muted small" }, x.lab || "–", x.material ? ` · ${x.material}` : ""),
          h("td", { class: "r num" }, `${loc(x.printed)} ${x.unit || ""}`),
          h("td", { class: "r num" }, x.y != null ? `${x.comparator || ""}${fmtNum(x.y)}` : "–"),
          h("td", {}, x.reference ? `${loc(x.reference)}${asPrinted(x, r.unit) ? "" : ` ${x.unit || ""}`}` : "–"),
          h("td", {}, statusBadge(x.status))),
        x.note ? h("tr", { class: "rest" }, h("td", { colspan: 6 }, h("div", { class: "lab-note" }, x.note))) : null,
      ])))),
    h("div", { class: "toolbar" },
      unitPick ? h("label", { class: "toolbar small muted" }, t("Show in"), unitPick) : null,
      r.analyte_key ? targetForm(ctx, r) : null));
}

function targetForm(ctx, r) {
  const num = (v) => h("input", { type: "number", step: "any", class: "num-input", value: v ?? "" });
  const lo = num(r.target?.min), hi = num(r.target?.max);
  const note = h("input", { type: "text", maxlength: 300, placeholder: t("e.g. from the doctor"), value: r.target?.note || "" });
  const err = h("span", { class: "small warn-text" });
  const save = async () => {
    const body = { min_value: lo.value === "" ? null : Number(lo.value), max_value: hi.value === "" ? null : Number(hi.value), unit: r.unit, note: note.value.trim() || null };
    try {
      if (body.min_value == null && body.max_value == null) await api(`/api/v1/labs/targets/${r.analyte_key}`, { method: "DELETE" });
      else await api(`/api/v1/labs/targets/${r.analyte_key}`, { method: "PUT", json: body });
      ctx.rerender();
    } catch (e) { err.textContent = e.message; }
  };
  return h("span", { class: "toolbar small muted" }, t("Personal target"), lo, "–", hi, r.unit, note,
    h("button", { onclick: save }, t("Save")), err);
}

function reportList(ctx, reports) {
  return h("details", { style: { marginTop: "14px" } },
    h("summary", { class: "muted small" }, t("Reports ({n})", { n: reports.length })),
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, t("Sample taken")), h("th", {}, t("Lab")), h("th", { class: "r" }, t("Values")),
        h("th", { class: "r" }, t("outside")), h("th", {}, t("Source")), h("th", {}))),
      h("tbody", {}, reports.map((r) => h("tr", {},
        h("td", {}, h("a", { href: `#/health/labs/report/${r.id}` }, f.numDate(r.collected_on))),
        h("td", {}, r.lab || "–"),
        h("td", { class: "r num" }, r.n_values),
        h("td", { class: "r num" }, r.n_outside || "–"),
        h("td", { class: "muted small" }, r.source === "import" ? t("import") : t("manual")),
        h("td", {}, r.has_file ? h("button", { class: "link-btn", onclick: () => openFile(`/api/v1/labs/reports/${r.id}/file`) }, t("Original")) : null)))))));
}

// --- report form (manual entry, editing, review) -----------------------------------------------------

function analyteList(catalog) {
  return h("datalist", { id: "lab-analytes" }, catalog.analytes.map((a) =>
    h("option", { value: a.label }, [a.display_unit, ...a.synonyms.slice(0, 4)].join(" · "))));
}

/** One value row; `review` adds the confirm checkbox, the source line and the model's warnings. */
function valueRow(catalog, v = {}, review = null) {
  const byLabel = new Map(catalog.analytes.map((a) => [a.label.toLowerCase(), a]));
  const byKey = new Map(catalog.analytes.map((a) => [a.key, a]));
  const input = (value, attrs = {}) => h("input", { type: "text", value: value ?? "", ...attrs });
  let key = v.analyte_key || null;
  const analyte = input(key ? byKey.get(key)?.label : "", { list: "lab-analytes", placeholder: t("from the catalog …"), "aria-label": t("Analyte") });
  const name = input(v.name, { maxlength: 120, required: true, placeholder: t("as printed"), "aria-label": t("Name") });
  const value = input(v.value != null ? loc(v.value) : "", { required: true, placeholder: "4,33 · <0,5", "aria-label": t("Value"), inputmode: "decimal" });
  const unitList = h("datalist", { id: `u-${uuid4()}` });
  const unit = input(v.unit, { maxlength: 30, list: unitList.id, placeholder: t("Unit"), "aria-label": t("Unit") });
  const reference = input(v.reference, { maxlength: 200, placeholder: "< 5,2 · 1,3 - 1,8", "aria-label": t("Lab reference") });
  const flag = input(v.flag, { maxlength: 10, placeholder: "H/L", "aria-label": t("Flag") });
  const material = input(v.material, { maxlength: 60, placeholder: "Serum, EDTA-Blut", "aria-label": t("Material") });
  const method = input(v.method, { maxlength: 60, placeholder: t("optional"), "aria-label": t("Method") });
  const note = h("textarea", { rows: 1, maxlength: 4000, placeholder: t("comment of the lab"), "aria-label": t("Comment") }, v.note || "");
  const hint = h("span", { class: "muted small" });
  const own = h("button", { type: "button", class: "link-btn", hidden: true }, t("create as own analyte"));

  const sync = () => {
    const a = byLabel.get(analyte.value.trim().toLowerCase());
    key = a ? a.key : null;
    unitList.replaceChildren(...(a ? a.units : []).map((u) => h("option", { value: u })));
    if (a && !name.value.trim()) name.value = a.label;
    if (a && !unit.value.trim()) unit.value = a.display_unit;
    hint.textContent = a ? (a.material ? t("material: {m}", { m: t(a.material) }) : "") : analyte.value.trim() ? t("not in the catalog") : t("matched automatically if possible");
    own.hidden = !!a || !name.value.trim();
  };
  analyte.addEventListener("change", sync);
  analyte.addEventListener("input", sync);
  name.addEventListener("input", () => { own.hidden = !!key || !name.value.trim(); });
  own.addEventListener("click", async () => {
    try {
      const a = await api("/api/v1/labs/analytes", { method: "POST", json: { label: name.value.trim(), unit: unit.value.trim() || "–" } });
      const entry = { key: a.key, label: a.label, units: [a.unit], display_unit: a.unit, synonyms: [], material: a.material };
      catalog.analytes.push(entry);
      byLabel.set(a.label.toLowerCase(), entry);
      byKey.set(a.key, entry);
      document.getElementById("lab-analytes")?.append(h("option", { value: a.label }));
      analyte.value = a.label;
      sync();
    } catch (e) { hint.textContent = e.message; }
  });
  sync();

  const confirm = review ? h("input", { type: "checkbox", "aria-label": t("confirmed") }) : null;
  const row = h("div", { class: ["lab-row", review && "review"] },
    review ? h("div", { class: "toolbar" },
      h("label", { class: "form-check" }, confirm, h("b", {}, t("confirmed"))),
      h("span", { class: "muted small" }, t("page {n}", { n: review.page })),
      review.found_in_text === true ? h("span", { class: "badge ok", title: t("The value appears in the PDF's text layer.") }, t("found in the text")) : null,
      review.status ? statusBadge(review.status) : null,
      (review.warnings || []).map((w) => h("span", { class: "badge warn" }, t(w.replace(/unit .* does not convert.*/, "unit does not convert").replace(/material unclear.*/, "material unclear")))),
      h("span", { style: { flex: 1 } }),
      h("button", { type: "button", class: "link-btn", onclick: () => row.remove() }, t("remove"))) : null,
    review?.source_line ? h("div", { class: "lab-source", title: t("line as the model read it") }, review.source_line) : null,
    h("div", { class: "lab-fields" },
      h("label", {}, t("Analyte"), analyte), h("label", {}, t("Name as printed"), name), h("label", {}, t("Value"), value),
      h("label", {}, t("Unit"), unit, unitList), h("label", {}, t("Lab reference"), reference), h("label", {}, t("Flag"), flag)),
    h("div", { class: "lab-fields more" }, h("label", {}, t("Material"), material), h("label", {}, t("Method"), method), h("label", {}, t("Comment"), note)),
    h("div", { class: "toolbar" }, hint, own,
      review ? null : h("span", { style: { flex: 1 } }),
      review ? null : h("button", { type: "button", class: "link-btn", onclick: () => row.remove() }, t("remove"))));
  if (confirm) {
    const mark = () => row.classList.toggle("confirmed", confirm.checked);
    confirm.addEventListener("change", mark);
  }
  row.confirmed = () => !confirm || confirm.checked;
  row.value = () => ({
    analyte_key: key, name: name.value.trim(), value: value.value.trim(), unit: unit.value.trim() || null,
    reference: reference.value.trim() || null, flag: flag.value.trim() || null, material: material.value.trim() || null,
    method: method.value.trim() || null, note: note.value.trim() || null,
  });
  return row;
}

function reportForm(ctx, catalog, { report = null, proposal = null, importId = null } = {}) {
  const src = report || proposal || {};
  const date = (v) => h("input", { type: "date", value: v || "" });
  const collected = date(src.collected_on);
  collected.required = true;
  const reported = date(src.reported_on);
  const lab = h("input", { type: "text", maxlength: 120, value: src.lab || "", placeholder: t("e.g. MVZ Labor") });
  const note = h("input", { type: "text", maxlength: 4000, value: report?.note || "", placeholder: t("optional"), class: "wide" });
  const rows = h("div", { class: "lab-rows" });
  const values = report?.values || proposal?.values || [];
  for (const v of values) {
    rows.append(valueRow(catalog, proposal ? v : { ...v, value: v.printed || v.value_text, reference: v.reference }, proposal ? v : null));
  }
  if (!values.length) rows.append(valueRow(catalog));
  const fileInput = importId ? null : h("input", { type: "file", accept: "application/pdf,image/*" });
  const err = h("div", { class: "error", hidden: true });
  const counter = h("span", { class: "muted small" });
  const saveBtn = h("button", { class: "primary", type: "submit" }, t("Save"));
  const update = () => {
    if (!proposal) return;
    const n = [...rows.children].filter((r) => r.confirmed()).length;
    counter.textContent = t("{n} of {m} values confirmed; only confirmed values are saved.", { n, m: rows.children.length });
    saveBtn.textContent = t("Save {n} confirmed values", { n });
    saveBtn.disabled = !n;
  };
  rows.addEventListener("change", update);
  new MutationObserver(update).observe(rows, { childList: true });
  update();

  const submit = async (e) => {
    e.preventDefault();
    const body = {
      id: report?.id || uuid4(), collected_on: collected.value, reported_on: reported.value || null,
      lab: lab.value.trim() || null, note: note.value.trim() || null,
      values: [...rows.children].filter((r) => r.confirmed()).map((r) => r.value()).filter((v) => v.name || v.value),
    };
    try {
      const saved = importId ? await api(`/api/v1/labs/imports/${importId}/confirm`, { method: "POST", json: body })
        : report ? await api(`/api/v1/labs/reports/${report.id}`, { method: "PUT", json: body })
          : await api("/api/v1/labs/reports", { method: "POST", json: body });
      if (!report && !importId && fileInput?.files[0]) {
        const fd = new FormData();
        fd.append("file", fileInput.files[0]);
        await api(`/api/v1/labs/reports/${saved.id}/file`, { method: "PUT", body: fd });
      }
      ctx.navigate("#/health");
    } catch (ex) {
      err.hidden = false;
      err.textContent = ex.message;
    }
  };
  const dup = proposal?.possible_duplicates?.length ? h("div", { class: "notice warn small" },
    t("Possibly already saved: "), proposal.possible_duplicates.map((d, i) => [i ? ", " : "", h("a", { href: `#/health/labs/report/${d.id}` }, `${f.numDate(d.collected_on)} ${d.lab || ""}`)])) : null;
  const pageErrors = proposal?.page_errors?.length ? h("div", { class: "notice warn small" },
    proposal.page_errors.map((pe) => h("div", {}, t("Page {n}: {msg}", { n: pe.page, msg: pe.error })))) : null;

  return h("form", { class: "form", onsubmit: submit },
    analyteList(catalog), dup, pageErrors,
    h("div", { class: "toolbar" },
      h("label", { class: "form-row" }, h("span", {}, t("Sample taken (date)")), collected),
      h("label", { class: "form-row" }, h("span", {}, t("Report date")), reported),
      h("label", { class: "form-row" }, h("span", {}, t("Lab")), lab)),
    h("label", { class: "form-row" }, h("span", {}, t("Note")), note),
    fileInput ? h("label", { class: "form-row" }, h("span", {}, report?.has_file ? t("Replace original file (PDF or image)") : t("Original file (PDF or image, optional)")), fileInput) : null,
    rows,
    h("div", {}, h("button", { type: "button", onclick: () => rows.append(valueRow(catalog, {}, proposal ? { page: "–" } : null)) }, t("+ Value"))),
    err,
    h("div", { class: "toolbar" }, saveBtn, counter,
      h("button", { type: "button", onclick: () => ctx.navigate("#/health") }, t("Cancel")),
      h("span", { style: { flex: 1 } }),
      report ? h("button", { type: "button", class: "danger", onclick: async () => {
        if (!confirm(t("Delete this report with all its values and the original file?"))) return;
        await api(`/api/v1/labs/reports/${report.id}`, { method: "DELETE" });
        ctx.navigate("#/health");
      } }, t("Delete")) : null,
      importId ? h("button", { type: "button", class: "danger", onclick: async () => {
        if (!confirm(t("Discard this import and delete the uploaded file?"))) return;
        await api(`/api/v1/labs/imports/${importId}`, { method: "DELETE" });
        ctx.navigate("#/health");
      } }, t("Discard")) : null));
}

export async function labReportView(ctx, id = null) {
  const [catalog, report] = await Promise.all([api("/api/v1/labs/catalog"), id ? api(`/api/v1/labs/reports/${id}`) : null]);
  return h("div", {},
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, report ? t("Lab report {date}", { date: f.numDate(report.collected_on) }) : t("Enter lab report")),
        h("div", { class: "muted small" }, t("Choose the analyte from the catalog so that values of different labs compare; enter value, unit and reference as printed."))),
      h("div", { class: "toolbar" },
        report?.has_file ? h("button", { onclick: () => openFile(`/api/v1/labs/reports/${report.id}/file`) }, t("Original")) : null,
        h("a", { href: "#/health" }, t("Back")))),
    card(t("Report"), null, reportForm(ctx, catalog, { report })));
}

export async function labImportView(ctx, id) {
  const imp = await api(`/api/v1/labs/imports/${id}`);
  const head = (sub) => h("div", { class: "page-head" },
    h("div", {}, h("h1", {}, t("Review lab report")), h("div", { class: "muted small" }, sub)),
    h("div", { class: "toolbar" }, h("button", { onclick: () => openFile(`/api/v1/labs/imports/${id}/file`) }, t("Original")), h("a", { href: "#/health" }, t("Back"))));

  if (imp.status === "pending" || imp.status === "running") {
    const started = Date.parse(imp.started_at || imp.created_at);
    const msg = h("div", { class: "notice" }, t("The local model is reading {file} …", { file: imp.filename || t("the report") }));
    const tick = () => {
      if (!ctx.isCurrent()) return;
      msg.textContent = t("The local model is reading {file} … {s} s", { file: imp.filename || t("the report"), s: Math.round((Date.now() - started) / 1000) });
    };
    const timer = setInterval(tick, 1000);
    setTimeout(() => { clearInterval(timer); if (ctx.isCurrent()) ctx.rerender(); }, 3000);
    return h("div", {}, head(t("Nothing is saved until you confirm the values.")), card(t("Import"), null, msg));
  }
  if (imp.status === "failed") {
    return h("div", {}, head(t("Nothing was saved.")), card(t("Import"), null,
      h("div", { class: "error" }, t("The model could not read the report: {msg}", { msg: imp.error || "?" })),
      h("div", { class: "toolbar", style: { marginTop: "10px" } },
        h("button", { class: "primary", onclick: async () => { await api(`/api/v1/labs/imports/${id}/retry`, { method: "POST" }); ctx.rerender(); } }, t("Try again")),
        h("button", { class: "danger", onclick: async () => { await api(`/api/v1/labs/imports/${id}`, { method: "DELETE" }); ctx.navigate("#/health"); } }, t("Discard")),
        h("a", { href: "#/settings/ai" }, t("AI model settings")))));
  }
  if (imp.status === "confirmed") {
    return h("div", {}, head(t("Already saved.")), card(t("Import"), null, h("a", { href: `#/health/labs/report/${imp.report_id}` }, t("Open report"))));
  }

  const catalog = await api("/api/v1/labs/catalog");
  const pages = h("div", { class: "lab-pages", onclick: () => pages.classList.toggle("zoom") });
  const n = Math.min(imp.pages || 1, 8);
  for (let i = 1; i <= n; i++) {
    const img = h("img", { alt: t("page {n}", { n: i }), loading: "lazy" });
    blobUrl(`/api/v1/labs/imports/${id}/pages/${i}`).then((u) => { img.src = u; }).catch(() => img.replaceWith(h("div", { class: "muted small" }, t("page {n} not available", { n: i }))));
    pages.append(img);
  }
  return h("div", {},
    head(t("{model} read {n} values in {s} s. Check each value against the original and tick it; only ticked values are saved.",
      { model: imp.model || "", n: imp.proposal.values.length, s: Math.round((imp.duration_ms || 0) / 1000) })),
    h("div", { class: "lab-review" },
      card(t("Values"), null, reportForm(ctx, catalog, { proposal: imp.proposal, importId: id })),
      h("div", {}, pages, h("div", { class: "muted small", style: { marginTop: "6px" } }, t("Click the page to zoom.")))));
}
