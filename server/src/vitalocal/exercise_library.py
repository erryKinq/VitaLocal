"""Built-in exercise library (`exercise_library.json`): common exercises with German names (English
ones derived from the keys), the Garmin
exercise the watch records for them, muscles and the equipment they need.

An entry becomes an own exercise only when it is used (taken over); until then it is just a
suggestion. Filtering by equipment answers "what can I do with what I have": an entry fits when
everything it needs is available, so exercises without equipment always fit.
"""

from __future__ import annotations

import json
from functools import cache
from importlib import resources

from vitalocal import training


@cache
def entries() -> tuple[dict, ...]:
    raw = resources.files("vitalocal").joinpath("exercise_library.json").read_text(encoding="utf-8")
    return tuple(json.loads(raw))


@cache
def by_key() -> dict[str, dict]:
    return {e["key"]: e for e in entries()}


# words of the keys that read differently in a name
_EN_WORDS = {"ez": "EZ", "t": "T", "l": "L"}
_EN_JOIN = (("push up", "push-up"), ("pull up", "pull-up"), ("chin up", "chin-up"), ("T bar", "T-bar"),
            ("step up", "step-up"), ("get up", "get-up"), ("pull apart", "pull-apart"), ("L sit", "L-sit"),
            ("flye", "fly"))  # fmt: skip


def english_name(key: str) -> str:
    """English name from the key, e.g. barbell_bench_press → Barbell bench press."""
    name = " ".join(_EN_WORDS.get(w, w) for w in key.split("_"))
    for a, b in _EN_JOIN:
        name = name.replace(a, b)
    return name[0].upper() + name[1:]


def localized(e: dict, lang: str) -> dict:
    return {**e, "name": english_name(e["key"])} if lang == "en" else e


def search(
    muscle: str | None = None, equipment: set[str] | None = None, q: str | None = None, lang: str = "de"
) -> list[dict]:
    """Entries training `muscle` (or one of the muscle group `muscle`) as a primary muscle, needing
    nothing beyond `equipment` (None: any) and containing `q` in the name, in library order (names in
    `lang`)."""
    needle = (q or "").strip().lower()
    wanted = set(training.MUSCLE_GROUPS.get(muscle or "", (muscle,)))
    return [
        e
        for e in (localized(x, lang) for x in entries())
        if (muscle is None or wanted & set(e["muscles"]))
        and (equipment is None or set(e["equipment"]) <= equipment)
        and needle in e["name"].lower()
    ]
