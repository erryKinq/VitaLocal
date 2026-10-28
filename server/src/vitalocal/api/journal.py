"""Manual check-ins (mood, energy, symptoms, RPE, notes), the daily journal and the weekly/monthly
reviews. Drinks are nutrition and live in api/drinks.py."""

import calendar
import json
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, HTTPException, Query, Response, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import Connection, text

from vitalocal.api.drinks import DrinkIn, store_drink
from vitalocal.config import get_settings
from vitalocal.db import CONN
from vitalocal.insights import period_facts

router = APIRouter(prefix="/api/v1", tags=["journal"])

# "water" is accepted for clients that predate the drinks endpoint and stored as a drink.
Kind = Literal["mood", "energy", "water", "symptom", "rpe", "note"]

# kind -> (min, max, required) for `value`
_VALUE_RULES = {
    "mood": (1, 5, True),
    "energy": (1, 5, True),
    "water": (1, 5000, True),  # ml
    "symptom": (1, 5, False),  # severity
    "rpe": (1, 10, True),
    "note": (None, None, False),
}
_DRINK_KINDS = {"water", "tea", "coffee", "other"}


class CheckinIn(BaseModel):
    id: uuid.UUID = Field(description="Client-generated; resubmitting the same id is a no-op")
    kind: Kind
    ts: datetime | None = Field(None, description="Defaults to now")
    value: float | None = None
    label: str | None = Field(None, max_length=120, description="Symptom name")
    note: str | None = Field(None, max_length=4000)
    activity_id: int | None = None
    source: str = Field("webui", max_length=40)

    @model_validator(mode="after")
    def _check(self):
        lo, hi, required = _VALUE_RULES[self.kind]
        if self.value is None and required:
            raise ValueError(f"{self.kind} needs a value")
        if self.value is not None:
            if lo is None:
                raise ValueError(f"{self.kind} takes no value")
            if not lo <= self.value <= hi:
                raise ValueError(f"{self.kind} value must be within {lo}..{hi}")
        if self.kind == "symptom" and not (self.label or "").strip():
            raise ValueError("symptom needs a label")
        if self.kind == "note" and not (self.note or "").strip():
            raise ValueError("note needs text")
        if self.ts is not None and self.ts.tzinfo is None:
            raise ValueError("ts must include a timezone")
        return self


_COLS = "id, ts, kind, value, label, note, activity_id, source, created_at, updated_at"


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def _day_window(day: date) -> tuple[datetime, datetime]:
    tz = _tz()
    start = datetime.combine(day, time.min, tzinfo=tz)
    return start, datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)


@router.post("/checkins", status_code=status.HTTP_201_CREATED)
def create_checkin(c: CheckinIn, response: Response, conn: Connection = CONN) -> dict:
    if c.kind == "water":  # deprecated: old clients and queued offline entries
        drink = (c.label or "water") if (c.label or "water") in _DRINK_KINDS else "other"
        row, created = store_drink(conn, DrinkIn(id=c.id, ts=c.ts, drink=drink, ml=c.value, source=c.source))
        if not created:
            response.status_code = status.HTTP_200_OK
        return {
            "id": row["id"], "ts": row["ts"], "kind": "water", "value": row["ml"], "label": row["drink"],
            "note": None, "activity_id": None, "source": row["source"], "created_at": row["created_at"],
            "updated_at": row["created_at"],
        }  # fmt: skip
    if (
        c.activity_id is not None
        and not conn.execute(text("SELECT 1 FROM activity WHERE id = :id"), {"id": c.activity_id}).first()
    ):
        raise HTTPException(422, "activity not found")
    row = conn.execute(
        text(f"""
        INSERT INTO checkin (id, ts, kind, value, label, note, activity_id, source)
        VALUES (:id, :ts, :kind, :value, :label, :note, :activity_id, :source)
        ON CONFLICT (id) DO NOTHING
        RETURNING {_COLS}
    """),
        {**c.model_dump(), "ts": c.ts or datetime.now(UTC), "label": (c.label or "").strip() or None},
    ).first()
    if row is None:  # replay of an already stored check-in
        response.status_code = status.HTTP_200_OK
        row = conn.execute(text(f"SELECT {_COLS} FROM checkin WHERE id = :id"), {"id": c.id}).one()
    return dict(row._mapping)


@router.get("/checkins")
def list_checkins(
    from_: datetime = Query(alias="from"),
    to: datetime = Query(),
    kind: Kind | None = None,
    conn: Connection = CONN,
) -> list[dict]:
    rows = conn.execute(
        text(f"""SELECT {_COLS} FROM checkin WHERE ts >= :f AND ts < :t
                 AND (CAST(:k AS text) IS NULL OR kind = :k) ORDER BY ts"""),
        {"f": from_, "t": to, "k": kind},
    )
    return [dict(r._mapping) for r in rows]


@router.delete("/checkins/{checkin_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_checkin(checkin_id: uuid.UUID, conn: Connection = CONN) -> Response:
    conn.execute(text("DELETE FROM checkin WHERE id = :id"), {"id": checkin_id})
    # drinks were check-ins before; old clients delete them here
    conn.execute(text("DELETE FROM drink WHERE id = :id"), {"id": checkin_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/symptoms")
def symptom_labels(conn: Connection = CONN) -> list[dict]:
    """Previously used symptom names (most frequent first) for quick re-entry."""
    rows = conn.execute(
        text("""SELECT label, count(*) AS n, max(ts) AS last FROM checkin
                WHERE kind = 'symptom' GROUP BY label ORDER BY n DESC, last DESC LIMIT 30""")
    )
    return [dict(r._mapping) for r in rows]


# --- texts: day journal and weekly/monthly reviews ------------------------------------------------

Scope = Literal["day", "week", "month"]
# A replaced text is kept as a revision unless the same source rewrote it within this time
# (autosave while typing); an emptied text is always kept.
REVISION_QUIET = timedelta(minutes=10)


class TextIn(BaseModel):
    text: str = Field(max_length=20000, description="Empty text clears the entry (kept as a revision)")
    version: int | None = Field(
        None, ge=0, description="Version the edit is based on (0 = new); 409 if the stored one differs"
    )
    source: str = Field("api", max_length=40, description="Who wrote it, e.g. webui, android, agent, ai")


def period_of(scope: Scope, day: date) -> tuple[date, date]:
    if scope == "week":
        start = day - timedelta(days=day.weekday())
        return start, start + timedelta(days=6)
    if scope == "month":
        return day.replace(day=1), day.replace(day=calendar.monthrange(day.year, day.month)[1])
    return day, day


def _etag(version: int) -> str:
    return f'"{version}"'


def _expected_version(body: TextIn, if_match: str | None) -> int | None:
    if body.version is not None:
        return body.version
    if if_match is None or if_match.strip() == "*":
        return None
    try:
        return int(if_match.strip().removeprefix("W/").strip('"'))
    except ValueError:
        raise HTTPException(400, 'If-Match must be a version ETag such as "3"') from None


def _load(conn: Connection, scope: Scope, start: date, lock: bool = False):
    suffix = " FOR UPDATE" if lock else ""
    if scope == "day":
        return conn.execute(
            text(f"SELECT text, version, source, updated_at FROM journal_day WHERE day = :d{suffix}"),
            {"d": start},
        ).first()
    return conn.execute(
        text(f"""SELECT text, version, source, updated_at, facts FROM journal_review
                 WHERE scope = :s AND period_start = :d{suffix}"""),
        {"s": scope, "d": start},
    ).first()


def _state(row) -> dict:
    return {
        "text": row.text if row else "",
        "version": row.version if row else 0,
        "source": row.source if row else None,
        "updated_at": row.updated_at if row else None,
    }


def write_text(conn: Connection, scope: Scope, day: date, body: TextIn, if_match: str | None) -> dict:
    """Compare-and-swap write shared by day entries and reviews. Returns the new state."""
    start, end = period_of(scope, day)
    expected = _expected_version(body, if_match)
    if scope == "day":  # the row may not exist yet; serialize concurrent first writes
        conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtext('journal_day'), :k)"), {"k": start.toordinal()}
        )
    cur = _load(conn, scope, start, lock=True)
    current = _state(cur)
    if expected is not None and expected != current["version"]:
        raise HTTPException(
            409,
            jsonable_encoder(
                {"message": "stale version: the text was changed elsewhere", "current": current}
            ),
        )
    if cur is None and not body.text.strip():
        return current
    if cur is not None and cur.text == body.text:
        return current
    now = datetime.now(UTC)
    if (
        cur is not None
        and cur.text.strip()
        and (not body.text.strip() or cur.source != body.source or now - cur.updated_at > REVISION_QUIET)
    ):
        conn.execute(
            text("""INSERT INTO journal_revision (scope, period, version, text, source, written_at)
                    VALUES (:scope, :p, :v, :t, :src, :w)"""),
            {
                "scope": scope,
                "p": start,
                "v": cur.version,
                "t": cur.text,
                "src": cur.source,
                "w": cur.updated_at,
            },
        )
    new_text = body.text if body.text.strip() else ""
    if scope == "day":
        row = conn.execute(
            text("""INSERT INTO journal_day (day, text, source) VALUES (:d, :t, :src)
                    ON CONFLICT (day) DO UPDATE SET text = EXCLUDED.text, source = EXCLUDED.source,
                        version = journal_day.version + 1, updated_at = now()
                    RETURNING text, version, source, updated_at"""),
            {"d": start, "t": new_text, "src": body.source},
        ).one()
    else:
        facts = json.dumps(period_facts(conn, _tz(), start, end), default=str)
        row = conn.execute(
            text("""INSERT INTO journal_review (scope, period_start, period_end, text, source, facts)
                    VALUES (:s, :d, :e, :t, :src, CAST(:f AS jsonb))
                    ON CONFLICT (scope, period_start) DO UPDATE SET text = EXCLUDED.text,
                        source = EXCLUDED.source, facts = EXCLUDED.facts,
                        version = journal_review.version + 1, updated_at = now()
                    RETURNING text, version, source, updated_at"""),
            {"s": scope, "d": start, "e": end, "t": new_text, "src": body.source, "f": facts},
        ).one()
    return _state(row)


@router.get("/journal/search")
def search_journal(
    q: str = Query(min_length=2, max_length=200),
    limit: int = Query(30, ge=1, le=100),
    conn: Connection = CONN,
) -> list[dict]:
    """Full-text search (German stemming, web-search syntax: "exact phrase", or, -exclude) over day
    entries, weekly/monthly reviews and check-in notes. Matches in `snippet` are wrapped in ⟦ ⟧."""
    rows = conn.execute(
        text("""
        WITH q AS (SELECT websearch_to_tsquery('german', :q) AS query),
        hits AS (
            SELECT 'day' AS scope, day AS start, day AS "end", text AS body, updated_at
            FROM journal_day WHERE to_tsvector('german', text) @@ (SELECT query FROM q)
            UNION ALL
            SELECT scope, period_start, period_end, text, updated_at FROM journal_review
            WHERE to_tsvector('german', text) @@ (SELECT query FROM q)
            UNION ALL
            SELECT 'note', (ts AT TIME ZONE :tz)::date, (ts AT TIME ZONE :tz)::date,
                   concat_ws(' – ', label, note), updated_at
            FROM checkin WHERE to_tsvector('german', concat_ws(' ', label, note)) @@ (SELECT query FROM q)
        )
        SELECT scope, start, "end",
               ts_headline('german', body, (SELECT query FROM q),
                           'StartSel=⟦, StopSel=⟧, MaxWords=30, MinWords=12, MaxFragments=2') AS snippet,
               ts_rank(to_tsvector('german', body), (SELECT query FROM q)) AS rank
        FROM hits ORDER BY start DESC, rank DESC LIMIT :limit
    """),
        {"q": q, "tz": get_settings().timezone, "limit": limit},
    )
    return [dict(r._mapping) for r in rows]


@router.get("/journal/reviews")
def list_reviews(
    from_: date = Query(alias="from"), to: date = Query(), conn: Connection = CONN
) -> list[dict]:
    """Weekly and monthly reviews whose period ends within from..to, newest first."""
    rows = conn.execute(
        text("""SELECT scope, period_start AS start, period_end AS "end", left(text, 280) AS excerpt,
                       version, source, updated_at
                FROM journal_review WHERE period_end BETWEEN :f AND :t AND text <> ''
                ORDER BY period_end DESC, scope"""),
        {"f": from_, "t": to},
    )
    return [dict(r._mapping) for r in rows]


@router.get("/journal/revisions")
def list_revisions(
    day: date,
    scope: Scope = "day",
    conn: Connection = CONN,
) -> list[dict]:
    """Earlier texts of a day entry or review (any day within the period), newest first."""
    start, _ = period_of(scope, day)
    rows = conn.execute(
        text("""SELECT id, version, text, source, written_at, saved_at FROM journal_revision
                WHERE scope = :s AND period = :p ORDER BY id DESC LIMIT 50"""),
        {"s": scope, "p": start},
    )
    return [dict(r._mapping) for r in rows]


def _review(conn: Connection, scope: Scope, day: date, response: Response) -> dict:
    start, end = period_of(scope, day)
    row = _load(conn, scope, start)
    response.headers["ETag"] = _etag(row.version if row else 0)
    return {
        "scope": scope,
        "start": start,
        "end": end,
        **_state(row),
        "facts": period_facts(conn, _tz(), start, end),
        "facts_saved": row.facts if row else None,
    }


@router.get("/journal/week/{day}")
def get_week_review(day: date, response: Response, conn: Connection = CONN) -> dict:
    """Review of the Monday–Sunday week containing `day`, with live `facts` for the period."""
    return _review(conn, "week", day, response)


@router.get("/journal/month/{day}")
def get_month_review(day: date, response: Response, conn: Connection = CONN) -> dict:
    return _review(conn, "month", day, response)


@router.put("/journal/week/{day}")
def put_week_review(
    day: date,
    body: TextIn,
    response: Response,
    if_match: str | None = Header(None),
    conn: Connection = CONN,
) -> dict:
    return _put_review(conn, "week", day, body, if_match, response)


@router.put("/journal/month/{day}")
def put_month_review(
    day: date,
    body: TextIn,
    response: Response,
    if_match: str | None = Header(None),
    conn: Connection = CONN,
) -> dict:
    return _put_review(conn, "month", day, body, if_match, response)


def _put_review(conn, scope: Scope, day: date, body: TextIn, if_match, response: Response) -> dict:
    state = write_text(conn, scope, day, body, if_match)
    start, end = period_of(scope, day)
    response.headers["ETag"] = _etag(state["version"])
    return {"scope": scope, "start": start, "end": end, **state}


@router.get("/journal/{day}")
def get_journal(day: date, response: Response, conn: Connection = CONN) -> dict:
    start, end = _day_window(day)
    state = _state(_load(conn, "day", day))
    checkins = conn.execute(
        text(f"SELECT {_COLS} FROM checkin WHERE ts >= :s AND ts < :e ORDER BY ts"),
        {"s": start, "e": end},
    )
    response.headers["ETag"] = _etag(state["version"])
    return {"day": day, **state, "checkins": [dict(r._mapping) for r in checkins]}


@router.put("/journal/{day}")
def put_journal(
    day: date,
    body: TextIn,
    response: Response,
    if_match: str | None = Header(None),
    conn: Connection = CONN,
) -> dict:
    state = write_text(conn, "day", day, body, if_match)
    response.headers["ETag"] = _etag(state["version"])
    return {"day": day, **state}


@router.get("/journal")
def journal_overview(
    from_: date = Query(alias="from"), to: date = Query(), conn: Connection = CONN
) -> list[dict]:
    """Per day: journal excerpt and check-in counts/averages, for the journal list."""
    tz = get_settings().timezone
    rows = conn.execute(
        text("""
        WITH c AS (
            SELECT (ts AT TIME ZONE :tz)::date AS day,
                   avg(value) FILTER (WHERE kind = 'mood') AS mood,
                   avg(value) FILTER (WHERE kind = 'energy') AS energy,
                   array_agg(DISTINCT label) FILTER (WHERE kind = 'symptom') AS symptoms,
                   count(*) AS checkins
            FROM checkin GROUP BY 1
        ), j AS (SELECT day, text FROM journal_day WHERE text <> '')
        SELECT coalesce(j.day, c.day) AS day, left(j.text, 280) AS excerpt, c.mood, c.energy,
               c.symptoms, coalesce(c.checkins, 0) AS checkins
        FROM j FULL JOIN c ON c.day = j.day
        WHERE coalesce(j.day, c.day) BETWEEN :f AND :t
        ORDER BY 1 DESC
    """),
        {"tz": tz, "f": from_, "t": to},
    )
    return [dict(r._mapping) for r in rows]
