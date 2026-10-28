"""Lab results: reports with values, the overview per analyte, personal targets and the catalog.

A report is one blood draw (date of the sample, lab, optionally the original file). Its values
keep what was printed (value with "<"/">", unit, reference range, flag, comment) and are linked to
a catalog analyte, so results from different labs compare after unit conversion. The status of a
value is judged against the lab's own reference (a printed flag wins); a personal target range
per analyte is a second, independent judgement. Nothing here interprets the results; the day and
week summary and agents get them as data (`lab_facts`, `GET /labs/facts`).
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import Connection, text

from vitalocal import labs
from vitalocal.api.days import _tz
from vitalocal.db import CONN
from vitalocal.deps import get_store
from vitalocal.labs import CATALOG, Analyte
from vitalocal.rawstore import RawStore

router = APIRouter(prefix="/api/v1", tags=["labs"])

SETTINGS_KEY = "labs"
MAX_FILE_BYTES = 30 * 1024 * 1024
_STATUS_ORDER = {"low": 0, "high": 0, "normal": 1, None: 2}


# --- settings & catalog ---------------------------------------------------------------------------------


class LabSettings(BaseModel):
    system: Literal["si", "conventional"] = Field("si", description="Default display units")
    units: dict[str, str] = Field(default_factory=dict, description="Per analyte key: display unit override")


def load_settings(conn: Connection) -> LabSettings:
    raw = conn.execute(text("SELECT value FROM app_setting WHERE key = :k"), {"k": SETTINGS_KEY}).scalar()
    return LabSettings(**(raw or {}))


def analytes(conn: Connection) -> dict[str, Analyte]:
    """Built-in catalog plus the user's own analytes."""
    out = dict(CATALOG)
    for r in conn.execute(
        text("SELECT key, label, grp, unit, material, molar_mass FROM lab_analyte ORDER BY key")
    ):
        out[r.key] = Analyte(r.key, r.label, r.grp, r.unit, None, r.molar_mass, (), r.material, custom=True)
    return out


def learned_synonyms(conn: Connection) -> dict[tuple[str, str], str]:
    return {(r.name_norm, r.material): r.analyte_key for r in conn.execute(text("SELECT * FROM lab_synonym"))}


class Matcher:
    """Analytes, synonym index and learned synonyms of one request."""

    def __init__(self, conn: Connection):
        self.analytes = analytes(conn)
        self.index = labs.synonym_index(self.analytes)
        self.learned = learned_synonyms(conn)

    def match(self, name: str, material: str | None, unit: str | None) -> tuple[str | None, str | None]:
        return labs.match_analyte(name, material, unit, self.analytes, self.index, self.learned)


def display_unit(a: Analyte, s: LabSettings) -> str:
    if (u := s.units.get(a.key)) and u in labs.units_for(a):
        return u
    return a.conventional if s.system == "conventional" and a.conventional else a.unit


def _targets(conn: Connection) -> dict[str, dict]:
    return {r.analyte_key: dict(r._mapping) for r in conn.execute(text("SELECT * FROM lab_target"))}


def _f(v) -> float | None:
    return float(v) if v is not None else None


def _printed(comparator: str | None, value: Decimal | float | None, value_text: str | None) -> str:
    if value is None:
        return value_text or ""
    s = format(value, "f") if isinstance(value, Decimal) else f"{value:g}"
    return f"{comparator or ''}{s}"


# --- values & reports -----------------------------------------------------------------------------------


class ValueIn(BaseModel):
    id: uuid.UUID | None = None
    analyte_key: str | None = Field(
        None, description="Catalog key; omitted: matched from name, material and unit (null if unknown)"
    )
    name: str = Field(min_length=1, max_length=120, description="Name as printed")
    material: str | None = Field(None, max_length=60, description='As printed, e.g. "Serum", "EDTA-Blut"')
    method: str | None = Field(None, max_length=60)
    value: str | float | None = Field(
        None, description='As printed: 0.46, "0,46", "< 0,5" or a text result like "negativ"'
    )
    comparator: Literal["<", "<=", ">", ">="] | None = Field(
        None, description="Overrides one parsed from value"
    )
    unit: str | None = Field(
        None, max_length=30, description="Omitted with a catalog analyte: its default unit"
    )
    reference: str | None = Field(
        None, max_length=200, description='Reference range as printed, e.g. "< 2,3"'
    )
    ref_low: float | None = Field(None, description="Overrides the lower bound parsed from reference")
    ref_high: float | None = Field(None, description="Overrides the upper bound parsed from reference")
    flag: str | None = Field(None, max_length=10, description="Flag as printed (H, L, +, ↑ …)")
    note: str | None = Field(None, max_length=4000, description="The lab's comment on this value")

    @model_validator(mode="after")
    def _check(self):
        p = labs.parse_value(self.value)
        if p.value is None and not p.text:
            raise ValueError(f"{self.name}: a value is required")
        return self


class ReportIn(BaseModel):
    id: uuid.UUID | None = Field(None, description="Client-chosen id makes POST safe to repeat")
    collected_on: date = Field(description="Day the sample was taken (Abnahme)")
    reported_on: date | None = None
    lab: str | None = Field(None, max_length=120)
    note: str | None = Field(None, max_length=4000)
    values: list[ValueIn] = Field(default_factory=list, max_length=400)
    learn_synonyms: bool = Field(
        True, description="Remember printed names that were assigned to an analyte by hand, for later imports"
    )


def _value_params(v: ValueIn, m: Matcher, report_id, position: int) -> tuple[dict, str | None]:
    """DB row for a value and, when the analyte was assigned by hand, the synonym to learn."""
    p = labs.parse_value(v.value)
    key, learn = v.analyte_key, None
    if key is not None and key not in m.analytes:
        raise HTTPException(422, f"unknown analyte {key!r}")
    if key is None:
        key, _ = m.match(v.name, v.material, v.unit)
    elif m.match(v.name, v.material, v.unit)[0] != key:
        learn = key
    unit = (v.unit or "").strip() or (m.analytes[key].unit if key else None)
    low, high = labs.parse_reference(v.reference)
    low = Decimal(str(v.ref_low)) if v.ref_low is not None else low
    high = Decimal(str(v.ref_high)) if v.ref_high is not None else high
    if low is not None and high is not None and low > high:
        raise HTTPException(422, f"{v.name}: reference lower bound is above the upper bound")
    row = {
        "id": v.id or uuid.uuid4(),
        "report_id": report_id,
        "position": position,
        "analyte_key": key,
        "name": v.name.strip(),
        "material": (v.material or "").strip() or None,
        "method": (v.method or "").strip() or None,
        "comparator": (v.comparator or p.comparator) if p.value is not None else None,
        "value": p.value,
        "value_text": p.text if p.value is None else None,
        "unit": unit,
        "ref_text": (v.reference or "").strip() or None,
        "ref_low": low,
        "ref_high": high,
        "flag": (v.flag or "").strip() or None,
        "note": (v.note or "").strip() or None,
    }
    return row, learn


def _write_values(conn: Connection, report_id, values: list[ValueIn], learn: bool) -> None:
    m = Matcher(conn)
    conn.execute(text("DELETE FROM lab_value WHERE report_id = :r"), {"r": report_id})
    for i, v in enumerate(values):
        row, key = _value_params(v, m, report_id, i)
        conn.execute(
            text("""INSERT INTO lab_value (id, report_id, position, analyte_key, name, material, method,
                        comparator, value, value_text, unit, ref_text, ref_low, ref_high, flag, note)
                    VALUES (:id, :report_id, :position, :analyte_key, :name, :material, :method, :comparator,
                        :value, :value_text, :unit, :ref_text, :ref_low, :ref_high, :flag, :note)"""),
            row,
        )
        if learn and key:
            learn_synonym(conn, v.name, v.material, key)


def learn_synonym(conn: Connection, name: str, material: str | None, key: str) -> None:
    conn.execute(
        text("""INSERT INTO lab_synonym (name_norm, material, analyte_key) VALUES (:n, :m, :k)
                ON CONFLICT (name_norm, material) DO UPDATE SET analyte_key = EXCLUDED.analyte_key"""),
        {"n": labs.norm_name(name), "m": labs.norm_material(material) or "", "k": key},
    )


def value_status(row) -> tuple[str | None, str | None]:
    """(status, source): the printed flag wins over the range check."""
    if s := labs.flag_status(row["flag"]):
        return s, "flag"
    s = labs.range_status(row["value"], row["comparator"], row["ref_low"], row["ref_high"])
    return s, ("reference" if s else None)


def _value_out(row: dict, m: dict[str, Analyte], s: LabSettings, targets: dict) -> dict:
    a = m.get(row["analyte_key"]) if row["analyte_key"] else None
    st, src = value_status(row)
    out = {
        "id": row["id"],
        "analyte_key": row["analyte_key"],
        "analyte": a.label if a else None,
        "name": row["name"],
        "material": row["material"],
        "method": row["method"],
        "comparator": row["comparator"],
        "value": _f(row["value"]),
        "value_text": row["value_text"],
        "printed": _printed(row["comparator"], row["value"], row["value_text"]),
        "unit": row["unit"],
        "reference": row["ref_text"],
        "ref_low": _f(row["ref_low"]),
        "ref_high": _f(row["ref_high"]),
        "flag": row["flag"],
        "note": row["note"],
        "status": st,
        "status_source": src,
    }
    if a is not None and row["value"] is not None:
        du = display_unit(a, s)
        conv = labs.convert(row["value"], row["unit"], du, a)
        if conv is not None:
            out["display"] = {
                "unit": du,
                "value": row["value"]
                if labs.norm_unit(du) == labs.norm_unit(row["unit"])
                else labs.round_sig(conv),
                "ref_low": labs.round_sig(labs.convert(row["ref_low"], row["unit"], du, a)),
                "ref_high": labs.round_sig(labs.convert(row["ref_high"], row["unit"], du, a)),
            }
            out["display"]["value"] = _f(out["display"]["value"])
        t = targets.get(a.key)
        if t:
            lo = labs.convert(t["min_value"], t["unit"], row["unit"], a)
            hi = labs.convert(t["max_value"], t["unit"], row["unit"], a)
            out["target_status"] = labs.range_status(row["value"], row["comparator"], lo, hi)
    return out


_REPORT_COLS = """r.id, r.collected_on, r.reported_on, r.lab, r.note, r.artifact_sha256, r.filename, r.source,
                  r.created_at, r.updated_at"""


def _report(conn: Connection, rid, with_values: bool = True) -> dict:
    row = conn.execute(text(f"SELECT {_REPORT_COLS} FROM lab_report r WHERE id = :id"), {"id": rid}).first()
    if row is None:
        raise HTTPException(404, "lab report not found")
    rep = dict(row._mapping)
    rep["has_file"] = rep["artifact_sha256"] is not None
    if with_values:
        m, s, targets = analytes(conn), load_settings(conn), _targets(conn)
        rows = conn.execute(
            text("SELECT * FROM lab_value WHERE report_id = :r ORDER BY position"), {"r": rid}
        )
        rep["values"] = [_value_out(dict(v._mapping), m, s, targets) for v in rows]
    return rep


def list_reports(conn: Connection) -> list[dict]:
    reports = [dict(r._mapping) for r in conn.execute(
        text(f"SELECT {_REPORT_COLS} FROM lab_report r ORDER BY r.collected_on DESC, r.created_at DESC")
    )]  # fmt: skip
    counts: dict = {}
    for v in conn.execute(
        text("SELECT report_id, value, comparator, ref_low, ref_high, flag FROM lab_value")
    ):
        c = counts.setdefault(v.report_id, [0, 0])
        c[0] += 1
        c[1] += value_status(v._mapping)[0] in ("low", "high")
    for r in reports:
        r["has_file"] = r["artifact_sha256"] is not None
        r["n_values"], r["n_outside"] = counts.get(r["id"], [0, 0])
    return reports


def insert_report(conn: Connection, body: ReportIn, artifact: str | None = None, filename: str | None = None,
                  source: str = "manual", import_meta: dict | None = None) -> uuid.UUID:  # fmt: skip
    rid = body.id or uuid.uuid4()
    created = conn.execute(
        text("""INSERT INTO lab_report (id, collected_on, reported_on, lab, note, artifact_sha256, filename,
                                        source, import_meta)
                VALUES (:id, :c, :r, :lab, :note, :sha, :fn, :src, CAST(:meta AS jsonb))
                ON CONFLICT (id) DO NOTHING RETURNING id"""),
        {
            "id": rid,
            "c": body.collected_on,
            "r": body.reported_on,
            "lab": (body.lab or "").strip() or None,
            "note": (body.note or "").strip() or None,
            "sha": artifact,
            "fn": filename,
            "src": source,
            "meta": json.dumps(import_meta, default=str) if import_meta is not None else None,
        },
    ).first()
    if created:
        _write_values(conn, rid, body.values, body.learn_synonyms)
    return rid


# --- overview -------------------------------------------------------------------------------------------


def _trend(latest: float | None, prev: float | None) -> str | None:
    if latest is None or prev is None:
        return None
    if prev == 0:
        return "up" if latest > 0 else "stable"
    change = (latest - prev) / abs(prev)
    return "up" if change > 0.05 else "down" if change < -0.05 else "stable"


def overview(conn: Connection, until: date | None = None, key: str | None = None) -> dict:
    """One row per analyte (uncatalogued values: per printed name) with every value over time."""
    m, s, targets = analytes(conn), load_settings(conn), _targets(conn)
    rows = conn.execute(
        text("""SELECT v.*, r.collected_on, r.lab FROM lab_value v JOIN lab_report r ON r.id = v.report_id
                WHERE (CAST(:u AS date) IS NULL OR r.collected_on <= :u)
                ORDER BY r.collected_on, r.created_at, v.position"""),
        {"u": until},
    )
    groups: dict[str, dict] = {}
    for r in rows:
        d = dict(r._mapping)
        a = m.get(d["analyte_key"]) if d["analyte_key"] else None
        gkey = a.key if a else f"name:{labs.norm_name(d['name'])}"
        if key is not None and gkey != key:
            continue
        g = groups.setdefault(gkey, {
            "key": gkey,
            "analyte_key": a.key if a else None,
            "label": a.label if a else d["name"],
            "group": a.group if a else "other",
            "custom": a.custom if a else None,
            "unit": display_unit(a, s) if a else d["unit"],
            "units": labs.units_for(a) if a else [d["unit"]] if d["unit"] else [],
            "ingredients": list(a.ingredients) if a else [],
            "note": a.note if a else None,
            "points": [],
        })  # fmt: skip
        v = _value_out(d, m, s, targets)
        disp = v.get("display")
        same_unit = labs.norm_unit(d["unit"]) == labs.norm_unit(g["unit"])
        g["points"].append({
            **v,
            "date": d["collected_on"],
            "report_id": d["report_id"],
            "lab": d["lab"],
            "y": disp["value"] if disp else (v["value"] if same_unit else None),
            "y_ref_low": disp["ref_low"] if disp else (v["ref_low"] if same_unit else None),
            "y_ref_high": disp["ref_high"] if disp else (v["ref_high"] if same_unit else None),
        })  # fmt: skip
    out = []
    for g in groups.values():
        pts = g["points"]
        latest = pts[-1]
        prev = next((p for p in reversed(pts[:-1]) if p["report_id"] != latest["report_id"]), None)
        t = targets.get(g["analyte_key"]) if g["analyte_key"] else None
        a = m.get(g["analyte_key"]) if g["analyte_key"] else None
        target = None
        if t and a:
            target = {
                "min": labs.round_sig(labs.convert(t["min_value"], t["unit"], g["unit"], a)),
                "max": labs.round_sig(labs.convert(t["max_value"], t["unit"], g["unit"], a)),
                "unit": g["unit"],
                "note": t["note"],
            }
        both = prev is not None and latest["y"] is not None and prev["y"] is not None
        g.update(
            latest=latest,
            previous=prev,
            status=latest["status"],
            target=target,
            target_status=latest.get("target_status"),
            delta=round(latest["y"] - prev["y"], 4) if both else None,
            trend=_trend(latest["y"], prev["y"] if prev else None),
            count=len(pts),
        )  # fmt: skip
        out.append(g)
    order = {k: i for i, (k, _) in enumerate(labs.GROUPS)}
    out.sort(key=lambda g: (order.get(g["group"], 99), g["label"].lower()))
    grouped = []
    for gk, label in labs.GROUPS:
        rows_ = [g for g in out if g["group"] == gk]
        if rows_:
            grouped.append({"key": gk, "label": label, "rows": rows_})
    return {"settings": s.model_dump(), "groups": grouped}


# --- facts for the day/week summary and agents ----------------------------------------------------------


def supplement_history(conn: Connection, day: date) -> list[dict]:
    """Per supplement: since when it is taken (start date or first log) and how regularly."""
    rows = conn.execute(
        text("""SELECT s.id, s.name, s.brand, s.per_day, s.ingredients, s.started_on, s.archived,
                       min(l.day) AS first_logged, max(l.day) AS last_logged,
                       count(*) FILTER (WHERE l.day > CAST(:d AS date) - 90 AND l.count >= s.per_day)
                           AS full_90
                FROM supplement s LEFT JOIN supplement_log l ON l.supplement_id = s.id AND l.day <= :d
                GROUP BY s.id ORDER BY s.archived, s.position, s.created_at"""),
        {"d": day},
    )
    out = []
    for r in rows:
        since = (
            min(x for x in (r.started_on, r.first_logged) if x) if (r.started_on or r.first_logged) else None
        )
        out.append({
            "id": r.id,
            "name": r.name + (f" ({r.brand})" if r.brand else ""),
            "taking_since": since,
            "since_source": None if not since else "start date" if since == r.started_on else "first log",
            "last_logged": r.last_logged,
            "days_complete_last_90": r.full_90,
            "archived": r.archived,
            "ingredients": sorted({i["key"] for i in r.ingredients if i.get("key")}),
        })  # fmt: skip
    return out


def _days_taken(conn: Connection, sid, end: date, days: int = 90) -> int:
    return conn.execute(
        text("""SELECT count(*) FROM supplement_log l JOIN supplement s ON s.id = l.supplement_id
                WHERE l.supplement_id = :s AND l.day > CAST(:e AS date) - :n AND l.day <= :e
                  AND l.count >= s.per_day"""),
        {"s": sid, "e": end, "n": days},
    ).scalar()


def _fmt(v: float | None, unit: str | None, comparator: str | None = None) -> str | None:
    if v is None:
        return None
    return f"{comparator or ''}{v:g} {unit or ''}".strip()


def lab_facts(conn: Connection, day: date, new_days: int = 14, detail: bool = False) -> dict | str:
    """Lab results as data: new values, values outside the lab reference or the personal target,
    change against the previous value, and which related supplements were taken before the draw."""
    ov = overview(conn, until=day)
    rows = [r for g in ov["groups"] for r in g["rows"]]
    if not rows:
        return "no lab results recorded"
    last = conn.execute(
        text(
            """SELECT collected_on, lab FROM lab_report WHERE collected_on <= :d
               ORDER BY collected_on DESC LIMIT 1"""
        ),
        {"d": day},
    ).first()
    sups = supplement_history(conn, day)

    def compact(r: dict) -> dict:
        p = r["latest"]
        item = {
            "analyte": r["label"],
            "date": p["date"].isoformat(),
            "value": _fmt(p["y"], r["unit"], p["comparator"])
            or p["printed"] + (f" {p['unit']}" if p["unit"] else ""),
            "lab_reference": f"{p['reference']} {p['unit'] or ''}".strip() if p["reference"] else None,
            "status_vs_lab_reference": p["status"] or "unknown",
        }
        printed = f"{p['printed']} {p['unit'] or ''}".strip()
        if p["y"] is not None and labs.norm_unit(p["unit"]) != labs.norm_unit(r["unit"]):
            item["as_printed"] = printed
        if p["flag"]:
            item["printed_flag"] = p["flag"]
        if r["target"]:
            lo, hi = r["target"]["min"], r["target"]["max"]
            item["personal_target"] = f"{lo:g}–{hi:g}" if lo is not None and hi is not None else (
                f">= {lo:g}" if lo is not None else f"<= {hi:g}")  # fmt: skip
            item["status_vs_personal_target"] = r["target_status"] or "unknown"
        if prev := r["previous"]:
            item["previous"] = (
                f"{_fmt(prev['y'], r['unit'], prev['comparator'])} ({prev['date'].isoformat()})"
            )
            if r["delta"] is not None and prev["y"]:
                item["change_pct"] = round(r["delta"] / abs(prev["y"]) * 100)
        if detail:
            item["history"] = [
                {"date": x["date"].isoformat(),
                 "value": _fmt(x["y"], r["unit"], x["comparator"]) or x["printed"],
                 "status": x["status"]} for x in r["points"]
            ]  # fmt: skip
            if p["note"]:
                item["lab_comment"] = p["note"]
        related = [s for s in sups if set(s["ingredients"]) & set(r["ingredients"])]
        if related:
            item["related_supplements_before_draw"] = [
                {"supplement": s["name"],
                 "taking_since": s["taking_since"].isoformat() if s["taking_since"] else "never logged",
                 "days_complete_in_90_days_before_draw": _days_taken(conn, s["id"], p["date"])}
                for s in related
            ]  # fmt: skip
        return item

    cutoff = day - timedelta(days=new_days)
    new = [compact(r) for r in rows if r["latest"]["date"] > cutoff]
    outside = [compact(r) for r in rows if r["status"] in ("low", "high")]
    off_target = [compact(r) for r in rows if r["target_status"] in ("low", "high")]
    out = {
        "latest_report": {
            "date": last.collected_on.isoformat(),
            "lab": last.lab,
            "days_ago": (day - last.collected_on).days,
        },
        f"new_values_last_{new_days}_days": new or "none",
        "latest_outside_lab_reference": outside or "none",
        "latest_outside_personal_target": off_target or "none",
        "supplements_taken": [
            {"name": s["name"], "taking_since": s["taking_since"].isoformat() if s["taking_since"] else None,
             "since_source": s["since_source"], "days_complete_last_90": s["days_complete_last_90"],
             **({"archived": True} if s["archived"] else {})}
            for s in sups
        ] or "none",
        "note": "Status compares with the printed reference range; it is not a medical assessment.",
    }  # fmt: skip
    if detail:
        out["all_latest"] = [compact(r) for r in rows]
    return out


# --- API: catalog & settings ----------------------------------------------------------------------------


@router.get("/labs/catalog")
def get_catalog(conn: Connection = CONN) -> dict:
    """Analytes with units, synonyms and related supplement ingredients, plus learned synonyms."""
    m, s, targets = analytes(conn), load_settings(conn), _targets(conn)
    return {
        "groups": [{"key": k, "label": label} for k, label in labs.GROUPS],
        "materials": list(labs.MATERIALS),
        "analytes": [
            {
                "key": a.key,
                "label": a.label,
                "group": a.group,
                "unit": a.unit,
                "conventional_unit": a.conventional,
                "units": labs.units_for(a),
                "display_unit": display_unit(a, s),
                "material": a.material,
                "molar_mass": a.molar_mass,
                "synonyms": list(a.synonyms),
                "ingredients": list(a.ingredients),
                "note": a.note,
                "custom": a.custom,
                "target": targets.get(a.key),
            }
            for a in m.values()
        ],
        "learned_synonyms": [
            {"name_norm": n, "material": mat or None, "analyte_key": k}
            for (n, mat), k in learned_synonyms(conn).items()
        ],
    }


@router.get("/settings/labs")
def get_lab_settings(conn: Connection = CONN) -> dict:
    return load_settings(conn).model_dump()


@router.put("/settings/labs")
def put_lab_settings(body: LabSettings, conn: Connection = CONN) -> dict:
    m = analytes(conn)
    for k, u in body.units.items():
        if k not in m or u not in labs.units_for(m[k]):
            raise HTTPException(422, f"{k}: unit {u!r} is not available")
    conn.execute(
        text("""INSERT INTO app_setting (key, value) VALUES (:k, CAST(:v AS jsonb))
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"""),
        {"k": SETTINGS_KEY, "v": body.model_dump_json()},
    )
    return body.model_dump()


class AnalyteIn(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    group: str = "other"
    unit: str = Field(min_length=1, max_length=30)
    material: Literal["serum", "plasma", "whole_blood", "urine"] | None = None
    molar_mass: float | None = Field(None, gt=0, description="g/mol, enables mass ↔ molar conversion")


@router.post("/labs/analytes", status_code=status.HTTP_201_CREATED)
def create_analyte(body: AnalyteIn, conn: Connection = CONN) -> dict:
    """An analyte that is not in the built-in catalog. Its key is `x_` + a slug of the label."""
    if body.group not in labs.GROUP_LABELS:
        raise HTTPException(422, f"unknown group {body.group!r}")
    slug = (
        re.sub(r"[^a-z0-9]+", "_", labs.norm_name(body.label).replace(" ", "_")).strip("_")[:36] or "analyte"
    )
    key = f"x_{slug}"
    if key in CATALOG or conn.execute(text("SELECT 1 FROM lab_analyte WHERE key = :k"), {"k": key}).first():
        raise HTTPException(409, f"analyte {key} exists")
    conn.execute(
        text("""INSERT INTO lab_analyte (key, label, grp, unit, material, molar_mass)
                VALUES (:k, :l, :g, :u, :m, :mm)"""),
        {"k": key, "l": body.label.strip(), "g": body.group, "u": body.unit.strip(), "m": body.material,
         "mm": body.molar_mass},
    )  # fmt: skip
    return {"key": key, **body.model_dump()}


@router.delete("/labs/analytes/{key}", status_code=status.HTTP_204_NO_CONTENT)
def delete_analyte(key: str, conn: Connection = CONN) -> Response:
    """Removes an own analyte; its values stay, compared by name only."""
    if not conn.execute(text("DELETE FROM lab_analyte WHERE key = :k RETURNING key"), {"k": key}).first():
        raise HTTPException(404, "only own analytes (x_…) can be deleted")
    conn.execute(text("UPDATE lab_value SET analyte_key = NULL WHERE analyte_key = :k"), {"k": key})
    conn.execute(text("DELETE FROM lab_synonym WHERE analyte_key = :k"), {"k": key})
    conn.execute(text("DELETE FROM lab_target WHERE analyte_key = :k"), {"k": key})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class SynonymIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    material: str | None = None
    analyte_key: str


@router.post("/labs/synonyms")
def add_synonym(body: SynonymIn, conn: Connection = CONN) -> dict:
    if body.analyte_key not in analytes(conn):
        raise HTTPException(422, f"unknown analyte {body.analyte_key!r}")
    learn_synonym(conn, body.name, body.material, body.analyte_key)
    return {"name_norm": labs.norm_name(body.name), "material": labs.norm_material(body.material),
            "analyte_key": body.analyte_key}  # fmt: skip


@router.delete("/labs/synonyms", status_code=status.HTTP_204_NO_CONTENT)
def delete_synonym(name: str, material: str | None = None, conn: Connection = CONN) -> Response:
    conn.execute(
        text("DELETE FROM lab_synonym WHERE name_norm = :n AND material = :m"),
        {"n": labs.norm_name(name), "m": labs.norm_material(material) or ""},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class TargetIn(BaseModel):
    min_value: float | None = None
    max_value: float | None = None
    unit: str | None = Field(None, description="Defaults to the analyte's display unit")
    note: str | None = Field(None, max_length=300)

    @model_validator(mode="after")
    def _check(self):
        if self.min_value is None and self.max_value is None:
            raise ValueError("give min_value, max_value or both")
        if self.min_value is not None and self.max_value is not None and self.min_value > self.max_value:
            raise ValueError("min_value is above max_value")
        return self


@router.put("/labs/targets/{key}")
def put_target(key: str, body: TargetIn, conn: Connection = CONN) -> dict:
    """Personal target range for an analyte (e.g. LDL < 2.6 mmol/l), judged next to the lab reference."""
    m = analytes(conn)
    if key not in m:
        raise HTTPException(404, "unknown analyte")
    unit = body.unit or display_unit(m[key], load_settings(conn))
    if labs.convert(1.0, unit, m[key].unit, m[key]) is None:
        raise HTTPException(422, f"unit {unit!r} does not fit {m[key].label}")
    conn.execute(
        text("""INSERT INTO lab_target (analyte_key, min_value, max_value, unit, note)
                VALUES (:k, :lo, :hi, :u, :n)
                ON CONFLICT (analyte_key) DO UPDATE SET min_value = EXCLUDED.min_value,
                    max_value = EXCLUDED.max_value, unit = EXCLUDED.unit, note = EXCLUDED.note,
                    updated_at = now()"""),
        {"k": key, "lo": body.min_value, "hi": body.max_value, "u": unit, "n": body.note},
    )
    return {"analyte_key": key, **body.model_dump(), "unit": unit}


@router.delete("/labs/targets/{key}", status_code=status.HTTP_204_NO_CONTENT)
def delete_target(key: str, conn: Connection = CONN) -> Response:
    conn.execute(text("DELETE FROM lab_target WHERE analyte_key = :k"), {"k": key})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- API: reports ---------------------------------------------------------------------------------------


@router.get("/labs/reports")
def get_reports(conn: Connection = CONN) -> list[dict]:
    """All reports, newest first, with the number of values and of flagged/out-of-range values."""
    return list_reports(conn)


@router.post("/labs/reports", status_code=status.HTTP_201_CREATED)
def create_report(body: ReportIn, conn: Connection = CONN) -> dict:
    """Enter a report by hand (or from an agent). Repeating a POST with the same id changes nothing."""
    return _report(conn, insert_report(conn, body))


@router.get("/labs/reports/{rid}")
def get_report(rid: uuid.UUID, conn: Connection = CONN) -> dict:
    return _report(conn, rid)


@router.put("/labs/reports/{rid}")
def update_report(rid: uuid.UUID, body: ReportIn, conn: Connection = CONN) -> dict:
    """Full state of the report: header and every value (values not listed are removed)."""
    _report(conn, rid, with_values=False)
    conn.execute(
        text("""UPDATE lab_report SET collected_on = :c, reported_on = :r, lab = :lab, note = :note,
                    updated_at = now() WHERE id = :id"""),
        {"id": rid, "c": body.collected_on, "r": body.reported_on, "lab": (body.lab or "").strip() or None,
         "note": (body.note or "").strip() or None},
    )  # fmt: skip
    _write_values(conn, rid, body.values, body.learn_synonyms)
    return _report(conn, rid)


def drop_artifact_if_unused(conn: Connection, store: RawStore, sha: str | None) -> None:
    """Delete an uploaded report file once nothing refers to it (health data should not linger)."""
    if not sha:
        return
    used = conn.execute(
        text("""SELECT EXISTS (SELECT 1 FROM lab_report WHERE artifact_sha256 = :s)
                    OR EXISTS (SELECT 1 FROM lab_import WHERE artifact_sha256 = :s)
                    OR EXISTS (SELECT 1 FROM ingest_batch WHERE outer_artifact_sha256 = :s)
                    OR EXISTS (SELECT 1 FROM artifact_member WHERE artifact_sha256 = :s)"""),
        {"s": sha},
    ).scalar()
    if used:
        return
    conn.execute(text("DELETE FROM raw_artifact WHERE sha256 = :s"), {"s": sha})
    store.path_for(sha).unlink(missing_ok=True)


@router.delete("/labs/reports/{rid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_report(rid: uuid.UUID, conn: Connection = CONN, store: RawStore = Depends(get_store)) -> Response:
    """Deletes the report with its values and, if nothing else uses it, the original file."""
    row = conn.execute(
        text("DELETE FROM lab_report WHERE id = :id RETURNING artifact_sha256"), {"id": rid}
    ).first()
    if row:
        drop_artifact_if_unused(conn, store, row.artifact_sha256)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def sniff_media_type(head: bytes) -> str | None:
    if head.startswith(b"%PDF"):
        return "application/pdf"
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def store_upload(conn: Connection, store: RawStore, file: UploadFile) -> tuple[str, str]:
    """Stores a PDF or image in the raw store; (sha256, media type)."""
    from vitalocal.ingest.service import store_artifact

    media = sniff_media_type(file.file.read(16))
    if media is None:
        raise HTTPException(415, "expected a PDF or an image (PNG, JPEG, TIFF, WebP)")
    file.file.seek(0, 2)
    if file.file.tell() > MAX_FILE_BYTES:
        raise HTTPException(413, f"file is larger than {MAX_FILE_BYTES // 1024 // 1024} MB")
    file.file.seek(0)
    return store_artifact(conn, store, file.file, media), media


@router.put("/labs/reports/{rid}/file")
def put_report_file(
    rid: uuid.UUID,
    file: UploadFile,
    conn: Connection = CONN,
    store: RawStore = Depends(get_store),
) -> dict:
    """Attach (or replace) the original PDF or image of a report."""
    old = _report(conn, rid, with_values=False)["artifact_sha256"]
    sha, _ = store_upload(conn, store, file)
    conn.execute(
        text("UPDATE lab_report SET artifact_sha256 = :s, filename = :f, updated_at = now() WHERE id = :id"),
        {"s": sha, "f": (file.filename or "")[:200] or None, "id": rid},
    )
    if old and old != sha:
        drop_artifact_if_unused(conn, store, old)
    return _report(conn, rid, with_values=False)


@router.get("/labs/reports/{rid}/file")
def get_report_file(rid: uuid.UUID, conn: Connection = CONN, store: RawStore = Depends(get_store)):
    """The original file as uploaded."""
    rep = _report(conn, rid, with_values=False)
    sha = rep["artifact_sha256"]
    media = (
        conn.execute(text("SELECT media_type FROM raw_artifact WHERE sha256 = :s"), {"s": sha}).scalar()
        if sha
        else None
    )
    if not sha or not store.exists(sha):
        raise HTTPException(404, "no original file")
    ext = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg", "image/tiff": ".tiff"}.get(
        media, ""
    )
    name = rep["filename"] or f"befund-{rep['collected_on']}{ext}"
    return FileResponse(store.path_for(sha), media_type=media or "application/octet-stream", filename=name)


# --- API: overview & facts ------------------------------------------------------------------------------


@router.get("/labs/overview")
def get_overview(conn: Connection = CONN) -> dict:
    """Per analyte: latest value, lab reference, status, previous value, trend and every value over time
    (in the display unit)."""
    return overview(conn)


@router.get("/labs/analytes/{key}/history")
def get_history(key: str, conn: Connection = CONN) -> dict:
    """One analyte's row of the overview (key = catalog key or `name:<normalized name>`)."""
    rows = [r for g in overview(conn, key=key)["groups"] for r in g["rows"]]
    if not rows:
        raise HTTPException(404, "no values for this analyte")
    return rows[0]


@router.get("/labs/facts")
def get_facts(
    day: date | None = Query(None, description="Reference day (default today); later reports are ignored"),
    new_days: int = Query(
        30, ge=1, le=3650, description="Values of reports within this many days count as new"
    ),
    conn: Connection = CONN,
) -> dict | str:
    """Lab results prepared for agents: new values, out of range, changes, history, lab comments and
    the related supplements (since when, how regularly before the draw)."""
    return lab_facts(conn, day or datetime.now(_tz()).date(), new_days, detail=True)
