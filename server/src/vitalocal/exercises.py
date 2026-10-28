"""Exercise catalog from the Garmin FIT profile (categories and their specific exercise names)."""

from functools import cache

from fitdecode import profile

UNKNOWN_CATEGORY = 65534


@cache
def categories() -> dict[int, str]:
    return {k: v for k, v in profile.FIELD_TYPES["exercise_category"].enum.items() if k != UNKNOWN_CATEGORY}


@cache
def names(category: str) -> dict[int, str]:
    t = profile.FIELD_TYPES.get(f"{category}_exercise_name")
    return dict(t.enum) if t else {}


def catalog() -> list[dict]:
    return [{"category": c, "names": sorted(names(c).values())} for c in sorted(categories().values())]


def is_category(category: str) -> bool:
    return category in categories().values()


def is_name(category: str, name: str) -> bool:
    return name in names(category).values()


def name_for(category: str | None, subtype_candidates: list | None) -> str | None:
    """Specific exercise name recorded by the watch for the first candidate, if any."""
    if not category or not subtype_candidates or subtype_candidates[0] is None:
        return None
    return names(category).get(subtype_candidates[0])
