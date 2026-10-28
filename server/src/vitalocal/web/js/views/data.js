import { api, meta, token } from "../api.js";
import { icon } from "../icons.js";
import { heatmap } from "../charts.js";
import { h, responsive } from "../dom.js";
import * as f from "../format.js";
import { t } from "../i18n.js";
import { card } from "../ui.js";

const COVERAGE_DAYS = 60;
const BATCH_PAGE = 50;
const COVERAGE_ROWS = [
  ["heart_rate", "Heart rate"], ["stress", "Stress"], ["body_energy", "Body energy"], ["steps", "Steps"],
  ["spo2", "SpO₂"], ["respiratory_rate", "Respiration"], ["sleep_stage_code", "Sleep stages"],
  ["hrv_rmssd", "HRV values"], ["daily.sleep_score", "Sleep score"], ["resting_heart_rate", "Resting HR"],
  ["daily.skin_temp_nightly", "Skin temperature"], ["workout_heart_rate", "Workout heart rate"],
  ["nutrition.energy", "Food log (Waistline)"], ["weight", "Weight"],
];

/** Settings › Data & import: a small import, then coverage, reconciliation and the import history. */
export async function dataSection(ctx) {
  const to = ctx.meta.last_day;
  const [batches, recon, sets, coverage] = await Promise.all([
    api("/api/v1/ingest/batches", { params: { limit: BATCH_PAGE } }),
    api("/api/v1/quality/reconciliation"),
    api("/api/v1/quality/activity-sets"),
    to ? api("/api/v1/quality/coverage", { params: {
      from: `${f.addDays(to, -COVERAGE_DAYS)}T00:00:00Z`, to: `${f.addDays(to, 2)}T00:00:00Z`, tz: ctx.meta.timezone } })
      : Promise.resolve([]),
  ]);
  return h("div", { class: "stack" },
    h("p", { class: "muted small", style: { margin: 0 } }, t("Imports, raw-data reconciliation and coverage. Missing data is shown as missing.")),
    uploadCard(ctx),
    exportCard(),
    coverageCard(coverage, to),
    h("div", { class: "grid-2" }, reconciliationCard(recon), setsCard(sets)),
    batchesCard(batches));
}

/** The Android companion as published here (upload below or android/publish.sh); downloaded with
 * the API token, and the app updates itself from it. */
export function appCard() {
  const body = h("div", { class: "muted small" }, t("Loading …"));
  api("/api/v1/app/android").then((app) => {
    if (!app) {
      body.replaceChildren(h("div", { class: "muted small" }, t("No app published yet. Upload the APK of a VitaLocal release below.")));
      return;
    }
    const status = h("span", { class: "small muted" });
    const download = async () => {
      status.textContent = t("Downloading …");
      try {
        const res = await fetch("/api/v1/app/android/apk", { headers: { Authorization: `Bearer ${token.get() || ""}` } });
        if (!res.ok) throw new Error(`${res.status}`);
        const url = URL.createObjectURL(await res.blob());
        const a = h("a", { href: url, download: `VitaLocal-${app.version}.apk` });
        document.body.append(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 60_000);
        status.textContent = t("Open the file on the phone to install the update; settings stay.");
      } catch (e) {
        status.textContent = t("Not downloaded: {msg}", { msg: e.message });
      }
    };
    body.replaceChildren(
      h("div", { class: "stat-row" },
        h("span", {}, h("span", { class: "muted" }, `${t("Version")} `), h("b", {}, app.version)),
        h("span", {}, h("span", { class: "muted" }, `${t("Size")} `), h("b", {}, f.bytes(app.size))),
        h("span", {}, h("span", { class: "muted" }, `${t("Published")} `), h("b", {}, f.dateTime(app.uploaded_at)))),
      h("div", { class: "toolbar", style: { marginTop: "10px" } },
        h("button", { class: "primary", onclick: download }, t("Download APK")), status),
      h("div", { class: "muted small", style: { marginTop: "8px" } },
        t("Installing needs \"Install unknown apps\" for the browser once. Updates install over the existing app."),
        h("div", { class: "num", title: "SHA-256" }, `SHA-256 ${app.sha256.slice(0, 16)}…`)));
  }).catch((e) => body.replaceChildren(h("div", { class: "error" }, e.message)));
  return h("div", { class: "stack" }, card({ title: t("Android app"), icon: "device-mobile" }, null, body), apkUploadCard());
}

/** Publish an APK (e.g. from a VitaLocal release) here; installed apps update to it. Version and
 * version code come from the file name "VitaLocal-1.7.0-35.apk" when it has them. */
function apkUploadCard() {
  const file = h("input", { type: "file", accept: ".apk,application/vnd.android.package-archive" });
  const version = h("input", { type: "text", placeholder: "1.7.0", class: "num-input", pattern: "[0-9A-Za-z.+-]{1,40}", required: true });
  const code = h("input", { type: "number", min: 1, placeholder: "35", class: "num-input", required: true });
  const status = h("span", { class: "small muted" });
  file.addEventListener("change", () => {
    const m = file.files[0]?.name.match(/(\d+\.\d+(?:\.\d+)?)[-_](\d+)\.apk$/i);
    if (m) { version.value = m[1]; code.value = m[2]; }
  });
  const upload = async (e) => {
    e.preventDefault();
    if (!file.files[0]) return;
    const body = new FormData();
    body.append("file", file.files[0]);
    body.append("version", version.value.trim());
    body.append("version_code", code.value);
    status.textContent = t("Uploading …");
    try {
      await api("/api/v1/app/android", { method: "PUT", body });
      status.textContent = t("Published. Apps update on their next check.");
    } catch (err) {
      status.textContent = t("Not uploaded: {msg}", { msg: err.message });
    }
  };
  return card({ title: t("Publish an app version"), icon: "upload" }, null,
    h("p", { class: "small muted" }, t("Only builds signed with the same key as the installed app can update it. The version code must be higher than the installed one.")),
    h("form", { class: "form", onsubmit: upload },
      h("label", { class: "form-row" }, h("span", {}, t("APK file")), file),
      h("div", { class: "toolbar" },
        h("label", { class: "form-row" }, h("span", {}, t("Version")), version),
        h("label", { class: "form-row" }, h("span", {}, t("Version code")), code)),
      h("div", { class: "toolbar" }, h("button", { class: "primary", type: "submit" }, t("Upload")), status)));
}

function uploadCard(ctx) {
  const status = h("div", { class: "small muted" });
  const input = h("input", { type: "file", accept: ".zip,.json,.csv,application/zip,application/json,text/csv", hidden: true, onchange: (e) => upload(e.target.files[0]) });
  // Exports normally arrive from the app; a manual import is the exception, so it stays small.
  const zone = h("div", { class: "import-row" },
    icon("file-import", { size: 20 }),
    h("div", { class: "grow" },
      h("div", {}, t("Import an export manually")),
      h("div", { class: "small muted" }, t("Gadgetbridge (ZIP), Waistline (JSON/CSV), workouts from Strong or Hevy (CSV), daily values from MyFitnessPal, Cronometer or any CSV with a date column (calories, protein, weight, …). Drop the file here or choose it. Re-uploading the same file does nothing."))),
    h("button", { type: "button", onclick: () => input.click() }, icon("upload", { size: 16 }), t("Choose file")),
    input);
  zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("over"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("over"));
  zone.addEventListener("drop", (e) => { e.preventDefault(); zone.classList.remove("over"); upload(e.dataTransfer.files[0]); });

  async function upload(file) {
    if (!file) return;
    const body = new FormData();
    body.append("file", file);
    status.textContent = t("Uploading {name} ({size}) …", { name: file.name, size: f.bytes(file.size) });
    try {
      const res = await api("/api/v1/ingest/upload", { method: "POST", body });
      if (!res.created) { status.textContent = t("This file was already imported (batch {id}).", { id: res.batch_id }); return; }
      status.textContent = t("Processing batch {id} …", { id: res.batch_id });
      for (;;) {
        await new Promise((r) => setTimeout(r, 2000));
        const b = await api(`/api/v1/ingest/batches/${res.batch_id}`);
        if (!["pending", "processing"].includes(b.status)) {
          status.textContent = `${t("Batch {id}: {status}", { id: res.batch_id, status: t(b.status.replaceAll("_", " ")) })}${b.error ? ` – ${b.error}` : ""}`;
          await meta(true);
          if (ctx.isCurrent()) setTimeout(() => window.dispatchEvent(new HashChangeEvent("hashchange")), 800);
          return;
        }
      }
    } catch (e) {
      status.textContent = t("Upload failed: {msg}", { msg: e.message });
    }
  }
  return h("section", { class: "card" }, zone, status);
}

function coverageCard(rows, to) {
  if (!to) return null;
  const days = [];
  for (let i = COVERAGE_DAYS - 1; i >= 0; i--) days.push(f.addDays(to, -i));
  const by = {};
  for (const r of rows) {
    by[r.metric] ??= {};
    by[r.metric][r.day] = (by[r.metric][r.day] || 0) + r.samples;
  }
  const hm = COVERAGE_ROWS.filter(([k]) => by[k]).map(([k, label]) => {
    const raw = by[k];
    const max = Math.max(...Object.values(raw));
    return { label: t(label), raw, values: Object.fromEntries(Object.entries(raw).map(([d, n]) => [d, n / max])) };
  });
  return card(t("Coverage · last {n} days", { n: COVERAGE_DAYS }), h("span", { class: "muted small" }, t("darker = more samples (relative to the metric's best day)")),
    h("div", { class: "table-wrap" }, responsive(h("div"), (w) => heatmap({ width: w, rows: hm, days }))));
}

function reconciliationCard(r) {
  if (!r.batch_id) return card(t("Reconciliation"), null, h("div", { class: "muted" }, t("No completed import yet.")));
  const a = r.activities;
  const db = r.gadgetbridge_db;
  const fitErrors = r.categories.reduce((s, c) => s + (c.fit_errors || 0), 0);
  const line = (label, value, badge) => h("tr", {}, h("td", {}, label), h("td", { class: "r" }, value, badge ? " " : null, badge));
  return card(t("Reconciliation"), h("span", { class: "muted small" }, t("batch {id}", { id: r.batch_id })),
    h("table", {}, h("tbody", {},
      line(t("Raw files archived"), f.num(r.categories.reduce((s, c) => s + c.files, 0))),
      line(t("FIT files with decode errors"), f.num(fitErrors), h("span", { class: fitErrors ? "badge warn" : "badge ok" }, fitErrors ? t("check") : t("ok"))),
      line(t("Activity FIT files"), f.num(a.activity_fit_files)),
      line(t("… with Gadgetbridge summary"), f.num(a.db_summaries_linked_to_fit)),
      line(t("Gadgetbridge DB schema"), `v${db.schema_version}`, h("span", { class: db.schema_known ? "badge ok" : "badge warn" }, db.schema_known ? t("verified") : t("unverified"))),
      line(t("Unmapped DB tables with data"), f.num(Object.keys(db.unmapped_tables || {}).length)))),
    a.fit_without_db_summary.length ? h("div", { class: "notice warn", style: { marginTop: "12px" } },
      h("b", {}, t("FIT without Gadgetbridge summary: ")),
      a.fit_without_db_summary.map((x) => x.archive_path.split("/").at(-1)).join(", "),
      t(". The activity is still available from the FIT file.")) : null,
    db.empty_tables?.length ? h("details", { style: { marginTop: "12px" } },
      h("summary", { class: "small muted" }, t("{n} relevant tables are empty (e.g. weight, temperature)", { n: db.empty_tables.length })),
      h("div", { class: "small muted" }, db.empty_tables.join(", "))) : null);
}

function setsCard(rows) {
  return card(t("Strength sets: FIT vs. Gadgetbridge"), null,
    rows.length ? h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, t("Date")), h("th", {}, t("Type")), h("th", { class: "r" }, "FIT"),
        h("th", { class: "r" }, "GB"), h("th", {}, t("Result")))),
      h("tbody", {}, rows.map((r) => h("tr", { class: "link", onclick: () => { location.hash = `#/training/general/${r.activity_id}`; } },
        h("td", {}, f.dateTime(r.start_ts)),
        h("td", {}, f.humanize(r.sub_sport)),
        h("td", { class: "r" }, r.fit_active_sets),
        h("td", { class: "r" }, r.gadgetbridge_set_rows),
        h("td", {}, !r.sources.gadgetbridge_db ? h("span", { class: "badge warn" }, t("FIT only"))
          : r.mismatches.length ? h("span", { class: "badge warn" }, t("{n} differences", { n: r.mismatches.length }))
          : h("span", { class: "badge ok" }, t("identical")))))))) : h("div", { class: "muted" }, t("No set-based activities.")),
    h("p", { class: "muted small" }, t("Active sets compared row by row: repetitions, weight, duration (±1 s).")));
}

const clientLabel = (c) => c && [c.device, c.app].filter(Boolean).join(" · ");

const SOURCE_LABELS = {
  waistline_export: "Waistline (JSON)", waistline_diary_csv: "Waistline (CSV)",
  gadgetbridge_db: "Gadgetbridge (live database)", gadgetbridge_files: "Gadgetbridge (files)",
  daily_csv: "Daily values (CSV)", workout_csv: "Workouts (CSV)", health_connect: "Health Connect",
};
const APP_LABELS = { myfitnesspal: "MyFitnessPal", cronometer: "Cronometer", strong: "Strong", hevy: "Hevy" };

/** All data as a ZIP (every table as CSV plus readable CSVs), fetched with the token. */
function exportCard() {
  const out = h("span", { class: "small muted" });
  const download = async () => {
    out.textContent = t("Preparing …");
    try {
      const res = await fetch("/api/v1/export", { headers: { Authorization: `Bearer ${token.get() || ""}` } });
      if (!res.ok) throw new Error(`${res.status}`);
      const url = URL.createObjectURL(await res.blob());
      const name = res.headers.get("content-disposition")?.match(/filename="?([^";]+)/)?.[1] || "vitalocal-export.zip";
      h("a", { href: url, download: name }).click();
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
      out.textContent = "";
    } catch (e) {
      out.textContent = t("Export failed: {msg}", { msg: e.message });
    }
  };
  return card({ title: t("Export"), icon: "download" }, null,
    h("p", { class: "small muted" }, t("All your data as a ZIP: every table as CSV (for a move or own analyses) plus readable files for the food diary, body values, strength sets and daily values.")),
    h("div", { class: "toolbar" }, h("button", { type: "button", onclick: download }, icon("download", { size: 16 }), t("Download export")), out));
}

function batchRow(b) {
  const cls = { completed: "ok", completed_with_errors: "warn", failed: "warn" };
  return h("tr", {},
    h("td", {}, b.id),
    h("td", {}, f.dateTime(b.received_at)),
    h("td", {}, h("span", { class: `badge ${cls[b.status] || ""}`, title: b.error || "" }, t(b.status.replaceAll("_", " "))),
      b.raw_pruned_at ? h("div", { class: "muted small", title: t("Replaced by a newer export; the imported data stays.") }, t("raw data removed")) : null),
    h("td", {}, SOURCE_LABELS[b.source] ? t(SOURCE_LABELS[b.source]) + (APP_LABELS[b.client_meta?.app] ? ` · ${APP_LABELS[b.client_meta.app]}` : "")
      : `Gadgetbridge ${b.client_meta?.version || ""}`,
      clientLabel(b.client_meta?.client) ? h("div", { class: "muted small" }, t("via {client}", { client: clientLabel(b.client_meta.client) })) : null),
    h("td", {}, b.client_meta?.exported_at ? f.dateTime(b.client_meta.exported_at) : "–"),
    h("td", { class: "muted small", title: b.outer_artifact_sha256 }, `${b.outer_artifact_sha256.slice(0, 12)}…`));
}

function batchesCard(batches) {
  if (!batches.length) return card(t("Import history"), null, h("div", { class: "muted" }, t("No imports yet.")));
  const body = h("tbody", {}, batches.map(batchRow));
  const more = h("button", { class: "link-btn", hidden: batches.length < BATCH_PAGE, onclick: async () => {
    more.disabled = true;
    const before = body.lastElementChild?.firstElementChild?.textContent;
    const page = await api("/api/v1/ingest/batches", { params: { limit: BATCH_PAGE, before_id: before } });
    body.append(...page.map(batchRow));
    more.disabled = false;
    more.hidden = page.length < BATCH_PAGE;
  } }, t("Load older"));
  return card(t("Import history"), null, h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, h("th", {}, "#"), h("th", {}, t("Received")), h("th", {}, t("Status")), h("th", {}, t("Source")),
      h("th", {}, t("Exported")), h("th", {}, "SHA-256"))),
    body)), more);
}
