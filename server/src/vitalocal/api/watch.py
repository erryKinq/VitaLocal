"""The day for the VitaLocal watch app (Connect IQ, through Gadgetbridge): a couple of kilobytes.

A watch app has little memory and every byte crosses Bluetooth, so this carries only what the
watch shows: drinks with goal, entries and caffeine, the weight with its last 30 days, the day's
supplements by time of day and the habits. The watch writes through the regular endpoints (POST
/drinks, DELETE /drinks/{id}, POST /body, PUT /supplements/{id}/log/{day}, PUT
/habits/{id}/log/{day}) with ids it generates itself, so a replayed request from its offline queue
is a no-op.
"""

import re
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Query
from sqlalchemy import Connection, text

from vitalocal.api.days import _local_midnight, _tz
from vitalocal.api.drinks import drinks_of_day
from vitalocal.api.goals import goals_of_day
from vitalocal.api.habits import evaluate, list_habits
from vitalocal.api.supplements import day_status
from vitalocal.db import CONN
from vitalocal.metrics import CLOCK_METRICS, METRICS, display_value, hhmm

router = APIRouter(prefix="/api/v1", tags=["watch"])

NAME_MAX = 24  # the watch shows one line per item
HABIT_HISTORY_DAYS = 7  # only today's state is shown; streaks stay on the phone
ENTRIES_MAX = 8  # the day's latest drinks on the watch
WEIGHT_DAYS = 30
# Time-of-day pages for supplements, matched in the free-text timing ("Mittags & Abends")
SLOTS = (
    ("morning", r"morgen|früh|frueh|nüchtern|aufstehen"),
    ("noon", r"mittag"),
    ("evening", r"abend|nacht|schlaf"),
)


def _short(name: str) -> str:
    # plain dots: the watch's system fonts may lack "…"
    return name if len(name) <= NAME_MAX else name[: NAME_MAX - 3].rstrip() + "..."


def slots(timing: str | None) -> list[str]:
    """Time-of-day pages a supplement belongs on; none matched: "other"."""
    t = (timing or "").lower()
    return [k for k, pattern in SLOTS if re.search(pattern, t)] or ["other"]


def _weights(conn: Connection, day: date, tz) -> dict | None:
    """Latest weight and one value per day (the day's last) over the last 30 days, oldest first."""
    rows = conn.execute(
        text("""SELECT ts, value FROM (
                    SELECT ts, value FROM body_entry WHERE metric = 'weight'
                    UNION ALL SELECT ts, value FROM sample WHERE metric = 'weight'
                ) w WHERE ts < :e ORDER BY ts"""),
        {"e": _local_midnight(day + timedelta(days=1), tz)},
    ).all()
    if not rows:
        return None
    first = day - timedelta(days=WEIGHT_DAYS - 1)
    by_day: dict[date, float] = {}
    for r in rows:
        d = r.ts.astimezone(tz).date()
        if d >= first:
            by_day[d] = r.value
    last = rows[-1]
    return {
        "kg": round(last.value, 1),
        "ts": last.ts,
        # [days ago, kg] for the sparkline
        "days": [[(day - d).days, round(v, 1)] for d, v in sorted(by_day.items())],
    }


def _num(v: float, decimals: int = 0) -> str:
    """German number for the watch: 2.444 / 2,5."""
    s = f"{v:,.{decimals}f}"
    return s.replace(",", "_").replace(".", ",").replace("_", ".")


def _auto_text(h: dict, today: dict) -> tuple[str | None, float | None]:
    """The measured value against the habit's range, e.g. ("2.444 / 5.000 Schritte", 0.49)."""
    metric, value = h["metric"], today.get("value")
    if value is None:
        return None, None
    lo, hi = h["min_value"], h["max_value"]
    if metric in CLOCK_METRICS:
        bound = f"bis {hhmm(hi)}" if hi is not None else f"ab {hhmm(lo)}" if lo is not None else ""
        return f"{hhmm(value)} ({bound})".replace(" ()", ""), 1.0 if today.get("done") else 0.0
    m = METRICS.get(metric)
    dec = m.decimals if m else 0
    v = display_value(metric, value)
    if lo is not None:
        target = display_value(metric, lo)
        frac = max(0.0, min(1.0, v / target)) if target else None
        return f"{_num(v, dec)} / {_num(target, dec)}", frac
    if hi is not None:
        return f"{_num(v, dec)} (max {_num(display_value(metric, hi), dec)})", 1.0 if today.get(
            "done"
        ) else 0.0
    return _num(v, dec), None


@router.get("/watch/today")
def watch_today(
    day: date | None = Query(None, description="Local day (defaults to today in the server time zone)"),
    conn: Connection = CONN,
) -> dict:
    tz = _tz()
    day = day or datetime.now(tz).date()

    drinks = drinks_of_day(conn, day)
    caffeinated = [e for e in drinks["entries"] if e["caffeine"]]
    goal = next((g for g in goals_of_day(conn, day) if g["metric"] == "nutrition.drinks"), None)

    sups = day_status(conn, day, history_days=7)["supplements"]
    habits = evaluate(conn, list_habits(False, conn), day - timedelta(days=HABIT_HISTORY_DAYS), day, day)
    # the caffeine cut-off is a habit on the time of the day's last caffeinated drink
    cutoff = next(
        (h for h in habits if h["metric"] == "drinks.last_caffeine" and h["max_value"] is not None), None
    )

    def habit(h: dict) -> dict:
        today = h["days"].get(day.isoformat()) or {}
        out = {"id": h["id"], "name": _short(h["name"]), "done": bool(today.get("done"))}
        if h["metric"]:
            out["auto"] = True  # evaluated from a measured value: read-only on the watch
            out["text"], out["frac"] = _auto_text(h, today)
        else:
            out.update(kind=h["kind"], value=today.get("value") or 0)
            if h["kind"] == "count":
                out.update(target=h["target"], unit=h["unit"])
        return out

    return {
        "day": day,
        "drinks": {
            "ml": round(drinks["total_ml"]),
            "goal_ml": round(goal["min_value"]) if goal and goal["min_value"] else None,
            "caffeine_n": len(caffeinated),
            "last_caffeine": hhmm(_minute(caffeinated[-1]["ts"], tz)) if caffeinated else None,
            "caffeine_limit": hhmm(cutoff["max_value"]) if cutoff else None,
            "entries": [
                {
                    "id": e["id"],
                    "t": hhmm(_minute(e["ts"], tz)),
                    "drink": e["drink"],
                    "ml": round(e["ml"]),
                    "caffeine": e["caffeine"],
                }
                for e in drinks["entries"][-ENTRIES_MAX:]
            ],
        },
        "weight": _weights(conn, day, tz),
        "supplements": [
            {
                "id": s["id"],
                "name": _short(s["name"]),
                "per_day": s["per_day"],
                "count": s["count"],
                # some days a week and this week's goal is reached: nothing due today
                "rest": s["week_done"] and not s["done"],
                "slots": slots(s["timing"]),
            }
            for s in sups
        ],
        "habits": [habit(h) for h in habits],
    }


def _minute(ts: datetime, tz) -> int:
    local = ts.astimezone(tz)
    return local.hour * 60 + local.minute
