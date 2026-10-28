// API client. The bearer token is a per-browser convenience kept in localStorage.

const KEY = "vitalocal.token";

export const token = {
  get() { try { return localStorage.getItem(KEY); } catch { return null; } },
  set(v) { try { localStorage.setItem(KEY, v); } catch { /* private mode: token lives for this page only */ } memory = v; },
  clear() { try { localStorage.removeItem(KEY); } catch { /* ignore */ } memory = null; },
};
let memory = null;

export class AuthError extends Error {}

/** Non-2xx answer; `status` and the parsed `detail` let callers handle e.g. 409 conflicts. */
export class ApiError extends Error {
  constructor(status, message, detail) { super(message); this.status = status; this.detail = detail; }
}

export async function api(path, { method = "GET", params, body, json, headers: extra } = {}) {
  const url = new URL(path, location.origin);
  for (const [k, v] of Object.entries(params || {})) if (v != null && v !== "") url.searchParams.set(k, v);
  const headers = { Authorization: `Bearer ${token.get() || memory || ""}`, Accept: "application/json", ...extra };
  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(json);
  }
  const res = await fetch(url, { method, body, headers });
  if (res.status === 401) throw new AuthError("unauthorized");
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* not JSON */ }
    const raw = detail;
    if (Array.isArray(detail)) detail = detail.map((d) => d.msg?.replace(/^Value error, /, "") || JSON.stringify(d)).join("; ");
    else if (detail && typeof detail === "object" && detail.message) detail = detail.message;
    throw new ApiError(res.status, `${res.status}: ${typeof detail === "string" ? detail : JSON.stringify(detail)}`, raw);
  }
  return res.status === 204 ? null : res.json();
}

// Cached metadata (catalog, timezone, data range); refreshed after imports.
let metaPromise = null;
export const meta = (refresh = false) => {
  if (refresh || !metaPromise) metaPromise = api("/api/v1/meta").catch((e) => { metaPromise = null; throw e; });
  return metaPromise;
};

// What the server offers (AI features, visible areas, setup state); refreshed after settings changes.
let capsPromise = null;
let capsValue = null;
export const capabilities = (refresh = false) => {
  if (refresh || !capsPromise) {
    capsPromise = api("/api/v1/capabilities")
      .then((c) => { capsValue = c; return c; })
      .catch((e) => { capsPromise = null; throw e; });
  }
  return capsPromise;
};
/** An AI feature (summary, coach, labs) can be offered: a model is set up and allowed to do it. */
export const aiOn = (feature) => !!capsValue?.ai?.features?.[feature];
/** An area (nutrition, drinks, training, habits, journal, health, supplements, watch) is shown. */
export const moduleOn = (name) => capsValue?.modules?.[name] !== false;
export const units = () => capsValue?.units || "metric";
