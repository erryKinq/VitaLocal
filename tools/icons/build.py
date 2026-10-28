#!/usr/bin/env python3
"""One icon set for WebUI and app: Tabler outline icons (MIT, https://tabler.io/icons).

Fetches the icons listed below once into tools/icons/tabler/ (committed, so later runs work
offline) and generates
  - server/src/vitalocal/web/js/icons.js          icon(name) + emoji → icon mapping
  - android/.../res/drawable/ic_t_<name>.xml        vector drawables
  - android/.../companion/ui/TablerIcons.kt        emoji → drawable mapping

Stored data keeps its emojis (habits, routines, achievements, templates); the clients map them to
icons when drawing and fall back to the emoji when there is no mapping.

    python3 tools/icons/build.py
"""

import re
import urllib.request
from pathlib import Path

VERSION = "3.48.0"
ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "tools/icons/tabler"
WEB_OUT = ROOT / "server/src/vitalocal/web/js/icons.js"
DRAWABLES = ROOT / "android/app/src/main/res/drawable"
KT_OUT = ROOT / "android/app/src/main/java/de/vitalocal/companion/ui/TablerIcons.kt"

# Interface icons (navigation, actions, metrics).
UI = """
sun salad plant-2 barbell heartbeat notebook chart-line settings
chevron-left chevron-right chevron-down chevron-up arrow-up arrow-down arrow-left arrow-right
arrow-up-right arrow-down-right x check plus minus pencil trash dots dots-vertical grip-vertical
search refresh calendar clock download upload external-link star info-circle alert-triangle
eye eye-off lock logout language palette robot device-mobile database layout-dashboard
adjustments-horizontal arrows-move player-play player-skip-forward history list-check target
trophy award medal crown flame droplet coffee cup moon zzz activity heart lungs run walk bike
swimming mountain scale pill flask mood-smile bolt brain battery-3 shoe stretching yoga book
writing dental bed apple carrot glass-off candy-off device-mobile-off brush guitar-pick leaf
heart-handshake wind phone bath notes sparkles seedling tree trees flower cards device-watch
droplet-off link refresh-dot campfire bulb meteor comet confetti hand-stop circles tent
medal-2 diamond ticket target-arrow military-award circle-dot beach circle-check circle body-scan
square square-check filter photo scan barcode wifi cloud plug bell sunrise sunset temperature
stairs ripple gauge percentage file-import report-medical tools-kitchen-2 bowl glass
calendar-stats layout-grid heading flag clipboard-list repeat circle-plus progress-check
sort-ascending maximize minimize home menu-2 apps bookmark
"""

# Emoji used in stored data → icon. Variation selectors (U+FE0F) are ignored when looking up.
EMOJI = {
    "💧": "droplet", "🏃": "run", "🚶": "walk", "🤸": "stretching", "💪": "barbell", "🏋️": "barbell",
    "🧘": "yoga", "📖": "book", "✍️": "writing", "🦷": "dental", "😴": "zzz", "🛏️": "bed",
    "☀️": "sun", "🥦": "carrot", "🍎": "apple", "🚱": "glass-off", "🍬": "candy-off", "☕": "coffee",
    "💊": "pill", "📵": "device-mobile-off", "🧹": "brush", "🎸": "guitar-pick", "🌿": "leaf",
    "❤️": "heart", "🎯": "target", "👟": "shoe", "🪜": "stairs", "🙏": "heart-handshake",
    "🧠": "brain", "🌬️": "wind", "📞": "phone", "🌙": "moon", "🚿": "bath", "🗒️": "notes",
    "🔥": "flame", "🕯️": "flame", "🏕️": "campfire", "💯": "award", "🔦": "bulb",
    "🌋": "flame", "☄️": "meteor", "🌟": "star", "⭐": "star", "✨": "sparkles", "🌠": "comet",
    "🎩": "confetti", "🏆": "trophy", "👑": "crown", "🖐️": "hand-stop", "🤹": "circles", "🎪": "tent",
    "🌱": "seedling", "🥉": "medal", "🥈": "medal", "🥇": "medal", "💎": "diamond",
    "❤️‍🔥": "flame", "🏅": "medal-2", "🎟️": "ticket", "🏹": "target-arrow", "🎖️": "military-award",
    "🔗": "link", "📿": "circle-dot", "🔄": "refresh", "🏖️": "beach", "✅": "circle-check",
    "🌻": "flower", "🌳": "tree", "🌲": "trees", "🌰": "seedling", "🪴": "plant-2", "🃏": "cards",
    "🔒": "lock", "⌚": "device-watch", "🥀": "droplet-off", "🍽": "tools-kitchen-2",
    "🏋": "barbell", "⚙": "settings",
}

# Emojis offered in the habit form's picker (each stored as the emoji, shown as its icon).
PICKER = ["💧", "🏃", "🚶", "🤸", "💪", "🧘", "📖", "✍️", "🦷", "😴", "🛏️", "☀️", "🥦", "🍎", "🚱",
          "🍬", "☕", "💊", "📵", "🧹", "🎸", "🌿", "❤️", "🎯", "👟", "🪜", "🙏", "🧠", "🌬️", "📞",
          "🌙", "🚿", "🗒️"]


def key(emoji: str) -> str:
    return emoji.replace("️", "")


def fetch(name: str) -> str:
    path = CACHE / f"{name}.svg"
    if not path.exists():
        url = f"https://cdn.jsdelivr.net/npm/@tabler/icons@{VERSION}/icons/outline/{name}.svg"
        CACHE.mkdir(parents=True, exist_ok=True)
        path.write_bytes(urllib.request.urlopen(url, timeout=20).read())
    return path.read_text()


def shapes(svg: str) -> list[str]:
    """Path data of all drawn shapes (Tabler 3 outline icons use <path> only)."""
    out = []
    for el in re.findall(r"<(\w+)\b([^>]*)/?>", svg):
        tag, attrs = el
        if tag in ("svg", "/svg"):
            continue
        if tag != "path":
            raise SystemExit(f"unsupported element <{tag}>")
        if 'stroke="none"' in attrs:
            continue
        out.append(re.search(r'\bd="([^"]+)"', attrs).group(1))
    return out


def main() -> None:
    names = sorted(set(UI.split()) | set(EMOJI.values()))
    paths = {n: shapes(fetch(n)) for n in names}

    # web
    icons = ",\n".join(f'  "{n}": {[*paths[n]]!r}'.replace("'", '"') for n in names)
    emoji = ",\n".join(f'  "{key(e)}": "{n}"' for e, n in EMOJI.items())
    picker = ", ".join(f'"{e}"' for e in PICKER)
    WEB_OUT.write_text(f"""// Generated by tools/icons/build.py from Tabler icons {VERSION} (MIT). Do not edit by hand.

import {{ s }} from "./dom.js";

const PATHS = {{
{icons},
}};

const EMOJI = {{
{emoji},
}};

/** Emojis offered in the habit form (stored as emoji, drawn as their icon). */
export const PICKER = [{picker}];

/** An outline icon (24-unit grid, stroke = currentColor), `size` in px. */
export function icon(name, {{ size = 18, cls = "" }} = {{}}) {{
  const d = PATHS[name] || PATHS.circle;
  return s("svg", {{ class: ["icon", cls], width: size, height: size, viewBox: "0 0 24 24", fill: "none",
    stroke: "currentColor", "stroke-width": 1.8, "stroke-linecap": "round", "stroke-linejoin": "round",
    "aria-hidden": "true" }}, d.map((p) => s("path", {{ d: p }})));
}}

/** Icon name for a stored emoji, or null. */
export const emojiIcon = (e) => (e ? EMOJI[e.replace(/\\uFE0F/g, "")] || null : null);

/** The icon for a stored emoji, else the emoji itself as text (custom emojis keep working). */
export function glyph(e, opts) {{
  const n = emojiIcon(e);
  return n ? icon(n, opts) : e ? document.createTextNode(e) : null;
}}
""")

    # android
    DRAWABLES.mkdir(parents=True, exist_ok=True)
    for old in DRAWABLES.glob("ic_t_*.xml"):
        old.unlink()
    for n in names:
        body = "\n".join(
            f'    <path android:pathData="{p}" android:strokeColor="#FF000000" android:strokeWidth="1.8"\n'
            f'        android:strokeLineCap="round" android:strokeLineJoin="round" android:fillColor="#00000000" />'
            for p in paths[n])
        (DRAWABLES / f"ic_t_{n.replace('-', '_')}.xml").write_text(
            f'<?xml version="1.0" encoding="utf-8"?>\n<!-- Tabler icon "{n}" (MIT), generated by tools/icons/build.py -->\n'
            f'<vector xmlns:android="http://schemas.android.com/apk/res/android"\n'
            f'    android:width="24dp" android:height="24dp" android:viewportWidth="24" android:viewportHeight="24">\n'
            f"{body}\n</vector>\n")
    KT_OUT.parent.mkdir(parents=True, exist_ok=True)
    entries = ",\n".join(f'    "{key(e)}" to R.drawable.ic_t_{n.replace("-", "_")}' for e, n in EMOJI.items())
    picker_kt = ", ".join(f'"{key(e)}"' for e in PICKER)
    KT_OUT.write_text(f"""// Generated by tools/icons/build.py from Tabler icons {VERSION} (MIT). Do not edit by hand.
package de.vitalocal.companion.ui

import de.vitalocal.companion.R

private val EMOJI_ICONS: Map<String, Int> = mapOf(
{entries},
)

/** Drawable for a stored emoji, or null (the caller then shows the emoji as text). */
fun emojiIcon(emoji: String?): Int? = emoji?.let {{ EMOJI_ICONS[it.replace("\\uFE0F", "")] }}

/** Emojis offered in the habit form (stored as emoji, drawn as their icon). */
val ICON_PICKER: List<String> = listOf({picker_kt})
""")
    print(f"{len(names)} icons")


if __name__ == "__main__":
    main()
