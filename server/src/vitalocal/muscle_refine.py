"""Exercises from before the finer muscles (2026-10-25) get them: chest, shoulders, back and traps are
replaced by the parts the exercise trains, everything else an exercise lists stays.

- What the exercise trains (the target) comes from the built-in library – the entry it was taken over
  from, else the one with its Garmin exercise or its name – else `training.default_muscles` (Garmin
  exercise name, own name, Garmin category).
- A coarse muscle becomes the target's parts of it (`PARTS`): a primary one the parts the target has
  as primary (the others secondary; none primary: all of them primary), a secondary one all of them
  secondary. Without any of its parts in the target it stands for what `training.ALIASES` says
  (traps: the upper traps). So own choices stay: dips with the chest primary keep it, as the mid/lower
  chest; traps on a row become the upper back, on a shrug they stay the upper traps.
"""

from __future__ import annotations

import re

from vitalocal import exercise_library, training

# what a coarse muscle may have meant
PARTS: dict[str, tuple[str, ...]] = {
    "chest": ("chest_upper", "chest_lower"),
    "shoulders": ("delts_front", "delts_side", "delts_rear", "rotator_cuff"),
    "back": ("lats", "upper_back", "rotator_cuff"),
    "traps": ("traps", "upper_back"),
}
FALLBACK: dict[str, tuple[str, ...]] = {**training.ALIASES, "traps": ("traps",)}
# fine muscle -> the coarse one it was part of (downgrade)
COARSE: dict[str, str] = {
    "chest_upper": "chest", "chest_lower": "chest", "delts_front": "shoulders", "delts_side": "shoulders",
    "delts_rear": "shoulders", "rotator_cuff": "back", "lats": "back", "upper_back": "traps",
}  # fmt: skip


def _norm(name: str) -> str:
    return re.sub(r"[^a-zäöüß0-9]+", " ", name.lower()).strip()


def target(ex: dict) -> tuple[list[str], list[str]]:
    """(primary, secondary) the exercise trains in fine muscles."""
    lib = exercise_library.by_key()
    entry = lib.get(ex.get("library_key") or "")
    if entry is None and ex.get("garmin_name"):
        entry = next((e for e in lib.values() if e.get("garmin_name") == ex["garmin_name"]), None)
    if entry is None and ex.get("name"):
        n = _norm(ex["name"])
        entry = next(
            (
                e
                for e in lib.values()
                if n in (_norm(e["name"]), _norm(exercise_library.english_name(e["key"])))
            ),
            None,
        )
    if entry is not None:
        return list(entry["muscles"]), list(entry.get("muscles_secondary") or [])
    return training.default_muscles(ex.get("garmin_category"), ex.get("garmin_name"), ex.get("name"))


def refine(ex: dict) -> tuple[list[str], list[str]]:
    """The exercise's muscles with the coarse ones replaced (see the module)."""
    tp, ts = target(ex)
    prim: list[str] = []
    sec: list[str] = []
    for role, muscles in (("p", ex.get("muscles") or []), ("s", ex.get("muscles_secondary") or [])):
        for m in muscles:
            if m not in PARTS:
                (prim if role == "p" else sec).append(m)
                continue
            parts = [x for x in tp + ts if x in PARTS[m]]
            if not parts:
                (prim if role == "p" else sec).extend(FALLBACK[m])
            elif role == "s":
                sec.extend(parts)
            else:
                main = [x for x in parts if x in tp] or parts
                prim.extend(main)
                sec.extend(x for x in parts if x not in main)
    prim = list(dict.fromkeys(prim))
    return prim, [m for m in dict.fromkeys(sec) if m not in prim]


def coarsen(primary: list[str], secondary: list[str]) -> tuple[list[str], list[str]]:
    """Back to the coarse muscles (downgrade); the neck had none."""
    p = list(dict.fromkeys(COARSE.get(m, m) for m in primary if m != "neck"))
    return p, [m for m in dict.fromkeys(COARSE.get(m, m) for m in secondary if m != "neck") if m not in p]
