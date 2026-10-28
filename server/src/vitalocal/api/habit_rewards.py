"""Gamification for habits: templates, flames, levels, records and achievements.

Every day a habit is done earns one flame (a count habit only once its target is reached). Flames,
records and achievements count from the day a habit is tracked (`tracked_from`), archived habits
included, so archiving never takes anything away. Unlocked achievements are stored with the day
they were earned and stay unlocked even if a log is removed later; `seen` drives the "new" notice.

Titles and descriptions are English source strings with `{placeholders}`; clients translate them.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import Connection, text

from vitalocal.api.days import _tz
from vitalocal.api.habits import _week_start, day_timeline, evaluate, list_habits, tracked_from
from vitalocal.db import CONN

router = APIRouter(prefix="/api/v1", tags=["habits"])


# --- templates ------------------------------------------------------------------------------------


def _tpl(key, name, icon, category, why, **rule) -> dict:
    return {
        "key": key,
        "name": name,
        "icon": icon,
        "category": category,
        "why": why,
        "kind": "check",
        "unit": None,
        "target": None,
        "per_week": 7,
        "metric": None,
        "min_value": None,
        "max_value": None,
    } | rule


TEMPLATES = [
    # movement
    _tpl("stretch", "Stretch", "🤸", "Movement", "Ten minutes keep you mobile"),
    _tpl("walk", "Walk outside", "🚶", "Movement", "Daylight and movement", per_week=5),
    _tpl(
        "workout",
        "Workout",
        "🏋️",
        "Movement",
        "Evaluated from the watch and your training log",
        per_week=3,
        metric="activities.minutes",
        min_value=20,
    ),
    _tpl(
        "pushups",
        "Push-ups",
        "💪",
        "Movement",
        "A small daily strength set",
        kind="count",
        unit="reps",
        target=20,
    ),
    _tpl(
        "steps_10k",
        "10,000 steps",
        "👟",
        "Movement",
        "Evaluated from the watch",
        metric="steps",
        min_value=10000,
    ),
    _tpl(
        "floors_10",
        "Climb 10 floors",
        "🪜",
        "Movement",
        "Evaluated from the watch",
        metric="floors_climbed",
        min_value=10,
    ),
    # mind
    _tpl("meditate", "Meditate", "🧘", "Mind", "Calm the mind for a few minutes"),
    _tpl("journal", "Write a journal", "✍️", "Mind", "Sort out the day"),
    _tpl("gratitude", "Three good things", "🙏", "Mind", "Note three things that went well"),
    _tpl("read", "Read", "📖", "Mind", "A few pages every day", kind="count", unit="pages", target=10),
    _tpl("learn", "Learn something new", "🧠", "Mind", "A language, an instrument, a skill", per_week=5),
    _tpl("breathe", "Breathing exercise", "🌬️", "Mind", "Box breathing lowers stress"),
    _tpl("call", "Call friends or family", "📞", "Mind", "Relationships keep you healthy", per_week=2),
    # sleep
    _tpl("no_screens", "No screens before bed", "📵", "Sleep", "The last hour without a display"),
    _tpl("morning_light", "Morning daylight", "☀️", "Sleep", "Sets the inner clock"),
    _tpl(
        "bed_by_23",
        "In bed by 23:00",
        "🛏️",
        "Sleep",
        "Evaluated from the watch",
        metric="sleep.bedtime",
        max_value=23 * 60,
    ),
    _tpl(
        "in_bed_7h30",
        "7½ hours in bed",
        "😴",
        "Sleep",
        "Evaluated from the watch",
        metric="sleep_in_bed",
        min_value=450,
    ),
    # nutrition
    _tpl(
        "veggies",
        "Fruit & vegetables",
        "🥦",
        "Nutrition",
        "Five portions a day",
        kind="count",
        unit="portions",
        target=5,
    ),
    _tpl("no_sweets", "No sweets", "🍬", "Nutrition", "No sweets or sugary drinks"),
    _tpl("no_alcohol", "No alcohol", "🚱", "Nutrition", "Better sleep and recovery"),
    _tpl("no_late_snack", "No snacks after dinner", "🌙", "Nutrition", "Give digestion a break"),
    _tpl(
        "drink_2l",
        "Drink 2 litres",
        "💧",
        "Nutrition",
        "Evaluated from your drinks",
        metric="nutrition.drinks",
        min_value=2000,
    ),
    _tpl(
        "caffeine_cutoff",
        "No caffeine after 14:00",
        "☕",
        "Nutrition",
        "Evaluated from your drinks",
        metric="drinks.last_caffeine",
        max_value=14 * 60,
    ),
    # everyday
    _tpl("floss", "Floss", "🦷", "Everyday", "Takes one minute"),
    _tpl("cold_shower", "Cold shower", "🚿", "Everyday", "Wakes you up"),
    _tpl("tidy", "Tidy up for 10 minutes", "🧹", "Everyday", "A tidy flat, a clear head"),
    _tpl("plan", "Plan tomorrow", "🗒️", "Everyday", "Three priorities for the next day"),
    _tpl(
        "supplements",
        "Take supplements",
        "💊",
        "Everyday",
        "Evaluated from your supplements",
        metric="supplements.taken",
        min_value=100,
    ),
]


@router.get("/habits/templates")
def habit_templates(conn: Connection = CONN) -> list[dict]:
    """Predefined habits; `added` when an active habit was created from the template."""
    used = set(conn.execute(text("SELECT template FROM habit WHERE NOT archived")).scalars())
    return [{**t, "added": t["key"] in used} for t in TEMPLATES]


# --- levels ---------------------------------------------------------------------------------------

# (from level, title): a title holds until the next one
LEVEL_TITLES = [
    (1, "Spark"),
    (3, "Ember"),
    (6, "Flame"),
    (10, "Blaze"),
    (15, "Bonfire"),
    (20, "Beacon"),
    (30, "Firestorm"),
    (40, "Phoenix"),
]


def level_floor(n: int) -> int:
    """Flames needed to reach level n: 0, 10, 30, 60, 100, … (each level 10 more than the last)."""
    return 5 * n * (n - 1)


def level_of(flames: int) -> dict:
    n = 1
    while level_floor(n + 1) <= flames:
        n += 1
    lo, hi = level_floor(n), level_floor(n + 1)
    return {
        "level": n,
        "title": next(t for start, t in reversed(LEVEL_TITLES) if n >= start),
        "flames": flames,
        "floor": lo,
        "next": hi,
        "progress": round((flames - lo) / (hi - lo), 3),
    }


# --- achievements ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Tier:
    n: int
    tier: str  # bronze, silver, gold, diamond (colour of the badge)
    title: str
    icon: str


FLAME_TIERS = [
    Tier(1, "bronze", "First flame", "🕯️"),
    Tier(10, "bronze", "Kindling", "🔥"),
    Tier(50, "silver", "Campfire", "🏕️"),
    Tier(100, "silver", "Hundred flames", "💯"),
    Tier(250, "gold", "Torch bearer", "🔦"),
    Tier(500, "gold", "Wildfire", "🌋"),
    Tier(1000, "diamond", "Thousand flames", "☄️"),
    Tier(2500, "diamond", "Eternal flame", "☀️"),
]
PERFECT_DAY_TIERS = [
    Tier(1, "bronze", "Perfect day", "⭐"),
    Tier(10, "silver", "Ten perfect days", "🌟"),
    Tier(50, "gold", "Fifty perfect days", "✨"),
    Tier(100, "diamond", "Hundred perfect days", "🌠"),
]
PERFECT_STREAK_TIERS = [
    Tier(3, "bronze", "Hat trick", "🎩"),
    Tier(7, "silver", "Perfect week", "🏆"),
    Tier(30, "diamond", "Perfect month", "👑"),
]
DAY_FLAME_TIERS = [Tier(5, "bronze", "High five", "🖐️"), Tier(10, "gold", "Perfect ten", "🎯")]
JUGGLER_TIERS = [Tier(3, "silver", "Juggler", "🤹"), Tier(5, "gold", "Ringmaster", "🎪")]
# Per habit. 66 days: the median time a new habit took to become automatic (Lally et al. 2010).
DAY_STREAK_TIERS = [
    Tier(3, "bronze", "Off to a good start", "🌱"),
    Tier(7, "bronze", "One week", "🥉"),
    Tier(21, "silver", "Three weeks", "🥈"),
    Tier(66, "gold", "Habit anchored", "🥇"),
    Tier(100, "diamond", "Hundred days", "💎"),
    Tier(365, "diamond", "One year", "👑"),
]
WEEK_STREAK_TIERS = [
    Tier(2, "bronze", "Off to a good start", "🌱"),
    Tier(4, "bronze", "One month", "🥉"),
    Tier(8, "silver", "Two months", "🥈"),
    Tier(13, "gold", "Habit anchored", "🥇"),
    Tier(26, "diamond", "Half a year", "💎"),
    Tier(52, "diamond", "One year", "👑"),
]
HABIT_FLAME_TIERS = [
    Tier(30, "bronze", "Regular", "🔥"),
    Tier(100, "silver", "Devoted", "❤️‍🔥"),
    Tier(365, "gold", "A year of flames", "🏅"),
]
CHALLENGE_TIERS = [
    Tier(1, "bronze", "Challenge accepted", "🎟️"),
    Tier(10, "silver", "Challenger", "🏹"),
    Tier(50, "gold", "Champion", "🎖️"),
]
ROUTINE_TIERS = [Tier(1, "bronze", "First routine", "🔗"), Tier(30, "gold", "Ritual", "📿")]
SINGLE = {
    "comeback": Tier(1, "silver", "Comeback", "🔄"),
    "perfect_weekend": Tier(1, "silver", "Weekend warrior", "🏖️"),
    "clean_sweep": Tier(1, "silver", "Clean sweep", "✅"),
    "routine_streak": Tier(7, "silver", "Like clockwork", "⏰"),
    "joker": Tier(1, "bronze", "Saved by the joker", "🃏"),
    "garden_bloom": Tier(21, "bronze", "In full bloom", "🌻"),
    "garden_tree": Tier(66, "silver", "Green thumb", "🌳"),
}
# Ranks of a series' steps, from the first to the last (colours of the badges).
RANKS = ["bronze", "silver", "gold", "platinum", "diamond", "master", "grandmaster", "legend"]
JUGGLER_STREAK = 7  # a habit counts for the juggler badges from this many days in a row
COMEBACK_GAP, COMEBACK_STREAK = 3, 3

# --- garden ---------------------------------------------------------------------------------------
# Every active habit is a plant. Growth: +1 per done day (weekly habits: 7 / per_week), −3 per
# missed day (weekly: −7 per missed week), never below 0; joker days and days without data change
# nothing. Health from the misses since the last done (or joker) day: 0 healthy, 1 thirsty, 2+ wilting.
PLANT_STAGES = [
    (0, "seed", "🌰", "Seed"),
    (1, "sprout", "🌱", "Sprout"),
    (3, "seedling", "🌿", "Seedling"),
    (7, "bush", "🪴", "Bush"),
    (21, "bloom", "🌻", "In bloom"),
    (66, "tree", "🌳", "Tree"),
    (100, "mighty", "🌲", "Mighty tree"),
]
MISS_COST, WEEK_MISS_COST = 3, 7

# bonus flames
ROUTINE_BONUS_PER_HABIT = 1
CHALLENGE_BONUS = {"habit": 15, "perfect": 20, "routine": 15, "flames": 10}
# a habit done on fewer days than this share (of its goal) over 4 weeks is suggested as hard
HARD_SUGGEST_RATE, HARD_SUGGEST_MIN_DAYS = 0.4, 14


def _first_reach(dated_counts: list[tuple[date, int]], n: int) -> date | None:
    """First day a running value reached n."""
    return next((d for d, v in dated_counts if v >= n), None)


def _cumulative(by_day: dict[date, int]) -> list[tuple[date, int]]:
    running, out = 0, []
    for d in sorted(by_day):
        running += by_day[d]
        out.append((d, running))
    return out


def _runs(days: list[date]) -> list[tuple[date, date]]:
    """Consecutive runs (first, last) of sorted days."""
    runs: list[tuple[date, date]] = []
    for d in days:
        if runs and d - runs[-1][1] == timedelta(days=1):
            runs[-1] = (runs[-1][0], d)
        else:
            runs.append((d, d))
    return runs


def _current_run(days: set[date], today: date) -> int:
    """Consecutive days up to today (or yesterday while today is still open)."""
    n, d = 0, today if today in days else today - timedelta(days=1)
    while d in days:
        n, d = n + 1, d - timedelta(days=1)
    return n


def _week_runs(done: list[date], per_week: int) -> list[tuple[date, date, date]]:
    """Runs of consecutive weeks meeting the goal: (first Monday, last Monday, day the last was met).

    A week counts on the day its goal is reached, so the running week can already count."""
    met: dict[date, date] = {}
    by_week: dict[date, list[date]] = {}
    for d in done:
        by_week.setdefault(_week_start(d), []).append(d)
    for w, ds in by_week.items():
        if len(ds) >= per_week:
            met[w] = sorted(ds)[per_week - 1]
    runs: list[tuple[date, date, date]] = []
    for w in sorted(met):
        if runs and w - runs[-1][1] == timedelta(days=7):
            runs[-1] = (runs[-1][0], w, met[w])
        else:
            runs.append((w, w, met[w]))
    return runs


def _known(h: dict, start: date, today: date) -> set[date] | None:
    """Days with a status for automatic habits (None: manual, every day counts)."""
    if not h["metric"]:
        return None
    return {d for iso in h["days"] if start <= (d := date.fromisoformat(iso)) <= today}


def _streak_stats(h: dict, done: list[date], start: date, today: date) -> dict:
    """Current and best streak (daily: with jokers), and the day each streak tier was first reached."""
    if h["per_week"] >= 7:
        tl = day_timeline(set(done), _known(h, start, today), start, today)
        best_run = max(tl["runs"], key=lambda r: (r[2], r[1]), default=None)
        return {
            "unit": "days",
            "tiers": DAY_STREAK_TIERS,
            "current": tl["streak"],
            "jokers": tl["jokers"],
            "bridged": tl["bridged"],
            "runs": tl["runs"],
            "best": best_run[2] if best_run else 0,
            "best_from": best_run[0] if best_run else None,
            "best_to": best_run[1] if best_run else None,
            "reached": {t.n: tl["reached"][t.n] for t in DAY_STREAK_TIERS if t.n in tl["reached"]},
        }
    reached: dict[int, date] = {}
    best, best_run, current = 0, None, 0
    by_week: dict[date, list[date]] = {}
    for d in done:
        by_week.setdefault(_week_start(d), []).append(d)
    this_week = _week_start(today)
    for first, last, last_met in _week_runs(done, h["per_week"]):
        length = (last - first).days // 7 + 1
        for t in WEEK_STREAK_TIERS:
            if length >= t.n and t.n not in reached:
                week = first + timedelta(days=7 * (t.n - 1))
                reached[t.n] = sorted(by_week[week])[h["per_week"] - 1]
        if length > best:
            best, best_run = length, (first, last_met)
        if last in (this_week, this_week - timedelta(days=7)):
            current = length
    return {
        "unit": "weeks",
        "tiers": WEEK_STREAK_TIERS,
        "current": current,
        "jokers": 0,
        "bridged": [],
        "runs": [],
        "best": best,
        "best_from": best_run[0] if best_run else None,
        "best_to": best_run[1] if best_run else None,
        "reached": reached,
    }


def _plant(growth: float) -> dict:
    i = max(i for i, st in enumerate(PLANT_STAGES) if growth >= st[0])
    _, key, icon, title = PLANT_STAGES[i]
    nxt = PLANT_STAGES[i + 1] if i + 1 < len(PLANT_STAGES) else None
    return {
        "stage": key,
        "stage_index": i,
        "plant": icon,
        "stage_title": title,
        "growth": round(growth, 1),
        "floor": PLANT_STAGES[i][0],
        "next_at": nxt[0] if nxt else None,
        "next_plant": nxt[2] if nxt else None,
    }


def _garden(h: dict, done: set[date], st: dict, start: date, today: date) -> tuple[dict, dict[int, date]]:
    """The habit's plant today, and the first day its growth reached each stage threshold."""
    growth, reached, misses = 0.0, {}, 0
    if h["per_week"] >= 7:
        known, bridged = _known(h, start, today), set(st["bridged"])
        d = start
        while d <= today:
            if d in done:
                growth, misses = growth + 1, 0
            elif d in bridged:
                misses = 0
            elif d != today and (known is None or d in known):
                growth, misses = max(0.0, growth - MISS_COST), misses + 1
            for n, *_ in PLANT_STAGES:
                if growth >= n and n not in reached:
                    reached[n] = d
            d += timedelta(days=1)
    else:
        step = 7 / h["per_week"]
        by_week: dict[date, int] = {}
        for d in done:
            by_week[_week_start(d)] = by_week.get(_week_start(d), 0) + 1
        d = start
        while d <= today:
            if d in done:
                growth += step
            if (
                d.weekday() == 6
                and d < today
                and _week_start(d) >= _week_start(start)
                and d - start >= timedelta(6)
            ):
                if by_week.get(_week_start(d), 0) >= h["per_week"]:
                    misses = 0
                else:
                    growth, misses = max(0.0, growth - WEEK_MISS_COST), misses + 1
            for n, *_ in PLANT_STAGES:
                if growth >= n and n not in reached:
                    reached[n] = d
            d += timedelta(days=1)
    if not done:  # a seed that never got water does not wilt, it waits
        misses = min(misses, 1)
    health = "healthy" if misses == 0 else "thirsty" if misses == 1 else "wilting"
    return {**_plant(growth), "health": health, "misses": misses}, reached


def _suggest_hard(h: dict, done: list[date], start: date, today: date) -> bool:
    if h["hard"] or h["archived"] or (today - start).days < HARD_SUGGEST_MIN_DAYS:
        return False
    first = max(start, today - timedelta(days=27))
    days = (today - first).days + 1
    goal = days * h["per_week"] / 7
    return sum(1 for d in done if d >= first) < HARD_SUGGEST_RATE * goal


def _routines(conn: Connection, done_sets: dict, tz, today: date) -> list[dict]:
    rows = conn.execute(
        text("""SELECT id, name, icon, habit_ids, position, archived, created_at FROM habit_routine
                ORDER BY archived, position, lower(name)""")
    ).all()
    out = []
    for r in rows:
        ids = [i for i in r.habit_ids if i in done_sets]
        first = r.created_at.astimezone(tz).date()
        days = set.intersection(*(done_sets[i] for i in ids)) if ids else set()
        days = {d for d in days if first <= d <= today}
        out.append(
            {
                "id": r.id,
                "name": r.name,
                "icon": r.icon,
                "habit_ids": list(r.habit_ids),
                "position": r.position,
                "archived": r.archived,
                "since": first,
                "bonus": ROUTINE_BONUS_PER_HABIT * len(r.habit_ids),
                "days": sorted(days),
                "completions": len(days),
                "streak": _current_run(days, today),
                "best_streak": max(((b - a).days + 1 for a, b in _runs(sorted(days))), default=0),
                "today": [i for i in ids if today in done_sets[i]],
                "done_today": today in days,
            }
        )
    return out


def _challenges(
    conn, today, habits_info, done_sets, perfect_days, habit_flames, routines, known
) -> list[dict]:
    """This week's challenges (created on the first look, from last week's numbers) and the
    progress of every stored challenge; completion is kept with the day it happened."""
    week = _week_start(today)
    if not conn.execute(text("SELECT 1 FROM habit_challenge WHERE week = :w"), {"w": week}).first():
        for c in _new_challenges(
            week, today, habits_info, done_sets, perfect_days, habit_flames, routines, known
        ):
            conn.execute(
                text("""INSERT INTO habit_challenge (week, key, kind, habit_id, routine_id, target, bonus)
                        VALUES (:week, :key, :kind, :habit_id, :routine_id, :target, :bonus)
                        ON CONFLICT DO NOTHING"""),
                {"week": week, **c},
            )
    rows = conn.execute(text("SELECT * FROM habit_challenge ORDER BY week, key")).all()
    pset = set(perfect_days)
    routine_days = {r["id"]: set(r["days"]) for r in routines}
    names = {h["id"]: h for h in habits_info}
    rnames = {r["id"]: r for r in routines}
    out = []
    for c in rows:
        end = min(c.week + timedelta(days=6), today)
        per_day: dict[date, int] = {}
        d = c.week
        while d <= end:
            if c.kind == "habit":
                v = int(d in done_sets.get(c.habit_id, set()))
            elif c.kind == "perfect":
                v = int(d in pset)
            elif c.kind == "routine":
                v = int(d in routine_days.get(c.routine_id, set()))
            else:
                v = habit_flames.get(d, 0)
            if v:
                per_day[d] = v
            d += timedelta(days=1)
        cum = _cumulative(per_day)
        progress = cum[-1][1] if cum else 0
        completed = c.completed_on
        if completed is None and (reached := _first_reach(cum, c.target)):
            completed = reached
            conn.execute(
                text("UPDATE habit_challenge SET completed_on = :d WHERE week = :w AND key = :k"),
                {"d": reached, "w": c.week, "k": c.key},
            )
        habit = names.get(c.habit_id)
        routine = rnames.get(c.routine_id)
        out.append(
            {
                "week": c.week,
                "key": c.key,
                "kind": c.kind,
                "habit_id": c.habit_id,
                "habit": habit["name"] if habit else None,
                "habit_icon": habit["icon"] if habit else None,
                "routine_id": c.routine_id,
                "routine": routine["name"] if routine else None,
                "target": c.target,
                "bonus": c.bonus,
                "progress": c.target if completed else min(progress, c.target),
                "completed_on": completed,
            }
        )
    return out


def _new_challenges(
    week, today, habits_info, done_sets, perfect_days, habit_flames, routines, known
) -> list[dict]:
    prev = [week - timedelta(days=i) for i in range(7, 0, -1)]
    out = []
    active = [h for h in habits_info if not h["archived"]]
    candidates = []
    for h in active:
        if h["metric"] and not known.get(h["id"]):
            continue  # an automatic habit without data last week cannot be judged
        goal = min(7, h["per_week"])
        last = sum(1 for d in prev if d in done_sets[h["id"]])
        if last >= goal:
            continue
        target = min(goal, max(2, last + 2))
        candidates.append((last / goal, h["name"].lower(), h, target))
    if candidates:
        _, _, h, target = min(candidates, key=lambda c: (c[0], c[1]))
        out.append(
            {
                "key": "habit",
                "kind": "habit",
                "habit_id": h["id"],
                "routine_id": None,
                "target": target,
                "bonus": CHALLENGE_BONUS["habit"],
            }
        )
    if any(h["per_week"] >= 7 for h in active):
        last = sum(1 for d in prev if d in set(perfect_days))
        out.append(
            {
                "key": "perfect",
                "kind": "perfect",
                "habit_id": None,
                "routine_id": None,
                "target": min(7, max(1, last + 1)),
                "bonus": CHALLENGE_BONUS["perfect"],
            }
        )
    live = [r for r in routines if not r["archived"]]
    if live:
        r = min(live, key=lambda r: (sum(1 for d in prev if d in set(r["days"])), r["name"].lower()))
        last = sum(1 for d in prev if d in set(r["days"]))
        out.append(
            {
                "key": "routine",
                "kind": "routine",
                "habit_id": None,
                "routine_id": r["id"],
                "target": min(7, max(2, last + 2)),
                "bonus": CHALLENGE_BONUS["routine"],
            }
        )
    elif active:
        last = sum(habit_flames.get(d, 0) for d in prev)
        out.append(
            {
                "key": "flames",
                "kind": "flames",
                "habit_id": None,
                "routine_id": None,
                "target": max(10, -(-round(last * 1.15) // 5) * 5),
                "bonus": CHALLENGE_BONUS["flames"],
            }
        )
    return out


def compute(conn: Connection, today: date) -> dict:
    tz = _tz()
    habits = list_habits(True, conn)
    first_logs = dict(conn.execute(text("SELECT habit_id, min(day) FROM habit_log GROUP BY 1")).all())
    starts = {}
    for h in habits:
        created = h["created_at"].astimezone(tz).date()
        starts[h["id"]] = (
            tracked_from(h, tz) if h["metric"] else min(created, first_logs.get(h["id"], created))
        )
    start = min(min(starts.values(), default=today), today)
    evaluated = evaluate(conn, habits, start, today, today) if habits else []

    week = _week_start(today)
    per_habit, achieved, garden = [], [], []  # achieved: (key, habit_id, day, Tier, group, params)
    habit_flames: dict[date, int] = {}  # flames from habits (hard habits count twice)
    done_sets: dict = {}
    juggling: dict[date, int] = {}
    comebacks, joker_days = [], []
    stage_reached: dict[int, list[date]] = {}
    for h in evaluated:
        s = starts[h["id"]]
        done = sorted(
            d for iso, v in h["days"].items() if v["done"] and s <= (d := date.fromisoformat(iso)) <= today
        )
        done_sets[h["id"]] = set(done)
        weights = {d: 2 if h["hard_since"] and d >= h["hard_since"] else 1 for d in done}
        for d, w in weights.items():
            habit_flames[d] = habit_flames.get(d, 0) + w
        flames = sum(weights.values())
        st = _streak_stats(h, done, s, today)
        for n, d in st["reached"].items():
            t = next(t for t in st["tiers"] if t.n == n)
            achieved.append(
                (f"habit:{h['id']}:streak:{n}", h["id"], d, t, "streak", {"n": n, "unit": st["unit"]})
            )
        for t in HABIT_FLAME_TIERS:
            if d := _first_reach(_cumulative(weights), t.n):
                achieved.append((f"habit:{h['id']}:flames:{t.n}", h["id"], d, t, "flames", {"n": t.n}))
        for i, (first, last, _n) in enumerate(st["runs"]):
            run_done = [d for d in done if first <= d <= last]
            for d in run_done[JUGGLER_STREAK - 1 :]:
                juggling[d] = juggling.get(d, 0) + 1
            if (
                i
                and (first - st["runs"][i - 1][1]).days - 1 >= COMEBACK_GAP
                and len(run_done) >= COMEBACK_STREAK
            ):
                comebacks.append(run_done[COMEBACK_STREAK - 1])
        joker_days += st["bridged"]
        current = st["current"] if not h["archived"] else 0
        info = {
            "id": h["id"],
            "name": h["name"],
            "icon": h["icon"],
            "archived": h["archived"],
            "per_week": h["per_week"],
            "metric": h["metric"],
            "hard": h["hard"],
            "hard_since": h["hard_since"],
            "tracked_from": s,
            "flames": flames,
            "flames_week": sum(w for d, w in weights.items() if d >= week),
            "streak": current,
            "jokers": st["jokers"] if not h["archived"] else 0,
            "best_streak": max(st["best"], current),
            "unit": st["unit"],
            "best_from": st["best_from"],
            "best_to": st["best_to"],
            "tier": _tier_dict(next((t for t in reversed(st["tiers"]) if st["best"] >= t.n), None)),
            "next_tier": _tier_dict(next((t for t in st["tiers"] if st["best"] < t.n), None)),
            "suggest_hard": _suggest_hard(h, done, s, today),
        }
        if not h["archived"]:
            plant, reached = _garden(h, set(done), st, s, today)
            info["plant"] = plant
            garden.append({"id": h["id"], "name": h["name"], "icon": h["icon"], **plant})
            for n, d in reached.items():
                stage_reached.setdefault(n, []).append(d)
        per_habit.append(info)

    perfect = _perfect_days(evaluated, starts, done_sets, start, today)
    pdays = perfect["days"]

    # bonus flames: complete routines and finished challenges
    routines = _routines(conn, done_sets, tz, today)
    bonus: dict[date, int] = {}
    for r in routines:
        for d in r["days"]:
            bonus[d] = bonus.get(d, 0) + r["bonus"]
    week_before = {week - timedelta(days=i) for i in range(1, 8)}
    known_last_week = {
        h["id"]: any(date.fromisoformat(k) in week_before for k in h["days"]) for h in evaluated
    }
    challenges = _challenges(
        conn, today, per_habit, done_sets, pdays, habit_flames, routines, known_last_week
    )
    for c in challenges:
        if c["completed_on"]:
            bonus[c["completed_on"]] = bonus.get(c["completed_on"], 0) + c["bonus"]
    flames_by_day = dict(habit_flames)
    for d, n in bonus.items():
        flames_by_day[d] = flames_by_day.get(d, 0) + n

    # across habits
    total = sum(flames_by_day.values())
    cum = _cumulative(flames_by_day)
    for t in FLAME_TIERS:
        if d := _first_reach(cum, t.n):
            achieved.append((f"flames:{t.n}", None, d, t, "flames", {"n": t.n}))
    for t in DAY_FLAME_TIERS:
        if d := min((d for d, n in habit_flames.items() if n >= t.n), default=None):
            achieved.append((f"day_flames:{t.n}", None, d, t, "day", {"n": t.n}))
    for t in JUGGLER_TIERS:
        if d := min((d for d, n in juggling.items() if n >= t.n), default=None):
            achieved.append((f"juggler:{t.n}", None, d, t, "juggler", {"n": t.n, "days": JUGGLER_STREAK}))
    singles = {
        "comeback": (min(comebacks, default=None), {"gap": COMEBACK_GAP, "n": COMEBACK_STREAK}),
        "joker": (min(joker_days, default=None), {}),
        "garden_bloom": (min(stage_reached.get(21, []), default=None), {"n": 21}),
        "garden_tree": (min(stage_reached.get(66, []), default=None), {"n": 66}),
    }
    for t in PERFECT_DAY_TIERS:
        if len(pdays) >= t.n:
            achieved.append((f"perfect:{t.n}", None, pdays[t.n - 1], t, "perfect", {"n": t.n}))
    for t in PERFECT_STREAK_TIERS:
        if d := next(
            ((a + timedelta(days=t.n - 1)) for a, b in _runs(pdays) if (b - a).days + 1 >= t.n), None
        ):
            achieved.append((f"perfect_streak:{t.n}", None, d, t, "perfect_streak", {"n": t.n}))
    pset = set(pdays)
    singles["perfect_weekend"] = (
        next((d for d in pdays if d.weekday() == 6 and d - timedelta(1) in pset), None),
        {},
    )
    done_challenges = sorted(c["completed_on"] for c in challenges if c["completed_on"])
    for t in CHALLENGE_TIERS:
        if len(done_challenges) >= t.n:
            achieved.append(
                (f"challenges:{t.n}", None, done_challenges[t.n - 1], t, "challenges", {"n": t.n})
            )
    by_week: dict[date, list] = {}
    for c in challenges:
        by_week.setdefault(c["week"], []).append(c)
    sweeps = [
        max(c["completed_on"] for c in cs)
        for cs in by_week.values()
        if len(cs) >= 2 and all(c["completed_on"] for c in cs)
    ]
    singles["clean_sweep"] = (min(sweeps, default=None), {})
    all_completions = sorted(d for r in routines for d in r["days"])
    for t in ROUTINE_TIERS:
        if len(all_completions) >= t.n:
            achieved.append((f"routines:{t.n}", None, all_completions[t.n - 1], t, "routines", {"n": t.n}))
    streak7 = [a + timedelta(days=6) for r in routines for a, b in _runs(r["days"]) if (b - a).days >= 6]
    singles["routine_streak"] = (min(streak7, default=None), {"n": 7})
    for key, (d, params) in singles.items():
        if d:
            achieved.append((key, None, d, SINGLE[key], key, params))

    stored = _store(conn, achieved)
    names = {h["id"]: h for h in per_habit}
    progress = {
        "comeback": 0,
        "joker": int(bool(joker_days)),
        "perfect_weekend": 0,
        "clean_sweep": 0,
        "routine_streak": max((r["best_streak"] for r in routines), default=0),
        "garden_bloom": int(max((g["growth"] for g in garden), default=0)),
        "garden_tree": int(max((g["growth"] for g in garden), default=0)),
    }
    achievements = _catalogue(
        stored,
        per_habit,
        total,
        habit_flames,
        juggling,
        pdays,
        names,
        len(done_challenges),
        len(all_completions),
        progress,
    )

    active = [h for h in per_habit if not h["archived"]]
    best_current = max(
        active, key=lambda h: (h["streak"] * (7 if h["unit"] == "weeks" else 1), h["flames"]), default=None
    )
    best_ever = max(
        per_habit,
        key=lambda h: (h["best_streak"] * (7 if h["unit"] == "weeks" else 1), h["best_to"] or date.min),
        default=None,
    )
    return {
        "flames": {
            "total": total,
            "habits": sum(habit_flames.values()),
            "bonus": sum(bonus.values()),
            "today": flames_by_day.get(today, 0),
            "week": sum(n for d, n in flames_by_day.items() if d >= week),
            "best_day": max(habit_flames.values(), default=0),
            "by_day": {d.isoformat(): n for d, n in sorted(flames_by_day.items())},
        },
        "level": level_of(total),
        "best_current": best_current if best_current and best_current["streak"] else None,
        "best_ever": best_ever if best_ever and best_ever["best_streak"] else None,
        "perfect": {
            "current": perfect["current"],
            "best": perfect["best"],
            "total": len(pdays),
            "today": today in pset,
            "open_today": perfect["open_today"],
            "days": pdays,
        },
        "habits": per_habit,
        "garden": garden,
        "routines": routines,
        "challenges": [c for c in challenges if c["week"] == week],
        "challenges_done": len(done_challenges),
        "achievements": achievements,
        "unlocked": sum(1 for a in achievements if a["unlocked_on"]),
        "new": [a for a in achievements if a["new"]],
    }


def _tier_dict(t: Tier | None) -> dict | None:
    return {"n": t.n, "tier": t.tier, "title": t.title, "icon": t.icon} if t else None


def _perfect_days(evaluated, starts, done_sets, start: date, today: date) -> dict:
    """Days every active daily habit with a known status was done (automatic ones without data are
    left out). Today is perfect once everything is done; until then it does not break the run."""
    daily = [h for h in evaluated if h["per_week"] >= 7 and not h["archived"]]
    out, open_today = [], []
    d = start
    while d <= today:
        iso = d.isoformat()
        relevant = [h for h in daily if starts[h["id"]] <= d and (not h["metric"] or iso in h["days"])]
        if relevant and all(d in done_sets[h["id"]] for h in relevant):
            out.append(d)
        if d == today:
            open_today = [h["id"] for h in relevant if d not in done_sets[h["id"]]]
        d += timedelta(days=1)
    best = max(((last - first).days + 1 for first, last in _runs(out)), default=0)
    return {"days": out, "current": _current_run(set(out), today), "best": best, "open_today": open_today}


def _store(conn: Connection, achieved: list) -> dict:
    """Store newly earned achievements; returns {key: (unlocked_on, seen)} of everything stored."""
    if achieved:
        conn.execute(
            text("""INSERT INTO habit_achievement (key, habit_id, unlocked_on) VALUES (:k, :h, :d)
                    ON CONFLICT (key) DO NOTHING"""),
            [{"k": k, "h": h, "d": d} for k, h, d, *_ in achieved],
        )
    return {
        r.key: (r.unlocked_on, r.seen)
        for r in conn.execute(text("SELECT key, unlocked_on, seen FROM habit_achievement"))
    }


def _catalogue(
    stored,
    per_habit,
    total,
    habit_flames,
    juggling,
    pdays,
    names,
    challenges_done,
    routine_completions,
    progress,
) -> list[dict]:
    """All achievements, unlocked or not, with progress towards the locked ones."""

    def entry(key, group, t: Tier, params, value, habit_id=None):
        unlocked = stored.get(key)
        return {
            "key": key,
            "group": group,
            "habit_id": habit_id,
            "habit": names[habit_id]["name"] if habit_id else None,
            "habit_icon": names[habit_id]["icon"] if habit_id else None,
            "title": t.title,
            "icon": t.icon,
            "tier": t.tier,
            "target": t.n,
            "params": params,
            "progress": min(value, t.n),
            "unlocked_on": unlocked[0] if unlocked else None,
            "new": bool(unlocked and not unlocked[1]),
        }

    best_perfect_run = max(((b - a).days + 1 for a, b in _runs(pdays)), default=0)
    out = [entry(f"flames:{t.n}", "flames", t, {"n": t.n}, total) for t in FLAME_TIERS]
    out += [entry(f"perfect:{t.n}", "perfect", t, {"n": t.n}, len(pdays)) for t in PERFECT_DAY_TIERS]
    out += [
        entry(f"perfect_streak:{t.n}", "perfect_streak", t, {"n": t.n}, best_perfect_run)
        for t in PERFECT_STREAK_TIERS
    ]
    best_day = max(habit_flames.values(), default=0)
    out += [entry(f"day_flames:{t.n}", "day", t, {"n": t.n}, best_day) for t in DAY_FLAME_TIERS]
    out += [
        entry(
            f"juggler:{t.n}",
            "juggler",
            t,
            {"n": t.n, "days": JUGGLER_STREAK},
            max(juggling.values(), default=0),
        )
        for t in JUGGLER_TIERS
    ]
    out += [entry(f"challenges:{t.n}", "challenges", t, {"n": t.n}, challenges_done) for t in CHALLENGE_TIERS]
    out += [entry(f"routines:{t.n}", "routines", t, {"n": t.n}, routine_completions) for t in ROUTINE_TIERS]
    params = {
        "comeback": {"gap": COMEBACK_GAP, "n": COMEBACK_STREAK},
        "routine_streak": {"n": 7},
        "garden_bloom": {"n": 21},
        "garden_tree": {"n": 66},
    }
    out += [entry(k, k, t, params.get(k, {}), progress.get(k, 0)) for k, t in SINGLE.items()]
    for h in per_habit:
        tiers = DAY_STREAK_TIERS if h["unit"] == "days" else WEEK_STREAK_TIERS
        if h["archived"] and not any(k.startswith(f"habit:{h['id']}:") for k in stored):
            continue
        out += [
            entry(
                f"habit:{h['id']}:streak:{t.n}",
                "streak",
                t,
                {"n": t.n, "unit": h["unit"]},
                h["best_streak"],
                h["id"],
            )
            for t in tiers
        ]
        out += [
            entry(f"habit:{h['id']}:flames:{t.n}", "habit_flames", t, {"n": t.n}, h["flames"], h["id"])
            for t in HABIT_FLAME_TIERS
        ]
    # an achievement earned once stays, even when the history behind it changed later
    for e in out:
        if e["unlocked_on"]:
            e["progress"] = e["target"]
    # Achievements of one kind form a series; its steps are ranks from bronze up (each with its own
    # title). A series stays hidden until its first step is earned.
    series: dict[str, list[dict]] = {}
    for e in out:
        key = e["group"] if not e["habit_id"] else f"habit:{e['habit_id']}:{e['group']}"
        if e["group"].startswith("garden_"):
            key = "garden"
        e["series"] = key
        series.setdefault(key, []).append(e)
    for steps in series.values():
        steps.sort(key=lambda e: e["target"])
        for i, e in enumerate(steps):
            e["rank"], e["ranks"] = i, len(steps)
            if len(steps) > 1:
                e["tier"] = RANKS[min(i, len(RANKS) - 1)]
    return out


def compact_rewards(conn: Connection) -> dict:
    """The phone's summary: flames, level, records, the garden and the icons of new achievements."""
    r = compute(conn, datetime.now(_tz()).date())

    def streak(h: dict | None, field: str) -> dict | None:
        return {"name": h["name"], "icon": h["icon"], "streak": h[field], "unit": h["unit"]} if h else None

    return {
        "flames": {k: r["flames"][k] for k in ("total", "today", "week", "best_day", "bonus")},
        "level": {k: r["level"][k] for k in ("level", "title", "progress", "flames", "next")},
        "best_current": streak(r["best_current"], "streak"),
        "best_ever": streak(r["best_ever"], "best_streak"),
        "perfect": {k: r["perfect"][k] for k in ("current", "best", "total", "today")},
        "garden": [
            {
                k: g[k]
                for k in (
                    "id",
                    "name",
                    "icon",
                    "plant",
                    "stage",
                    "stage_index",
                    "stage_title",
                    "health",
                    "growth",
                    "next_at",
                    "next_plant",
                )
            }
            for g in r["garden"]
        ],
        "challenges": [
            {k: c[k] for k in ("kind", "habit", "routine", "target", "progress", "bonus", "completed_on")}
            for c in r["challenges"]
        ],
        "unlocked": r["unlocked"],
        "achievements": len(r["achievements"]),
        "new": [{"key": a["key"], "icon": a["icon"], "title": a["title"]} for a in r["new"]],
    }


def period_rewards(conn: Connection, start: date, end: date) -> dict:
    """Flames, perfect days, finished challenges and new achievements of a review period, with
    the flames of the previous period of equal length."""
    r = compute(conn, datetime.now(_tz()).date())
    length = (end - start).days + 1
    prev_start = start - timedelta(days=length)
    by_day = {date.fromisoformat(k): v for k, v in r["flames"]["by_day"].items()}
    titles = {a["key"]: a for a in r["achievements"]}
    earned = conn.execute(
        text("""SELECT key, unlocked_on FROM habit_achievement WHERE unlocked_on BETWEEN :s AND :e
                ORDER BY unlocked_on, key"""),
        {"s": start, "e": end},
    ).all()
    challenges = conn.execute(
        text("""SELECT count(*) AS total, count(completed_on) AS done FROM habit_challenge
                WHERE week BETWEEN :s AND :e"""),
        {"s": _week_start(start), "e": end},
    ).one()
    return {
        "flames": sum(v for d, v in by_day.items() if start <= d <= end),
        "previous_flames": sum(v for d, v in by_day.items() if prev_start <= d < start),
        "perfect_days": sum(1 for d in r["perfect"]["days"] if start <= d <= end),
        "challenges": {"done": challenges.done, "total": challenges.total},
        "routines_completed": sum(1 for x in r["routines"] for d in x["days"] if start <= d <= end),
        "achievements": [
            {
                "title": titles[k]["title"],
                "icon": titles[k]["icon"],
                "habit": titles[k]["habit"],
                "params": titles[k]["params"],
                "unlocked_on": d,
            }
            for k, d in earned
            if k in titles
        ],
        "level": r["level"]["level"],
        "level_title": r["level"]["title"],
    }


@router.get("/habits/rewards")
def habit_rewards(
    today: date | None = Query(None, description="Reference day (defaults to today)"),
    conn: Connection = CONN,
) -> dict:
    """Flames, level, records, garden, routines, challenges and achievements (new unlocks are stored)."""
    return compute(conn, today or datetime.now(_tz()).date())


@router.post("/habits/rewards/seen")
def rewards_seen(keys: list[str] | None = None, conn: Connection = CONN) -> dict:
    """Mark new achievements as seen (all of them without `keys`)."""
    n = conn.execute(
        text("UPDATE habit_achievement SET seen = true WHERE NOT seen AND (:all OR key = ANY(:keys))"),
        {"all": keys is None, "keys": keys or []},
    ).rowcount
    return {"seen": n}


# --- routines -------------------------------------------------------------------------------------


_ROUTINE_COLS = "id, name, icon, habit_ids, position, archived, created_at"


class RoutineIn(BaseModel):
    id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=80)
    icon: str | None = Field(None, max_length=16)
    habit_ids: list[uuid.UUID] = Field(min_length=2, max_length=10)
    position: int = 0
    archived: bool = False


def _routine_row(conn: Connection, rid) -> dict:
    row = conn.execute(
        text(f"SELECT {_ROUTINE_COLS} FROM habit_routine WHERE id = :id"),
        {"id": rid},
    ).first()
    if row is None:
        raise HTTPException(404, "routine not found")
    return dict(row._mapping)


def _check_routine(conn: Connection, r: RoutineIn) -> dict:
    if len(set(r.habit_ids)) != len(r.habit_ids):
        raise HTTPException(422, "a habit appears twice in the routine")
    known = set(
        conn.execute(text("SELECT id FROM habit WHERE id = ANY(:ids)"), {"ids": r.habit_ids}).scalars()
    )
    if missing := [str(i) for i in r.habit_ids if i not in known]:
        raise HTTPException(422, f"unknown habits: {', '.join(missing)}")
    return {**r.model_dump(), "name": r.name.strip(), "icon": (r.icon or "").strip() or None}


@router.get("/habits/routines")
def list_routines(conn: Connection = CONN) -> list[dict]:
    rows = conn.execute(
        text("""SELECT id, name, icon, habit_ids, position, archived, created_at FROM habit_routine
                ORDER BY archived, position, lower(name)""")
    )
    return [dict(r._mapping) for r in rows]


@router.post("/habits/routines", status_code=201)
def create_routine(r: RoutineIn, conn: Connection = CONN) -> dict:
    v = _check_routine(conn, r)
    rid = r.id or uuid.uuid4()
    conn.execute(
        text("""INSERT INTO habit_routine (id, name, icon, habit_ids, position, archived)
                VALUES (:id, :name, :icon, :habit_ids, :position, :archived) ON CONFLICT (id) DO NOTHING"""),
        {**v, "id": rid},
    )
    return _routine_row(conn, rid)


@router.put("/habits/routines/{routine_id}")
def update_routine(routine_id: uuid.UUID, r: RoutineIn, conn: Connection = CONN) -> dict:
    _routine_row(conn, routine_id)
    v = _check_routine(conn, r)
    conn.execute(
        text("""UPDATE habit_routine SET name = :name, icon = :icon, habit_ids = :habit_ids,
                    position = :position, archived = :archived WHERE id = :id"""),
        {**v, "id": routine_id},
    )
    return _routine_row(conn, routine_id)


@router.delete("/habits/routines/{routine_id}", status_code=204)
def delete_routine(routine_id: uuid.UUID, conn: Connection = CONN) -> Response:
    conn.execute(text("DELETE FROM habit_routine WHERE id = :id"), {"id": routine_id})
    return Response(status_code=204)
