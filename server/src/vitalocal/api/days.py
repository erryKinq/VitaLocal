"""Day view, daily trends and catalog metadata for the WebUI (all day boundaries in local time)."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import Connection, text

from vitalocal import __version__, energy
from vitalocal.api.drinks import drinks_of_day
from vitalocal.config import get_settings
from vitalocal.db import CONN
from vitalocal.diary import day_summary
from vitalocal.metrics import MANUAL_METRICS, METRICS, SLEEP_STAGES, display_value
from vitalocal.nutrients import DAILY_NUTRIENTS

router = APIRouter(prefix="/api/v1", tags=["days"])

# Minute-level series shown in the day view.
DAY_SERIES = (
    "heart_rate",
    "workout_heart_rate",
    "stress",
    "body_energy",
    "steps",
    "spo2",
    "respiratory_rate",
)
# The night belonging to a day: previous evening 18:00 until 14:00 on the day itself.
SLEEP_WINDOW = (time(18, 0), time(14, 0))
_MAX_TREND_DAYS = 800


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def _local_midnight(d: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, time.min, tzinfo=tz)


def _daily_values(conn: Connection, metrics: list[str], start: datetime, end: datetime) -> dict:
    """{metric: {day: {"value": v, "n": n}}} using each metric's daily aggregation rule.

    Nutrition totals come from the day's diary entries (`nutrition_entry`, VitaLocal and Waistline
    JSON), one value per day at local noon, an entry without the nutrient counting as 0; imported
    daily totals (`sample`, e.g. a Waistline diary CSV) count only on days without entries, so
    nothing is counted twice. Body measurements come
    from samples (Waistline) and entries made in VitaLocal (`body_entry`).
    """
    tz = get_settings().timezone
    food = [(k, m) for k, (m, _) in DAILY_NUTRIENTS.items() if m in metrics]
    rows = conn.execute(
        text("""
        WITH entry_days AS (
            SELECT day, nutrients FROM nutrition_entry
            WHERE CAST(:any_food AS boolean)
              AND day >= (CAST(:start AS timestamptz) AT TIME ZONE :tz)::date
              AND day <= (CAST(:end AS timestamptz) AT TIME ZONE :tz)::date
        ), food_totals AS (
            SELECT k.metric, e.day,
                   round(coalesce(sum((e.nutrients->>k.key)::numeric), 0), 3)::float AS total
            FROM entry_days e
            CROSS JOIN unnest(CAST(:food_keys AS text[]), CAST(:food_metrics AS text[])) AS k(key, metric)
            GROUP BY 1, 2
        )
        SELECT metric, (ts AT TIME ZONE :tz)::date AS day, count(*) AS n,
               sum(value) AS sum, avg(value) AS avg, min(value) AS min, max(value) AS max,
               (array_agg(value ORDER BY ts DESC))[1] AS last
        FROM (
            SELECT metric, ts, value FROM sample s
            WHERE metric = ANY(:metrics) AND ts >= :start AND ts < :end
              AND NOT (metric = ANY(CAST(:food_metrics AS text[]))
                       AND EXISTS (SELECT 1 FROM entry_days e WHERE e.day = (s.ts AT TIME ZONE :tz)::date))
            UNION ALL
            SELECT metric, (day + time '12:00') AT TIME ZONE :tz, total FROM food_totals
            UNION ALL
            SELECT metric, ts, value FROM body_entry
            WHERE metric = ANY(:metrics) AND ts >= :start AND ts < :end
            UNION ALL
            SELECT 'checkin.' || kind, ts, value FROM checkin
            WHERE 'checkin.' || kind = ANY(:metrics) AND value IS NOT NULL
              AND ts >= :start AND ts < :end
            UNION ALL
            SELECT 'nutrition.drinks', ts, ml FROM drink
            WHERE 'nutrition.drinks' = ANY(:metrics) AND ts >= :start AND ts < :end
        ) v
        WHERE ts >= :start AND ts < :end
        GROUP BY 1, 2 ORDER BY 1, 2
    """),
        {
            "metrics": metrics,
            "start": start,
            "end": end,
            "tz": tz,
            "any_food": bool(food),
            "food_keys": [k for k, _ in food],
            "food_metrics": [m for _, m in food],
        },
    )
    out: dict[str, dict] = {}
    for r in rows:
        agg = METRICS[r.metric].daily if r.metric in METRICS else "avg"
        out.setdefault(r.metric, {})[r.day.isoformat()] = {
            "value": display_value(r.metric, getattr(r, agg)),
            "n": r.n,
        }
    return out


def latest_weight(conn: Connection, before: datetime) -> float | None:
    """The latest body weight before `before`, from Waistline samples or entries made in VitaLocal."""
    return conn.execute(
        text("""SELECT value FROM (
                    (SELECT ts, value FROM sample WHERE metric = 'weight' AND ts < :e
                     ORDER BY ts DESC LIMIT 1)
                    UNION ALL
                    (SELECT ts, value FROM body_entry WHERE metric = 'weight' AND ts < :e
                     ORDER BY ts DESC LIMIT 1)
                ) w ORDER BY ts DESC LIMIT 1"""),
        {"e": before},
    ).scalar()


@router.get("/meta")
def meta(conn: Connection = CONN) -> dict:
    """Timezone, data range and the metric catalog for the UI."""
    tz = get_settings().timezone
    rng = conn.execute(
        # Monitoring samples are stamped at the end of their interval, so a final 00:00 reading
        # still belongs to the previous day and must not open an (empty) new "latest" day.
        text("""SELECT (SELECT (min(ts) AT TIME ZONE :tz)::date FROM sample),
                       ((max(ts) - interval '1 second') AT TIME ZONE :tz)::date
                FROM sample WHERE metric = 'heart_rate'"""),
        {"tz": tz},
    ).one()
    stored = set(conn.execute(text("SELECT DISTINCT metric FROM sample")).scalars())
    stored |= set(MANUAL_METRICS)  # always offered: entered in the UI
    return {
        "version": __version__,
        "timezone": tz,
        "first_day": rng[0],
        "last_day": rng[1],
        "metrics": [m.as_dict() for m in METRICS.values() if m.key in stored],
        "sleep_stages": SLEEP_STAGES,
    }


@router.get("/energy")
def get_energy(from_: date = Query(alias="from"), to: date = Query(), conn: Connection = CONN) -> dict:
    """Burned energy per day: `rest` (resting / everyday base), `active`, `total` and `source`
    (`watch`, `estimate` from profile + training, or `manual`). Days without any basis are missing."""
    if (to - from_).days > 400:
        raise HTTPException(422, "at most 400 days")
    return {d.isoformat(): v for d, v in energy.daily_burn(conn, from_, to).items()}


@router.get("/days/{day}")
def get_day(day: date, conn: Connection = CONN) -> dict:
    from vitalocal.api.goals import goals_of_day  # goals and supplements build on this module
    from vitalocal.api.supplements import day_status
    from vitalocal.api.today import compact_supplements

    tz = _tz()
    start, end = _local_midnight(day, tz), _local_midnight(day + timedelta(days=1), tz)
    sleep_start = datetime.combine(day - timedelta(days=1), SLEEP_WINDOW[0], tzinfo=tz)
    sleep_end = datetime.combine(day, SLEEP_WINDOW[1], tzinfo=tz)

    daily = _daily_values(conn, list(METRICS), start, end)
    summary = {
        key: {**daily[key][day.isoformat()], "label": m.label, "unit": m.unit, "decimals": m.decimals}
        for key, m in METRICS.items()
        if day.isoformat() in daily.get(key, {})
    }

    series_rows = conn.execute(
        text("""SELECT metric, ts, value FROM sample
                WHERE metric = ANY(:m) AND ts >= :s AND ts < :e ORDER BY metric, ts"""),
        {"m": list(DAY_SERIES), "s": start, "e": end},
    )
    series: dict[str, list] = {m: [] for m in DAY_SERIES}
    for r in series_rows:
        series[r.metric].append([int(r.ts.timestamp() * 1000), r.value])
    series["workout_heart_rate"] = _minute_means(series["workout_heart_rate"])

    return {
        "date": day,
        "timezone": tz.key,
        "window": {"start": start, "end": end},
        "summary": summary,
        "series": series,
        "sleep": _sleep(conn, sleep_start, sleep_end),
        "activities": _activities(conn, start, end),
        "coverage": {
            # minutes with any heart rate: monitoring stream or workout recording
            "heart_rate_minutes": len(
                {t // 60_000 for t, _ in series["heart_rate"] + series["workout_heart_rate"]}
            ),
            "minutes_in_day": int((end - start).total_seconds() // 60),
        },
        "nutrition": _nutrition(conn, day),
        "energy": energy.daily_burn(conn, day, day, tz).get(day),
        "drinks": drinks_of_day(conn, day),
        "goals": goals_of_day(conn, day),
        "supplements": compact_supplements(day_status(conn, day, history_days=60)),
        "journal": _journal(conn, day, start, end),
        "sync": sync_state(conn),
    }


def sync_state(conn: Connection) -> dict:
    """How fresh the data is: last watch sample and the latest upload per source."""
    last_watch = conn.execute(text("SELECT max(ts) FROM sample WHERE metric = 'heart_rate'")).scalar()
    batches = conn.execute(
        text("""SELECT DISTINCT ON (source) source, id, status, received_at, finished_at, client_meta
                FROM ingest_batch ORDER BY source, received_at DESC, id DESC""")
    )
    return {
        "last_heart_rate_at": last_watch,
        "sources": {
            b.source: {
                "batch_id": b.id,
                "status": b.status,
                "received_at": b.received_at,
                "finished_at": b.finished_at,
                "exported_at": b.client_meta.get("exported_at"),
            }
            for b in batches
        },
    }


def _minute_means(points: list) -> list:
    buckets: dict[int, list] = {}
    for t, v in points:
        buckets.setdefault(t // 60_000, []).append(v)
    return [[k * 60_000, sum(v) / len(v)] for k, v in sorted(buckets.items())]


def _nutrition(conn: Connection, day: date) -> dict | None:
    return day_summary(conn, day)


def _journal(conn: Connection, day: date, start: datetime, end: datetime) -> dict:
    text_row = conn.execute(text("SELECT text FROM journal_day WHERE day = :d"), {"d": day}).first()
    checkins = conn.execute(
        text("""SELECT id, ts, kind, value, label, note, activity_id FROM checkin
                WHERE ts >= :s AND ts < :e ORDER BY ts"""),
        {"s": start, "e": end},
    )
    return {"text": text_row.text if text_row else "", "checkins": [dict(r._mapping) for r in checkins]}


def stage_segments(stages: list, starts: list[datetime]) -> list[dict]:
    """Hypnogram segments from Garmin stage samples, which are stamped at the END of their stage.

    A stage runs from the previous sample (or a later sleep start, when a new sleep began in
    between) to its own timestamp. The first stage of a sleep starts at the sleep start from
    the SLEEP FIT (`sleep_in_bed`); without one its start is unknown and it is left out.
    """
    segments = []
    prev = None
    for ts, code in stages:
        begin = max([s for s in starts if s < ts], default=None)
        seg_start = max(prev, begin) if prev is not None and begin is not None else (prev or begin)
        if seg_start is not None:
            segments.append(
                {"start": seg_start, "end": ts, "stage": SLEEP_STAGES.get(int(code), str(int(code)))}
            )
        prev = ts
    return segments


def sleep_starts(conn: Connection, start: datetime, end: datetime) -> list[datetime]:
    return list(
        conn.execute(
            text("""SELECT ts FROM sample WHERE metric = 'sleep_in_bed' AND ts >= :s AND ts < :e
                    ORDER BY ts"""),
            {"s": start, "e": end},
        ).scalars()
    )


def _sleep(conn: Connection, start: datetime, end: datetime) -> dict:
    """Hypnogram of the night in the window (see `stage_segments`)."""
    stages = conn.execute(
        text("""SELECT ts, value FROM sample WHERE metric = 'sleep_stage_code'
                AND ts >= :s AND ts < :e ORDER BY ts"""),
        {"s": start, "e": end},
    ).all()
    hrv = conn.execute(
        text("""SELECT ts, value FROM sample WHERE metric = 'hrv_rmssd'
                AND ts >= :s AND ts < :e ORDER BY ts"""),
        {"s": start, "e": end},
    ).all()
    skin = conn.execute(
        text("""SELECT ts, value FROM sample WHERE metric = 'skin_temp_relative'
                AND ts >= :s AND ts < :e ORDER BY ts"""),
        {"s": start, "e": end},
    ).all()
    segments = stage_segments(stages, sleep_starts(conn, start, end))
    minutes: dict[str, float] = {}
    for s in segments:
        minutes[s["stage"]] = minutes.get(s["stage"], 0) + (s["end"] - s["start"]).total_seconds() / 60
    asleep = sum(v for k, v in minutes.items() if k in ("light", "deep", "rem"))
    return {
        "window": {"start": start, "end": end},
        "segments": segments,
        "minutes": {k: round(v) for k, v in minutes.items()},
        "asleep_minutes": round(asleep) if segments else None,
        "bed_start": segments[0]["start"] if segments else None,
        "bed_end": segments[-1]["end"] if segments else None,
        "hrv": [[int(ts.timestamp() * 1000), v] for ts, v in hrv],
        "skin_temp": [[int(ts.timestamp() * 1000), v] for ts, v in skin],
    }


def _activities(conn: Connection, start: datetime, end: datetime) -> list[dict]:
    rows = conn.execute(
        text("""
        SELECT a.id, a.start_ts, a.end_ts,
               coalesce((SELECT t.title FROM training_session t WHERE t.activity_id = a.id),
                        a.sport_profile_name, a.name) AS name, a.sport,
               a.sub_sport, a.elapsed_s, a.distance_m, a.energy_kcal, a.avg_hr, a.max_hr,
               (SELECT count(*) FROM activity_set s WHERE s.activity_id = a.id AND s.set_type = 'active')
                   AS active_sets
        FROM activity a WHERE a.start_ts >= :s AND a.start_ts < :e ORDER BY a.start_ts
    """),
        {"s": start, "e": end},
    )
    return [dict(r._mapping) for r in rows]


@router.get("/trends")
def trends(
    metrics: str = Query(description="Comma-separated metric keys"),
    from_: date = Query(alias="from"),
    to: date = Query(description="Inclusive local date"),
    conn: Connection = CONN,
) -> dict:
    keys = [m for m in (k.strip() for k in metrics.split(",")) if m]
    if not keys:
        raise HTTPException(422, "metrics must not be empty")
    if to < from_ or (to - from_).days > _MAX_TREND_DAYS:
        raise HTTPException(422, f"range must be 1..{_MAX_TREND_DAYS} days")
    tz = _tz()
    daily = _daily_values(conn, keys, _local_midnight(from_, tz), _local_midnight(to + timedelta(days=1), tz))
    return {
        "from": from_,
        "to": to,
        "metrics": {
            k: {
                "def": METRICS[k].as_dict() if k in METRICS else {"key": k, "label": k},
                "days": [{"day": d, **v} for d, v in daily.get(k, {}).items()],
            }
            for k in keys
        },
    }
