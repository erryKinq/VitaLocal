"""Habit tracking: manual check/count habits and habits derived from a daily metric.

An automatic habit is done on a day when the metric's daily value lies within `min_value` ..
`max_value` (either bound may be open). Clock metrics (bedtime, wake time, last caffeine) are
minutes after midnight and compare in the order of their own day, so 23:40 .. 00:20 is a window.
"""

import uuid
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import Connection, text

from vitalocal.api.days import _daily_values, _local_midnight, _tz
from vitalocal.db import CONN
from vitalocal.metrics import (
    CLOCK_METRICS,
    MANUAL_METRICS,
    METRICS,
    SUPPLEMENTS_TAKEN,
    WORKOUT_MINUTES,
    ZERO_WHEN_ABSENT,
    clock_position,
    hhmm,
)
from vitalocal.sleep import nights

router = APIRouter(prefix="/api/v1", tags=["habits"])

_COLS = """id, name, icon, template, kind, unit, target, per_week, metric, min_value, max_value,
           position, archived, created_at, hard_since, hard_since IS NOT NULL AS hard"""
# Joker: a daily habit earns one for every 7 days of a streak (at most 2 banked); a missed day is
# bridged automatically while one is left, so the streak goes on (the day itself does not count).
JOKER_EVERY, JOKER_MAX = 7, 2
_MAX_RANGE_DAYS = 800


class HabitIn(BaseModel):
    id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=80)
    icon: str | None = Field(None, max_length=16)  # an emoji
    template: str | None = Field(None, max_length=40)  # key of the template it was added from
    kind: Literal["check", "count"] = "check"
    unit: str | None = Field(None, max_length=20)
    target: float | None = Field(None, gt=0)
    per_week: int = Field(7, ge=1, le=7)
    metric: str | None = None
    min_value: float | None = None
    max_value: float | None = None
    # deprecated one-sided rule (API ≤ 0.2), mapped to min_value/max_value
    comparator: Literal[">=", "<="] | None = Field(None, exclude=True)
    threshold: float | None = Field(None, exclude=True)
    position: int = 0
    archived: bool = False
    hard: bool = False  # two flames per done day, from the day it was marked hard

    @model_validator(mode="after")
    def _check(self):
        self.icon = (self.icon or "").strip() or None
        if (self.comparator is None) != (self.threshold is None):
            raise ValueError("comparator and threshold go together")
        if self.comparator and self.min_value is None and self.max_value is None:
            if self.comparator == ">=":
                self.min_value = self.threshold
            else:
                self.max_value = self.threshold
        bounds = [v for v in (self.min_value, self.max_value) if v is not None]
        if self.metric is None:
            if bounds:
                raise ValueError("min_value/max_value need a metric")
            if self.kind == "count" and self.target is None:
                raise ValueError("count habits need a daily target")
            return self
        if self.metric not in METRICS:
            raise ValueError(f"unknown metric {self.metric!r}")
        if not bounds:
            raise ValueError("automatic habits need min_value and/or max_value")
        if self.metric in CLOCK_METRICS and not all(0 <= v < 1440 for v in bounds):
            raise ValueError("clock values are minutes after midnight (0–1439)")
        lo, hi = (clock_position(self.metric, v) for v in (self.min_value or 0, self.max_value or 0))
        if len(bounds) == 2 and lo > hi:
            raise ValueError("min_value must not be above max_value")
        return self


def within(metric: str, value: float, lo: float | None, hi: float | None) -> bool:
    v = clock_position(metric, value)
    return (lo is None or v >= clock_position(metric, lo)) and (hi is None or v <= clock_position(metric, hi))


def deviation(metric: str, value: float, lo: float | None, hi: float | None) -> float:
    """Distance outside the bounds: negative below (earlier), positive above (later), 0 within."""
    v = clock_position(metric, value)
    if lo is not None and v < (p := clock_position(metric, lo)):
        return v - p
    if hi is not None and v > (p := clock_position(metric, hi)):
        return v - p
    return 0


def _row(conn: Connection, habit_id) -> dict:
    row = conn.execute(text(f"SELECT {_COLS} FROM habit WHERE id = :id"), {"id": habit_id}).first()
    if row is None:
        raise HTTPException(404, "habit not found")
    return dict(row._mapping)


@router.get("/habits")
def list_habits(include_archived: bool = False, conn: Connection = CONN) -> list[dict]:
    rows = conn.execute(
        # by type (automatic, tick off, count), then by name
        text(f"""SELECT {_COLS} FROM habit WHERE archived = false OR :all
                 ORDER BY archived, CASE WHEN metric IS NOT NULL THEN 0 WHEN kind = 'check' THEN 1 ELSE 2 END,
                          lower(name), created_at"""),
        {"all": include_archived},
    )
    return [dict(r._mapping) for r in rows]


@router.get("/habits/metrics")
def habit_metrics() -> list[dict]:
    """Metrics a habit can be derived from (daily value of the metric catalog)."""
    keys = [k for k, m in METRICS.items() if m.group in ("activity", "sleep", "nutrition", "body", "heart")]
    keys = [k for k in keys if k != WORKOUT_MINUTES.key] + [WORKOUT_MINUTES.key]
    keys += [k for k in MANUAL_METRICS if k not in keys] + [SUPPLEMENTS_TAKEN.key]
    return [METRICS[k].as_dict() for k in keys]


@router.post("/habits", status_code=status.HTTP_201_CREATED)
def create_habit(h: HabitIn, conn: Connection = CONN) -> dict:
    hid = h.id or uuid.uuid4()
    conn.execute(
        text("""INSERT INTO habit (id, name, icon, template, kind, unit, target, per_week, metric,
                                   min_value, max_value, position, archived, hard_since)
                VALUES (:id, :name, :icon, :template, :kind, :unit, :target, :per_week, :metric,
                        :min_value, :max_value, :position, :archived, CASE WHEN :hard THEN :today END)
                ON CONFLICT (id) DO NOTHING"""),
        {**h.model_dump(), "id": hid, "name": h.name.strip(), "today": datetime.now(_tz()).date()},
    )
    return _row(conn, hid)


@router.put("/habits/{habit_id}")
def update_habit(habit_id: uuid.UUID, h: HabitIn, conn: Connection = CONN) -> dict:
    _row(conn, habit_id)
    conn.execute(
        text("""UPDATE habit SET name = :name, icon = :icon, template = :template, kind = :kind,
                    unit = :unit, target = :target, per_week = :per_week, metric = :metric,
                    min_value = :min_value, max_value = :max_value, position = :position,
                    archived = :archived,
                    hard_since = CASE WHEN :hard THEN coalesce(hard_since, :today) END
                WHERE id = :id"""),
        {**h.model_dump(), "id": habit_id, "name": h.name.strip(), "today": datetime.now(_tz()).date()},
    )
    return _row(conn, habit_id)


@router.delete("/habits/{habit_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_habit(habit_id: uuid.UUID, conn: Connection = CONN) -> Response:
    conn.execute(text("DELETE FROM habit WHERE id = :id"), {"id": habit_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class LogIn(BaseModel):
    value: float = Field(ge=0, le=100000)


@router.put("/habits/{habit_id}/log/{day}")
def log_habit(habit_id: uuid.UUID, day: date, body: LogIn, conn: Connection = CONN) -> dict:
    habit = _row(conn, habit_id)
    if habit["metric"]:
        raise HTTPException(422, "this habit is evaluated automatically from its metric")
    if body.value == 0:
        conn.execute(
            text("DELETE FROM habit_log WHERE habit_id = :h AND day = :d"), {"h": habit_id, "d": day}
        )
    else:
        conn.execute(
            text("""INSERT INTO habit_log (habit_id, day, value) VALUES (:h, :d, :v)
                    ON CONFLICT (habit_id, day) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"""),
            {"h": habit_id, "d": day, "v": body.value},
        )
    return {"habit_id": habit_id, "day": day, "value": body.value}


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _minute(t: datetime, tz: ZoneInfo) -> int:
    local = t.astimezone(tz)
    return local.hour * 60 + local.minute


def clock_values(conn: Connection, metrics: list[str], start: date, end: date, tz: ZoneInfo) -> dict:
    """{metric: {day: {"value": minutes after midnight | None}}} for the clock metrics.

    Bedtime counts for the evening the night began, wake time for the morning it ended. Last
    caffeine: a day with drinks but none caffeinated has value None (no caffeine at all); a day
    without any drink is unknown.
    """
    out: dict[str, dict] = {}
    if {"sleep.bedtime", "sleep.wake_time"} & set(metrics):
        bed, wake = out.setdefault("sleep.bedtime", {}), out.setdefault("sleep.wake_time", {})
        for d, n in nights(conn, tz, start, end + timedelta(days=1)).items():
            evening = d - timedelta(days=1)
            if start <= evening <= end:
                bed[evening.isoformat()] = {"value": _minute(n["bed_start"], tz)}
            if d <= end:
                wake[d.isoformat()] = {"value": _minute(n["bed_end"], tz)}
    if "drinks.last_caffeine" in metrics:
        rows = conn.execute(
            text("""SELECT (ts AT TIME ZONE :tz)::date AS day, max(ts) FILTER (WHERE caffeine) AS last
                    FROM drink WHERE ts >= :s AND ts < :e GROUP BY 1"""),
            {"tz": tz.key, "s": _local_midnight(start, tz), "e": _local_midnight(end + timedelta(1), tz)},
        )
        out["drinks.last_caffeine"] = {
            r.day.isoformat(): {"value": _minute(r.last, tz) if r.last else None} for r in rows
        }
    return out


def workout_values(conn: Connection, start: date, end: date, tz: ZoneInfo) -> dict:
    """{day: {"value": minutes of recorded activities}} (watch and manual workouts)."""
    rows = conn.execute(
        text("""SELECT (start_ts AT TIME ZONE :tz)::date AS day, sum(coalesce(elapsed_s, 0)) / 60.0 AS m
                FROM activity WHERE start_ts >= :s AND start_ts < :e GROUP BY 1"""),
        {"tz": tz.key, "s": _local_midnight(start, tz), "e": _local_midnight(end + timedelta(1), tz)},
    )
    return {r.day.isoformat(): {"value": round(float(r.m)), "n": 1} for r in rows}


def supplement_due(per_week: int, day: date, today: date, taken: set[date]) -> bool | None:
    """Whether a supplement counts on a day: True done, False missed, None not due.

    Daily ones are due every day. One taken on some days of the week counts on the days it was
    taken; if its weekly goal is not reached by Sunday, the Sunday counts as missed."""
    if per_week >= 7:
        return day in taken
    if day in taken:
        return True
    if day.weekday() == 6 and day < today:
        week = {day - timedelta(days=i) for i in range(7)}
        return False if len(week & taken) < per_week else None
    return None


def supplement_values(conn: Connection, start: date, end: date, today: date) -> dict:
    """{day: {"value": % of the due supplements taken completely}} from the first logged day on.

    A supplement counts from its start date; archived ones no longer count. Supplements taken on
    some days of the week only count as described in `supplement_due`. Days before anything was
    ever logged, and days without a due supplement, have no value (unknown).
    """
    first = conn.execute(text("SELECT min(day) FROM supplement_log")).scalar()
    if first is None:
        return {}
    sups = conn.execute(
        text("SELECT id, per_day, per_week, started_on FROM supplement WHERE NOT archived")
    ).all()
    full: dict = {}
    for r in conn.execute(
        text("""SELECT l.supplement_id, l.day
                FROM supplement_log l JOIN supplement s ON s.id = l.supplement_id
                WHERE l.day BETWEEN :s AND :e AND l.count >= s.per_day"""),
        {"s": start - timedelta(days=6), "e": end},
    ):
        full.setdefault(r.supplement_id, set()).add(r.day)
    out = {}
    d = max(start, first)
    while d <= min(end, today):
        due = [
            v
            for x in sups
            if (x.started_on is None or x.started_on <= d)
            and (v := supplement_due(x.per_week, d, today, full.get(x.id, set()))) is not None
        ]
        if due:
            out[d.isoformat()] = {"value": round(100 * sum(due) / len(due))}
        d += timedelta(days=1)
    return out


def _zero_filled(
    conn: Connection, daily: dict, metrics: list[str], start: date, end: date, tz: ZoneInfo
) -> None:
    """Days the watch counted steps but recorded no ascent get 0 for the ascent metrics."""
    worn = _daily_values(
        conn, ["steps"], _local_midnight(start, tz), _local_midnight(end + timedelta(days=1), tz)
    )
    for m in metrics:
        for iso in worn.get("steps", {}):
            daily.setdefault(m, {}).setdefault(iso, {"value": 0, "n": 0})


def evaluate(conn: Connection, habits: list[dict], start: date, end: date, today: date) -> list[dict]:
    """Per habit: {day: {value, done}} plus streak (from `tracked_from`) and recent completion.

    Manual habits: an unlogged day is simply not done. Automatic habits: a day without the metric
    has no status at all (unknown), because "no measurement" is not "missed". Automatic entries
    carry `delta` (how far outside the bounds, in the metric's unit; minutes for clock metrics)
    and, for clock metrics, `display` ("22:07").
    """
    tz = _tz()
    metrics = sorted({h["metric"] for h in habits if h["metric"]})
    derived = {SUPPLEMENTS_TAKEN.key, WORKOUT_MINUTES.key}
    plain = [m for m in metrics if m not in CLOCK_METRICS and m not in derived]
    daily = (
        _daily_values(conn, plain, _local_midnight(start, tz), _local_midnight(end + timedelta(days=1), tz))
        if plain
        else {}
    )
    if CLOCK_METRICS & set(metrics):
        daily |= clock_values(conn, metrics, start, end, tz)
    if SUPPLEMENTS_TAKEN.key in metrics:
        daily[SUPPLEMENTS_TAKEN.key] = supplement_values(conn, start, end, today)
    if WORKOUT_MINUTES.key in metrics:
        daily[WORKOUT_MINUTES.key] = workout_values(conn, start, end, tz)
        _zero_filled(conn, daily, [WORKOUT_MINUTES.key], start, end, tz)
    if zero := [m for m in plain if m in ZERO_WHEN_ABSENT]:
        _zero_filled(conn, daily, zero, start, end, tz)
    logs: dict = {}
    manual_ids = [h["id"] for h in habits if not h["metric"]]
    if manual_ids:
        for r in conn.execute(
            text("""SELECT habit_id, day, value FROM habit_log
                    WHERE habit_id = ANY(:ids) AND day BETWEEN :s AND :e"""),
            {"ids": manual_ids, "s": start, "e": end},
        ):
            logs.setdefault(r.habit_id, {})[r.day] = r.value

    out = []
    for h in habits:
        days: dict[str, dict] = {}
        if metric := h["metric"]:
            lo, hi = h["min_value"], h["max_value"]
            for iso, v in daily.get(metric, {}).items():
                value = v["value"]
                if value is None:  # e.g. no caffeinated drink that day: nothing can be too late
                    days[iso] = {"value": None, "done": True, "auto": True, "none": True}
                    continue
                entry = {
                    "value": value,
                    "done": within(metric, value, lo, hi),
                    "auto": True,
                    "delta": deviation(metric, value, lo, hi),
                }
                if metric in CLOCK_METRICS:
                    entry["display"] = hhmm(value)
                days[iso] = entry
        else:
            for d, value in logs.get(h["id"], {}).items():
                done = value >= (h["target"] or 1) if h["kind"] == "count" else value >= 1
                days[d.isoformat()] = {"value": value, "done": done}
        out.append({**h, "days": days, **_streaks(h, days, today, max(start, tracked_from(h, tz)))})
    return out


def day_timeline(done: set[date], known: set[date] | None, first: date, today: date) -> dict:
    """Daily streaks from `first` to `today` with jokers.

    `known`: days with a status (automatic habits); other days are neutral, neither done nor
    missed (no measurement is not a miss). None: every day is known (manual habits). Today never
    breaks a streak while it is still open. Returns the current streak (done days of the running
    streak), jokers left, the bridged days, all runs (first day, last day, done days) and the day
    each streak length was first reached.
    """
    run, run_first, jokers = 0, None, 0
    bridged: list[date] = []
    runs: list[tuple[date, date, int]] = []
    reached: dict[int, date] = {}
    last_done = None
    d = first
    while d <= today:
        if d in done:
            if run == 0:
                run_first = d
            run += 1
            last_done = d
            reached.setdefault(run, d)
            if run % JOKER_EVERY == 0:
                jokers = min(jokers + 1, JOKER_MAX)
        elif d != today and (known is None or d in known) and run:
            if jokers:
                jokers -= 1
                bridged.append(d)
            else:
                runs.append((run_first, last_done, run))
                run = 0
        d += timedelta(days=1)
    if run:
        runs.append((run_first, last_done, run))
    return {"streak": run, "jokers": jokers, "bridged": bridged, "runs": runs, "reached": reached}


def tracked_from(h: dict, tz: ZoneInfo) -> date:
    """First day that counts for streaks, flames and badges.

    Automatic habits are evaluated back into the past (the history shows that), but a streak is
    only earned from the day the habit was created. Manual habits count from their first log.
    """
    return h["created_at"].astimezone(tz).date() if h["metric"] else date.min


def _streaks(h: dict, days: dict, today: date, start: date, jokers: bool = True) -> dict:
    done = {date.fromisoformat(k) for k, v in days.items() if v["done"]}
    last30 = sum(1 for d in done if d > today - timedelta(days=30))
    if h["per_week"] >= 7 and not jokers:
        d = today if today in done else today - timedelta(days=1)
        n = 0
        while d in done and d >= start:
            n, d = n + 1, d - timedelta(days=1)
        return {"streak": n, "streak_unit": "days", "done_last_30": last30}
    if h["per_week"] >= 7:
        known = {date.fromisoformat(k) for k in days} if h["metric"] else None
        tl = day_timeline({d for d in done if d >= start}, known, start, today)
        for d in tl["bridged"]:  # shown as joker days
            days.setdefault(d.isoformat(), {"value": None, "done": False})["joker"] = True
        return {
            "streak": tl["streak"],
            "streak_unit": "days",
            "done_last_30": last30,
            "jokers": tl["jokers"],
        }
    # N times per week: count consecutive weeks meeting the goal (this week only once it is met)
    week = _week_start(today)
    this_week = sum(1 for d in done if _week_start(d) == week)
    if this_week < h["per_week"]:
        week -= timedelta(days=7)
    n = 0
    while week >= _week_start(start) and sum(1 for d in done if _week_start(d) == week) >= h["per_week"]:
        n, week = n + 1, week - timedelta(days=7)
    return {"streak": n, "streak_unit": "weeks", "done_last_30": last30, "this_week": this_week}


@router.get("/habits/status")
def habit_status(
    from_: date = Query(alias="from"),
    to: date = Query(),
    today: date | None = Query(None, description="Reference day for streaks (defaults to `to`)"),
    conn: Connection = CONN,
) -> list[dict]:
    if to < from_ or (to - from_).days > _MAX_RANGE_DAYS:
        raise HTTPException(422, f"range must be 1..{_MAX_RANGE_DAYS} days")
    habits = list_habits(False, conn)
    return evaluate(conn, habits, from_, to, today or to)
