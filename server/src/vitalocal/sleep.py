"""Nights as a whole: bed and wake time per night, shared by insights, habits and AI facts.

A night belongs to the morning it ends (window: previous evening 18:00 until 14:00), like the day
view. Bed and wake time are the start of the first and the end of the last hypnogram segment,
i.e. the sleep window the watch recorded, including a final awake stage.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, text

from vitalocal.api.days import SLEEP_WINDOW, sleep_starts, stage_segments

_ASLEEP = {"light", "deep", "rem"}


def nights(conn: Connection, tz: ZoneInfo, first: date, last: date) -> dict[date, dict]:
    """Per day (= the morning the night ends): bed start/end and minutes asleep.

    Same night window and segments as the day view (previous evening 18:00 until 14:00).
    """
    window = (
        datetime.combine(first - timedelta(days=1), SLEEP_WINDOW[0], tzinfo=tz),
        datetime.combine(last, SLEEP_WINDOW[1], tzinfo=tz),
    )
    rows = conn.execute(
        text("""SELECT ts, value FROM sample WHERE metric = 'sleep_stage_code'
                AND ts >= :s AND ts < :e ORDER BY ts"""),
        {"s": window[0], "e": window[1]},
    ).all()
    starts = sleep_starts(conn, *window)

    def night_of(t: datetime) -> date | None:
        local = t.astimezone(tz)
        # 18:00 belongs to the next day's night; 14:00 until 18:00 is outside every window
        if SLEEP_WINDOW[1] <= local.time() < SLEEP_WINDOW[0]:
            return None
        return local.date() + timedelta(days=1) if local.time() >= SLEEP_WINDOW[0] else local.date()

    by_day: dict[date, list] = {}
    for ts, code in rows:
        if (d := night_of(ts)) is not None:
            by_day.setdefault(d, []).append((ts, code))
    out = {}
    for d, stages in by_day.items():
        segs = stage_segments(stages, [s for s in starts if night_of(s) == d])
        asleep = sum((s["end"] - s["start"]).total_seconds() / 60 for s in segs if s["stage"] in _ASLEEP)
        if not segs or asleep <= 0:
            continue
        out[d] = {
            "bed_start": segs[0]["start"].astimezone(tz),
            "bed_end": segs[-1]["end"].astimezone(tz),
            "asleep_minutes": round(asleep),
        }
    return out


def noon_minutes(t: datetime) -> int:
    """Minutes since the previous 12:00, so that 23:30 and 00:30 average to midnight, not noon."""
    return (t.hour * 60 + t.minute - 720) % 1440


def clock(noon_minutes: float | None) -> str | None:
    if noon_minutes is None:
        return None
    m = round(noon_minutes + 720) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"
