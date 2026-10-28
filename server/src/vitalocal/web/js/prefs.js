// Per-browser preferences: theme (language is in i18n.js).

const THEME_KEY = "vitalocal.theme";

export function currentTheme() {
  try { return localStorage.getItem(THEME_KEY) || "system"; } catch { return "system"; }
}

export function applyTheme(value) {
  if (value === "light" || value === "dark") document.documentElement.dataset.theme = value;
  else delete document.documentElement.dataset.theme;
  try { localStorage.setItem(THEME_KEY, value); } catch { /* ignore */ }
}
