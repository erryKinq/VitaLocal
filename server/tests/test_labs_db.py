"""Lab results: manual reports, overview with conversion and history, targets, synonyms, facts, import.

All values are made up.
"""

import io
import json
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from vitalocal import ai

pytestmark = pytest.mark.db
AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture
def client(engine, store):
    from vitalocal.main import app

    return TestClient(app)


def _pdf(lines: list[str]) -> bytes:
    """A one-page PDF with a text layer (Helvetica), enough for pdfium."""
    stream = "BT /F1 11 Tf 50 780 Td 14 TL " + " ".join(f"({x}) '" for x in lines) + " ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def _report(client, **body) -> dict:
    r = client.post("/api/v1/labs/reports", headers=AUTH, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _row(overview: dict, key: str) -> dict:
    return next(r for g in overview["groups"] for r in g["rows"] if r["key"] == key)


FIRST = {
    "collected_on": "2026-03-02",
    "lab": "Labor A",
    "values": [
        {
            "name": "Cholesterin, gesamt",
            "material": "Serum",
            "value": "4,40",
            "unit": "mmol/L",
            "reference": "< 5,2",
        },
        {"name": "25-OH-Vitamin D", "value": "30", "unit": "ng/ml", "reference": "30 - 100"},
        {
            "name": "Magnesium",
            "material": "EDTA-Blut",
            "value": "1,50",
            "unit": "mmol/L",
            "reference": "1.3 - 1.8",
        },
        {"name": "CRP", "value": "<0,5", "unit": "mg/l", "reference": "< 5"},
        {"name": "Ferritin", "value": "18", "unit": "ng/ml", "reference": "30 - 400", "flag": "L"},
        {
            "name": "Spezialtest",
            "value": "12",
            "unit": "U/l",
            "reference": "< 10",
            "note": "Kommentar des Labors",
        },
    ],
}


def test_manual_report_overview_units_and_targets(client):
    rep = _report(client, **FIRST)
    by = {v["name"]: v for v in rep["values"]}
    assert by["Cholesterin, gesamt"]["analyte_key"] == "cholesterol"
    assert by["Cholesterin, gesamt"]["printed"] == "4.40" and by["Cholesterin, gesamt"]["status"] == "normal"
    assert by["Magnesium"]["analyte_key"] == "magnesium_wb"  # whole blood, not serum
    assert (by["CRP"]["comparator"], by["CRP"]["value"], by["CRP"]["status"]) == ("<", 0.5, "normal")
    assert (by["Ferritin"]["status"], by["Ferritin"]["status_source"]) == ("low", "flag")
    assert by["Spezialtest"]["analyte_key"] is None and by["Spezialtest"]["status"] == "high"
    vd = by["25-OH-Vitamin D"]
    assert vd["display"] == {"unit": "nmol/l", "value": 74.9, "ref_low": 74.9, "ref_high": 250.0}

    second = _report(
        client,
        collected_on="2026-09-01",
        lab="Labor B",
        values=[
            {"name": "25-Hydroxy-Vitamin D", "value": "120", "unit": "nmol/L", "reference": "> 75"},
            {"name": "Cholesterin", "value": "212", "unit": "mg/dl", "reference": "< 200"},
        ],
    )
    ov = client.get("/api/v1/labs/overview", headers=AUTH).json()
    assert [g["key"] for g in ov["groups"]][:2] == ["lipids", "vitamins"]
    d = _row(ov, "vitamin_d")
    assert (d["latest"]["y"], d["previous"]["y"], d["trend"], d["count"]) == (120.0, 74.9, "up", 2)
    assert d["latest"]["report_id"] == second["id"] and d["unit"] == "nmol/l"
    chol = _row(ov, "cholesterol")
    assert chol["latest"]["y"] == 5.48 and chol["status"] == "high"  # 212 mg/dl, lab says < 200
    assert _row(ov, "name:spezialtest")["label"] == "Spezialtest"

    # conventional units, per-analyte override
    assert client.put("/api/v1/settings/labs", headers=AUTH, json={"system": "conventional",
                      "units": {"cholesterol": "mmol/l"}}).status_code == 200  # fmt: skip
    ov = client.get("/api/v1/labs/overview", headers=AUTH).json()
    assert _row(ov, "vitamin_d")["unit"] == "ng/ml" and _row(ov, "vitamin_d")["latest"]["y"] == 48.1
    assert _row(ov, "cholesterol")["unit"] == "mmol/l"
    bad = {"units": {"cholesterol": "g"}}
    assert client.put("/api/v1/settings/labs", headers=AUTH, json=bad).status_code == 422
    client.put("/api/v1/settings/labs", headers=AUTH, json={})

    # personal target in another unit than the value
    r = client.put(
        "/api/v1/labs/targets/vitamin_d",
        headers=AUTH,
        json={"min_value": 40, "max_value": 60, "unit": "ng/ml"},
    )
    assert r.status_code == 200, r.text
    d = _row(client.get("/api/v1/labs/overview", headers=AUTH).json(), "vitamin_d")
    assert (d["target"]["min"], d["target"]["max"], d["target_status"]) == (99.8, 150.0, "normal")
    assert (
        client.put(
            "/api/v1/labs/targets/vitamin_d", headers=AUTH, json={"min_value": 5, "max_value": 1}
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/api/v1/labs/targets/ldl", headers=AUTH, json={"max_value": 2.6, "unit": "U/l"}
        ).status_code
        == 422
    )
    hist = client.get("/api/v1/labs/analytes/vitamin_d/history", headers=AUTH).json()
    assert [p["date"] for p in hist["points"]] == ["2026-03-02", "2026-09-01"]

    reports = client.get("/api/v1/labs/reports", headers=AUTH).json()
    assert [(r["lab"], r["n_values"], r["n_outside"]) for r in reports] == [
        ("Labor B", 2, 1),
        ("Labor A", 6, 2),
    ]


def test_report_updates_are_full_state_and_repeatable(client):
    rid = str(uuid.uuid4())
    body = {**FIRST, "id": rid}
    _report(client, **body)
    again = client.post("/api/v1/labs/reports", headers=AUTH, json=body)
    assert again.status_code == 201 and len(again.json()["values"]) == 6

    body["values"] = body["values"][:2]
    body["lab"] = "Labor A2"
    r = client.put(f"/api/v1/labs/reports/{rid}", headers=AUTH, json=body).json()
    assert r["lab"] == "Labor A2" and len(r["values"]) == 2

    bad = {**body, "values": [{"name": "X", "value": ""}]}
    assert client.put(f"/api/v1/labs/reports/{rid}", headers=AUTH, json=bad).status_code == 422
    bad = {**body, "values": [{"name": "X", "value": "1", "analyte_key": "nope"}]}
    assert client.put(f"/api/v1/labs/reports/{rid}", headers=AUTH, json=bad).status_code == 422

    assert client.delete(f"/api/v1/labs/reports/{rid}", headers=AUTH).status_code == 204
    assert client.get(f"/api/v1/labs/reports/{rid}", headers=AUTH).status_code == 404


def test_hand_assigned_names_are_learned_and_own_analytes(client):
    _report(client, collected_on="2026-01-01",
            values=[{"name": "Vit. D (25-OH)", "analyte_key": "vitamin_d", "value": "35",
                     "unit": "ng/ml"}])  # fmt: skip
    rep = _report(
        client, collected_on="2026-02-01", values=[{"name": "Vit. D (25-OH)", "value": "38", "unit": "ng/ml"}]
    )
    assert rep["values"][0]["analyte_key"] == "vitamin_d"
    cat = client.get("/api/v1/labs/catalog", headers=AUTH).json()
    assert {"name_norm": "25 d oh vit", "material": None, "analyte_key": "vitamin_d"} in cat[
        "learned_synonyms"
    ]

    own = client.post(
        "/api/v1/labs/analytes", headers=AUTH, json={"label": "Spezialtest", "unit": "U/l"}
    ).json()
    assert own["key"] == "x_spezialtest"
    assert (
        client.post(
            "/api/v1/labs/analytes", headers=AUTH, json={"label": "Spezialtest", "unit": "U/l"}
        ).status_code
        == 409
    )
    rep = _report(
        client, collected_on="2026-02-01", values=[{"name": "Spezialtest", "value": "12", "unit": "U/l"}]
    )
    assert rep["values"][0]["analyte_key"] == "x_spezialtest"
    assert client.delete("/api/v1/labs/analytes/x_spezialtest", headers=AUTH).status_code == 204
    assert (
        client.get(f"/api/v1/labs/reports/{rep['id']}", headers=AUTH).json()["values"][0]["analyte_key"]
        is None
    )
    assert client.delete("/api/v1/labs/analytes/vitamin_d", headers=AUTH).status_code == 404


def test_facts_for_summary_and_agents(client):
    sup = client.post("/api/v1/supplements", headers=AUTH, json={
        "name": "Vitamin D3", "form": "drops", "per_day": 1, "started_on": "2026-01-15",
        "ingredients": [{"key": "vitamin_d", "amount": 1000}]}).json()  # fmt: skip
    assert sup["started_on"] == "2026-01-15"
    for d in ("2026-08-20", "2026-08-21", "2026-08-22"):
        client.put(f"/api/v1/supplements/{sup['id']}/log/{d}", headers=AUTH, json={"count": 1})
    _report(client, **FIRST)
    _report(client, collected_on="2026-09-01", lab="Labor B",
            values=[{"name": "25-OH-Vitamin D", "value": "120", "unit": "nmol/L", "reference": "> 75"},
                    {"name": "Cholesterin", "value": "212", "unit": "mg/dl",
                     "reference": "< 200"}])  # fmt: skip

    f = client.get("/api/v1/labs/facts", headers=AUTH, params={"day": "2026-09-05", "new_days": 10}).json()
    assert f["latest_report"] == {"date": "2026-09-01", "lab": "Labor B", "days_ago": 4}
    new = f["new_values_last_10_days"]
    assert [n["analyte"] for n in new] == ["Cholesterin, gesamt", "25-OH-Vitamin D"]
    chol, vd = new
    assert (chol["value"], chol["as_printed"], chol["lab_reference"]) == (
        "5.48 mmol/l",
        "212 mg/dl",
        "< 200 mg/dl",
    )
    assert (
        vd["value"] == "120 nmol/l"
        and vd["previous"] == "74.9 nmol/l (2026-03-02)"
        and vd["change_pct"] == 60
    )
    assert vd["lab_reference"] == "> 75 nmol/L" and "as_printed" not in vd
    ferritin = next(o for o in f["latest_outside_lab_reference"] if o["analyte"] == "Ferritin")
    assert ferritin["value"] == "18 µg/l" and "as_printed" not in ferritin  # ng/ml = µg/l
    assert vd["related_supplements_before_draw"] == [
        {"supplement": "Vitamin D3", "taking_since": "2026-01-15", "days_complete_in_90_days_before_draw": 3}
    ]
    assert [h["date"] for h in vd["history"]] == ["2026-03-02", "2026-09-01"]
    outside = {o["analyte"] for o in f["latest_outside_lab_reference"]}
    assert outside == {"Cholesterin, gesamt", "Ferritin", "Spezialtest"}
    assert next(o for o in f["latest_outside_lab_reference"] if o["analyte"] == "Spezialtest")["lab_comment"]
    assert f["supplements_taken"][0]["since_source"] == "start date"

    # before any report: nothing; the day facts carry the compact version
    early = client.get("/api/v1/labs/facts", headers=AUTH, params={"day": "2026-01-01"}).json()
    assert early == "no lab results recorded"
    from vitalocal import ai_facts
    from vitalocal.db import get_engine

    with get_engine().connect() as conn:
        day = ai_facts.day_facts(conn, __import__("datetime").date(2026, 9, 5))
        week = ai_facts.week_facts(conn, __import__("datetime").date(2026, 9, 5))
    assert day["lab_results"]["new_values_last_14_days"][1]["analyte"] == "25-OH-Vitamin D"
    assert "history" not in day["lab_results"]["new_values_last_14_days"][1]
    assert week["lab_results"]["new_values_last_7_days"][1]["value"] == "120 nmol/l"


# --- import -------------------------------------------------------------------------------------------


PDF_LINES = [
    "Befund 02.05.2026 Abnahme 30.04.2026",
    "03 Triglyceride 1,10 mmol/L < 2,3",
    "03 25-Hydroxy-Vitamin D 88 nmol/L > 75",
    "01 Magnesium 1,45 mmol/L 1.3 - 1.8",
]
MODEL_ANSWER = {
    "lab": "Testlabor",
    "collected_on": "30.04.2026",
    "reported_on": "2026-05-02",
    "values": [
        {"name": "Triglyceride", "material": "Serum", "method": "PHOT", "value": "1,10", "unit": "mmol/L",
         "reference": "< 2,3", "flag": None, "note": None, "source_line": PDF_LINES[1]},
        {"name": "25-Hydroxy-Vitamin D", "material": "Serum", "method": None, "value": "88", "unit": "nmol/L",
         "reference": "> 75", "flag": None, "note": "< 50 nmol/L Defizienz", "source_line": PDF_LINES[2]},
        {"name": "Magnesium", "material": "EDTA-Blut", "method": None, "value": "1,45", "unit": "mmol/L",
         "reference": "1.3 - 1.8", "flag": None, "note": None, "source_line": PDF_LINES[3]},
        {"name": "Kalium", "material": "Serum", "method": None, "value": "4,9", "unit": "mmol/L",
         "reference": "3,5 - 5,1", "flag": None, "note": None, "source_line": "?"},  # not on the page
    ],
}  # fmt: skip


@pytest.fixture
def model(client, monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        if "response_format" in body and calls[0] is body:
            return httpx.Response(400, json={"error": "response_format not supported"})
        content = "```json\n" + json.dumps(MODEL_ANSWER) + "\n```"
        return httpx.Response(200, json={"model": "vl-test", "choices": [{"message": {"content": content}}]})

    monkeypatch.setattr(ai, "TRANSPORT", httpx.MockTransport(handler))
    return calls


def _setup_model(client, vision=True):
    r = client.put(
        "/api/v1/settings/ai",
        headers=AUTH,
        json={"enabled": True, "base_url": "http://model.test/v1", "model": "vl-test", "vision": vision},
    )
    assert r.status_code == 200


def test_import_needs_a_model_and_a_pdf_or_image(client):
    files = {"file": ("b.pdf", _pdf(PDF_LINES), "application/pdf")}
    assert client.post("/api/v1/labs/imports", headers=AUTH, files=files).status_code == 409
    _setup_model(client, vision=False)  # a model that cannot read images is not offered
    r = client.post("/api/v1/labs/imports", headers=AUTH, files=files)
    assert r.status_code == 409 and "images" in r.json()["detail"]
    _setup_model(client)
    files = {"file": ("b.txt", b"hello", "text/plain")}
    assert client.post("/api/v1/labs/imports", headers=AUTH, files=files).status_code == 415


def test_import_review_confirm(client, model, store, engine):
    _setup_model(client)
    r = client.post(
        "/api/v1/labs/imports",
        headers=AUTH,
        files={"file": ("befund.pdf", _pdf(PDF_LINES), "application/pdf")},
    )
    assert r.status_code == 202, r.text
    iid = r.json()["id"]

    # 400 for response_format → asked again without; image and text layer went along
    assert "response_format" in model[0] and "response_format" not in model[1]
    parts = model[1]["messages"][1]["content"]
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "Triglyceride 1,10" in parts[0]["text"] and model[1]["temperature"] == 0

    imp = client.get(f"/api/v1/labs/imports/{iid}", headers=AUTH).json()
    assert imp["status"] == "ready" and imp["model"] == "vl-test" and imp["pages"] == 1
    p = imp["proposal"]
    assert (p["lab"], p["collected_on"], p["reported_on"]) == ("Testlabor", "2026-04-30", "2026-05-02")
    vals = {v["name"]: v for v in p["values"]}
    assert vals["Magnesium"]["analyte_key"] == "magnesium_wb" and vals["Magnesium"]["found_in_text"] is True
    assert vals["Triglyceride"]["status"] == "normal" and vals["Triglyceride"]["ref_high"] == 2.3
    assert (
        vals["Kalium"]["found_in_text"] is False
        and "value not found in the PDF text" in vals["Kalium"]["warnings"]
    )
    assert client.get("/api/v1/labs/reports", headers=AUTH).json() == []  # nothing saved yet

    page = client.get(f"/api/v1/labs/imports/{iid}/pages/1", headers=AUTH)
    assert page.status_code == 200 and page.content.startswith(b"\x89PNG")
    assert client.get(f"/api/v1/labs/imports/{iid}/pages/2", headers=AUTH).status_code == 404
    assert [i["n_values"] for i in client.get("/api/v1/labs/imports", headers=AUTH).json()] == [4]

    # same file again while open → the same import
    again = client.post(
        "/api/v1/labs/imports", headers=AUTH, files={"file": ("x.pdf", _pdf(PDF_LINES), "application/pdf")}
    )
    assert again.status_code == 200 and again.json()["id"] == iid

    confirmed = [v for v in p["values"] if v["name"] != "Kalium"]
    body = {"collected_on": p["collected_on"], "reported_on": p["reported_on"], "lab": p["lab"],
            "values": [{k: v[k] for k in ("analyte_key", "name", "material", "method", "value", "unit",
                                          "reference", "flag", "note")} for v in confirmed]}  # fmt: skip
    assert (
        client.post(
            f"/api/v1/labs/imports/{iid}/confirm", headers=AUTH, json={**body, "values": []}
        ).status_code
        == 422
    )
    rep = client.post(f"/api/v1/labs/imports/{iid}/confirm", headers=AUTH, json=body)
    assert rep.status_code == 201, rep.text
    rep = rep.json()
    assert rep["source"] == "import" and rep["has_file"] and len(rep["values"]) == 3
    assert (
        client.post(f"/api/v1/labs/imports/{iid}/confirm", headers=AUTH, json=body).json()["id"] == rep["id"]
    )
    assert client.get("/api/v1/labs/imports", headers=AUTH).json() == []
    f = client.get(f"/api/v1/labs/reports/{rep['id']}/file", headers=AUTH)
    assert f.status_code == 200 and f.content.startswith(b"%PDF")

    # retention keeps the report's file
    from vitalocal.ingest import retention

    assert retention.prune(engine, store, 7, dry_run=True)["artifacts"] == 0
    # deleting the report deletes the file as well
    sha = client.get(f"/api/v1/labs/imports/{iid}", headers=AUTH).json()["artifact_sha256"]
    client.delete(f"/api/v1/labs/imports/{iid}", headers=AUTH)
    assert store.exists(sha)
    client.delete(f"/api/v1/labs/reports/{rep['id']}", headers=AUTH)
    assert not store.exists(sha)


def test_image_import_failure_retry_and_discard(client, monkeypatch, store):
    from PIL import Image

    _setup_model(client)
    monkeypatch.setattr(ai, "TRANSPORT", httpx.MockTransport(lambda r: httpx.Response(500, text="boom")))
    buf = io.BytesIO()
    Image.new("L", (400, 600), 255).save(buf, "TIFF")
    r = client.post(
        "/api/v1/labs/imports", headers=AUTH, files={"file": ("scan.tiff", buf.getvalue(), "image/tiff")}
    )
    iid = r.json()["id"]
    imp = client.get(f"/api/v1/labs/imports/{iid}", headers=AUTH).json()
    assert imp["status"] == "failed" and "500" in imp["error"]
    assert client.post(f"/api/v1/labs/imports/{iid}/confirm", headers=AUTH, json=FIRST).status_code == 409

    answer = {"lab": None, "collected_on": None, "reported_on": None, "values": [
        {"name": "Ferritin", "material": None, "method": None, "value": "45", "unit": "µg/l",
         "reference": "30 - 400", "flag": None, "note": None,
         "source_line": "Ferritin 45 µg/l 30 - 400"}]}  # fmt: skip
    ok = httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(answer)}}]})
    monkeypatch.setattr(ai, "TRANSPORT", httpx.MockTransport(lambda r: ok))
    assert client.post(f"/api/v1/labs/imports/{iid}/retry", headers=AUTH).status_code == 202
    imp = client.get(f"/api/v1/labs/imports/{iid}", headers=AUTH).json()
    assert imp["status"] == "ready" and imp["proposal"]["values"][0]["found_in_text"] is None  # no text layer
    assert imp["proposal"]["collected_on"] is None

    sha = imp["artifact_sha256"]
    assert client.delete(f"/api/v1/labs/imports/{iid}", headers=AUTH).status_code == 204
    assert not store.exists(sha)
