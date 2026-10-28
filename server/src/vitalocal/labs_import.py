"""Reading a lab report with the local model: pages → JSON per page → a proposal for the review.

A PDF is rendered page by page with pdfium (plus its text layer, if it has one); images (photo,
scan, multi-page TIFF) are used as they are. Each page goes to the configured OpenAI-compatible
model with a JSON schema. The model only transcribes: values exactly as printed, no conversion,
no judgement, no personal data. VitaLocal then parses values and ranges, matches the catalog and
adds checks (value found in the text layer, unit convertible, material unclear). The result is a
proposal; only the review (`POST /labs/imports/{id}/confirm`) turns it into a report.
"""

from __future__ import annotations

import io
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import Connection, Engine, text

from vitalocal import ai, labs
from vitalocal.rawstore import RawStore

log = logging.getLogger(__name__)

MAX_PAGES = 8
LONG_SIDE = 1800  # px; enough for small print, small enough for local vision models
PROMPT_VERSION = 1

_STR = {"type": "string"}
_OPT = {"type": ["string", "null"]}
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["lab", "collected_on", "reported_on", "values"],
    "properties": {
        "lab": {**_OPT, "description": "Name of the laboratory, without address"},
        "collected_on": {**_OPT, "description": "Date the sample was taken (Abnahme/Entnahme), YYYY-MM-DD"},
        "reported_on": {**_OPT, "description": "Date of the report (Befund), YYYY-MM-DD"},
        "values": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "name",
                    "material",
                    "method",
                    "value",
                    "unit",
                    "reference",
                    "flag",
                    "note",
                    "source_line",
                ],  # fmt: skip
                "properties": {
                    "name": _STR,
                    "material": _OPT,
                    "method": _OPT,
                    "value": _STR,
                    "unit": _OPT,
                    "reference": _OPT,
                    "flag": _OPT,
                    "note": _OPT,
                    "source_line": _STR,
                },
            },
        },
    },
}

SYSTEM = """Du überträgst Laborbefunde in JSON. Du liest genau ab und bewertest nichts.

Regeln:
- Nimm nur Messergebnisse auf: Zeilen mit Untersuchung und Ergebnis. Tabellenköpfe, Materialtabellen,
  Anschreiben und Fußzeilen sind keine Werte.
- Übernimm keine persönlichen Daten: keinen Namen, kein Geburtsdatum, keine Adresse, keine Labor-,
  Auftrags- oder Patientennummer, keinen Barcode.
- value: genau wie gedruckt, mit Vergleichszeichen (<, >) und Dezimalkomma. Nicht umrechnen, nicht runden.
  Qualitative Ergebnisse (z. B. "negativ") als Text.
- name: die Untersuchung wie gedruckt, aber ohne hochgestellte Fußnoten-Kürzel (aus "MagnesiumCB" wird
  "Magnesium"); ein solches Kürzel gehört an den Anfang von note.
- material: das Material der Probe, aus der der Wert stammt. Oft steht vor dem Wert eine Probennummer,
  die in einer Materialtabelle aufgelöst wird (z. B. 01 = EDTA-Blut, 03 = Serum). Unbekannt: null.
- method: Methodenkürzel, falls gedruckt (z. B. PHOT, ECLIA), sonst null.
- unit: die Einheit wie gedruckt, sonst null.
- reference: der Referenzbereich wie gedruckt (z. B. "< 2,3", "1.3 - 1.8", "> 75"). Grafische Balken
  oder Marker ignorierst du.
- flag: nur ein gedrucktes Kennzeichen (H, L, +, -, ↑, ↓, *), sonst null. Setze nie selbst eines.
- note: Kommentar des Labors direkt zu diesem Wert (z. B. therapeutische Zielwerte, Einstufungen wie
  "< 50 nmol/L Defizienz"). Zahlen und Einstufungen vollständig, lange Fließtexte knapp; sonst null.
- source_line: die gedruckte Zeile dieses Werts als Text.
- lab, collected_on (Datum der Abnahme bzw. Entnahme), reported_on (Befunddatum): nur wenn auf dieser
  Seite gedruckt, sonst null. Datumsangaben als YYYY-MM-DD.
- Was du nicht sicher lesen kannst, lässt du weg. Erfinde nichts.

Antworte ausschließlich mit einem JSON-Objekt nach diesem Schema:
"""


@dataclass
class Page:
    image: bytes  # PNG
    text: str  # PDF text layer ("" for images and scans)


def _png(img) -> bytes:
    img.thumbnail((LONG_SIDE, LONG_SIDE))
    if img.mode not in ("L", "RGB"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _pdf_pages(data: bytes, only: int | None = None) -> tuple[list[Page], int]:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    try:
        total = len(pdf)
        out = []
        for i in range(min(total, MAX_PAGES)):
            if only is not None and i != only:
                continue
            page = pdf[i]
            w, h = page.get_size()
            bitmap = page.render(scale=LONG_SIDE / max(w, h, 1))
            textpage = page.get_textpage()
            out.append(Page(_png(bitmap.to_pil()), textpage.get_text_bounded() or ""))
            textpage.close()
            page.close()
        return out, total
    finally:
        pdf.close()


def _image_pages(data: bytes, only: int | None = None) -> tuple[list[Page], int]:
    from PIL import Image, ImageOps, ImageSequence

    img = Image.open(io.BytesIO(data))
    frames = [ImageOps.exif_transpose(f.copy()) for f in ImageSequence.Iterator(img)][:MAX_PAGES]
    total = getattr(img, "n_frames", 1)
    return [Page(_png(f), "") for i, f in enumerate(frames) if only is None or i == only], total


def pages(data: bytes, media_type: str | None, only: int | None = None) -> tuple[list[Page], int]:
    """Rendered pages (at most MAX_PAGES) and the document's page count."""
    if media_type == "application/pdf":
        return _pdf_pages(data, only)
    return _image_pages(data, only)


# --- model call ---------------------------------------------------------------------------------------


def _user_text(n: int, total: int, page_text: str) -> str:
    out = f"Seite {n} von {total}."
    if page_text.strip():
        out += (
            "\n\nTextebene der PDF-Seite (zur Kontrolle beim Ablesen; maßgeblich ist das Bild):\n"
            + page_text.strip()[:8000]
        )
    return out


def _clean(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _day(v) -> str | None:
    s = _clean(v)
    if not s:
        return None
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s) or re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", s)
    if not m:
        return None
    y, mo, d = (m.group(1), m.group(2), m.group(3)) if "-" in s else (m.group(3), m.group(2), m.group(1))
    try:
        return date(int(y), int(mo), int(d)).isoformat()
    except ValueError:
        return None


def extract(settings: ai.AiSettings, doc: list[Page], total: int) -> dict:
    """Asks the model page by page; returns the merged transcription and per-page errors."""
    header = {"lab": None, "collected_on": None, "reported_on": None}
    values, errors, model = [], [], settings.model
    system = SYSTEM + json.dumps(SCHEMA, ensure_ascii=False)
    for n, page in enumerate(doc, start=1):
        try:
            content, model = ai.chat(
                settings, system, _user_text(n, total, page.text), images=[page.image], json_schema=SCHEMA,
                temperature=0,
            )  # fmt: skip
            obj = ai.parse_json(content)
        except ai.AiError as e:
            errors.append({"page": n, "error": str(e)})
            continue
        for k in header:
            if header[k] is None:
                header[k] = _day(obj.get(k)) if k != "lab" else _clean(obj.get(k))
        for v in obj.get("values") or []:
            if not isinstance(v, dict) or not _clean(v.get("name")) or _clean(v.get("value")) is None:
                continue
            values.append({
                "page": n,
                **{k: _clean(v.get(k)) for k in ("name", "material", "method", "value", "unit", "reference",
                                                 "flag", "note", "source_line")},
            })  # fmt: skip
    return {**header, "values": values, "page_errors": errors, "model": model}


# --- proposal -----------------------------------------------------------------------------------------


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s).replace(",", ".")


def build_proposal(conn: Connection, raw: dict, doc: list[Page], sha: str) -> dict:
    """Parsed values with catalog match, status and checks for the review."""
    from vitalocal.api.labs import Matcher

    m = Matcher(conn)
    texts = [_squash(p.text) for p in doc]
    out = []
    for i, v in enumerate(raw["values"]):
        p = labs.parse_value(v["value"])
        key, match_warning = m.match(v["name"], v["material"], v["unit"])
        low, high = labs.parse_reference(v["reference"])
        warnings = []
        if match_warning:
            warnings.append(match_warning)
        if key is None:
            warnings.append("not in the catalog")
        a = m.analytes.get(key) if key else None
        if a and v["unit"] and labs.convert(1.0, v["unit"], a.unit, a) is None:
            warnings.append(f"unit {v['unit']} does not convert to {a.unit}")
        if p.value is None and not p.text:
            warnings.append("value not readable")
        if v["reference"] and low is None and high is None:
            warnings.append("reference range not numeric")
        page_text = texts[v["page"] - 1] if v["page"] - 1 < len(texts) else ""
        found = None
        if page_text and p.value is not None:
            found = _squash(v["value"]).lstrip("<>=≤≥") in page_text
            if not found:
                warnings.append("value not found in the PDF text")
        status = labs.flag_status(v["flag"]) or labs.range_status(p.value, p.comparator, low, high)
        out.append({
            "idx": i,
            **v,
            "analyte_key": key,
            "analyte": a.label if a else None,
            "comparator": p.comparator,
            "parsed_value": float(p.value) if p.value is not None else None,
            "value_text": p.text,
            "ref_low": float(low) if low is not None else None,
            "ref_high": float(high) if high is not None else None,
            "status": status,
            "found_in_text": found,
            "warnings": warnings,
        })  # fmt: skip
    dup = conn.execute(
        text("""SELECT id, collected_on, lab FROM lab_report
                WHERE artifact_sha256 = :s
                   OR (collected_on = CAST(:d AS date) AND lab IS NOT DISTINCT FROM :l)"""),
        {"s": sha, "d": raw["collected_on"], "l": raw["lab"]},
    )
    return {
        "lab": raw["lab"],
        "collected_on": raw["collected_on"],
        "reported_on": raw["reported_on"],
        "values": out,
        "page_errors": raw["page_errors"],
        "possible_duplicates": [dict(r._mapping) for r in dup],
        "prompt_version": PROMPT_VERSION,
    }


def run(engine: Engine, store: RawStore, import_id) -> None:
    """Background task: read the upload with the model and store the proposal (or the error)."""
    with engine.begin() as conn:
        row = conn.execute(
            text("""UPDATE lab_import SET status = 'running', error = NULL, started_at = now() WHERE id = :id
                    RETURNING artifact_sha256, media_type"""),
            {"id": import_id},
        ).first()
        settings = ai.load_settings(conn)
    if row is None:
        return
    started = time.monotonic()
    result: dict = {}
    try:
        data = store.path_for(row.artifact_sha256).read_bytes()
        doc, total = pages(data, row.media_type)
        raw = extract(settings, doc, total)
        if not raw["values"] and raw["page_errors"]:
            raise ai.AiError("; ".join(f"page {e['page']}: {e['error']}" for e in raw["page_errors"]))
        with engine.connect() as conn:
            proposal = build_proposal(conn, raw, doc, row.artifact_sha256)
        if total > MAX_PAGES:
            proposal["page_errors"].append(
                {"page": MAX_PAGES + 1, "error": f"only the first {MAX_PAGES} pages read"}
            )
        result = {"status": "ready", "proposal": json.dumps(proposal, default=str), "model": raw["model"],
                  "pages": total, "error": None}  # fmt: skip
    except Exception as e:  # noqa: BLE001 - any failure ends up visible on the import
        log.warning("lab import %s failed: %s", import_id, e)
        error = str(e) if isinstance(e, ai.AiError) else f"{type(e).__name__}: {e}"
        result = {
            "status": "failed",
            "proposal": None,
            "model": settings.model,
            "pages": None,
            "error": error,
        }
    with engine.begin() as conn:
        conn.execute(
            text("""UPDATE lab_import SET status = :status, proposal = CAST(:proposal AS jsonb),
                        model = :model, pages = COALESCE(:pages, pages), error = :error, duration_ms = :ms,
                        finished_at = :now
                    WHERE id = :id AND status = 'running'"""),
            {
                **result,
                "ms": int((time.monotonic() - started) * 1000),
                "now": datetime.now(UTC),
                "id": import_id,
            },
        )
