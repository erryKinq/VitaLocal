// Formatting helpers. The timezone comes from /api/v1/meta so the UI shows the server's day
// boundaries, not the browser's; number/date formats follow the UI language.

import { locale, t } from "./i18n.js";

let TZ = "UTC";
export const setTimezone = (tz) => { TZ = tz; };
export const timezone = () => TZ;

const cache = new Map();
function fmt(opts) {
  const key = JSON.stringify(opts) + TZ + locale();
  if (!cache.has(key)) cache.set(key, new Intl.DateTimeFormat(locale(), { timeZone: TZ, ...opts }));
  return cache.get(key);
}

export const time = (t) => fmt({ hour: "2-digit", minute: "2-digit" }).format(new Date(t));

const parts = (t) => Object.fromEntries(fmt({ year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit",
  minute: "2-digit", hourCycle: "h23" }).formatToParts(new Date(t)).map((p) => [p.type, p.value]));
/** Local calendar day ("2026-09-29") of a timestamp in the server's timezone. */
export const dayOf = (t) => { const p = parts(t); return `${p.year}-${p.month}-${p.day}`; };
/** Local wall-clock time ("07:05") of a timestamp in the server's timezone, as <input type=time> wants it. */
export const clockOf = (t) => { const p = parts(t); return `${p.hour}:${p.minute}`; };
/** Minutes after midnight as "HH:MM" (clock metrics). */
export const hhmm = (m) => `${String(Math.floor(m / 60) % 24).padStart(2, "0")}:${String(Math.round(m) % 60).padStart(2, "0")}`;
/** "HH:MM" -> minutes after midnight. */
export const minutesOf = (clock) => { const [a, b] = clock.split(":").map(Number); return a * 60 + b; };

/** ISO timestamp for wall-clock `clock` ("HH:MM") on `isoDay` in the server's timezone. */
export function zonedIso(isoDay, clock) {
  const [y, mo, d] = isoDay.split("-").map(Number);
  const [hh, mm] = clock.split(":").map(Number);
  const wall = Date.UTC(y, mo - 1, d, hh, mm);
  const offset = (ms) => { const p = parts(ms); return Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute) - ms; };
  // the offset at the guessed instant, then once more in case a DST switch lies in between
  const first = wall - offset(wall);
  return new Date(wall - offset(first)).toISOString();
}
export const dayLabel = (t) => fmt({ weekday: "short", day: "numeric", month: "short" }).format(new Date(t));
export const longDate = (isoDay) =>
  new Intl.DateTimeFormat(locale(), { weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "UTC" })
    .format(new Date(isoDay + "T00:00:00Z"));
export const mediumDate = (isoDay) =>
  new Intl.DateTimeFormat(locale(), { weekday: "short", day: "numeric", month: "short", year: "numeric", timeZone: "UTC" })
    .format(new Date(isoDay + "T00:00:00Z"));
/** "vor 5 Min." style distance from now. */
export function ago(ts) {
  const min = Math.round((Date.now() - Date.parse(ts)) / 60_000);
  if (min < 1) return t("just now");
  if (min < 60) return t("{n} min ago", { n: min });
  const hrs = Math.round(min / 60);
  if (hrs < 48) return t("{n} h ago", { n: hrs });
  return t("{n} days ago", { n: Math.round(hrs / 24) });
}
export const shortDate = (isoDay) =>
  new Intl.DateTimeFormat(locale(), { day: "numeric", month: "short", timeZone: "UTC" }).format(new Date(isoDay + "T00:00:00Z"));
/** 15.04.2026 */
export const numDate = (isoDay) =>
  new Intl.DateTimeFormat(locale(), { day: "2-digit", month: "2-digit", year: "numeric", timeZone: "UTC" }).format(new Date(isoDay + "T00:00:00Z"));
export const monthLabel = (isoDay) =>
  new Intl.DateTimeFormat(locale(), { month: "short", timeZone: "UTC" }).format(new Date(isoDay + "T00:00:00Z"));
export const dateTime = (t) =>
  fmt({ day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(t));

export function num(v, decimals = 0) {
  if (v == null || Number.isNaN(v)) return "–";
  return Number(v).toLocaleString(locale(), { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

export function duration(minutes) {
  if (minutes == null) return "–";
  const m = Math.round(minutes);
  const hh = Math.floor(m / 60);
  return hh ? `${hh}h ${String(m % 60).padStart(2, "0")}m` : `${m}m`;
}

export const seconds = (s) => (s == null ? "–" : s < 90 ? `${Math.round(s)}s` : duration(s / 60));

export function bytes(n) {
  if (n == null) return "–";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${num(n, i ? 1 : 0)} ${u[i]}`;
}

// ISO day arithmetic without timezone surprises (days are calendar labels, not instants).
export function addDays(isoDay, n) {
  const d = new Date(isoDay + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

export const humanize = (key) => {
  if (key == null || key === "") return "";
  if (/^\d+$/.test(String(key))) return t("Unknown ({v})", { v: key }); // enum value not in the FIT profile
  return t(String(key).replaceAll("_", " ").replace(/^\w/, (c) => c.toUpperCase()));
};

// Body weight in the profile's unit (kg stored; imperial shows and takes lb). Training weights stay kg.
const LB = 0.45359237;
let imperial = false;
export const setUnits = (u) => { imperial = u === "imperial"; };
export const weightUnit = () => (imperial ? "lb" : "kg");
/** kg → the shown number (lb when imperial). */
export const weightValue = (kg) => (kg == null ? null : imperial ? kg / LB : kg);
/** An entered number in the shown unit → kg. */
export const weightKg = (v) => (v == null || v === "" ? null : imperial ? Number(v) * LB : Number(v));
export const bodyWeight = (kg, decimals = 1) => (kg == null ? "–" : `${num(weightValue(kg), decimals)} ${weightUnit()}`);
