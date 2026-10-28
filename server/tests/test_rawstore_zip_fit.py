import hashlib
import os
import stat

from tests.fixtures import make_fit
from vitalocal.ingest.fit_catalog import catalog_fit, is_fit
from vitalocal.ingest.gadgetbridge_zip import classify, parse_comment
from vitalocal.rawstore import RawStore


def test_rawstore_dedupes_and_is_write_once(tmp_path):
    store = RawStore(tmp_path)
    sha, size = store.put_bytes(b"hello")
    assert sha == hashlib.sha256(b"hello").hexdigest() and size == 5
    assert store.put_bytes(b"hello")[0] == sha
    path = store.path_for(sha)
    assert not os.stat(path).st_mode & stat.S_IWUSR
    assert store.verify(sha)
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == [path]  # no temp leftovers


def test_classify_members():
    assert classify("database/Gadgetbridge").category == "gb_database"
    assert classify("preferences/global.json").category == "preferences"
    mi = classify("files/64:A3:37:29:29:67/ACTIVITY/2026/ACTIVITY_x.fit")
    assert (mi.category, mi.device_identifier) == ("ACTIVITY", "64:A3:37:29:29:67")
    assert classify("files/64:A3:37:29:29:67/KPI/KPI_x.bin").category == "KPI"
    assert classify("files/Screenshot.png").category == "other"


def test_parse_comment():
    meta = parse_comment(b"data export from Android application a.b version 0.94.0 from 2026-09-27T22:12:25Z")
    assert meta == {"app": "a.b", "version": "0.94.0", "exported_at": "2026-09-27T22:12:25Z"}
    assert parse_comment(b"") == {}


def test_catalog_fit_reads_header_and_session():
    data = make_fit(sport=10, sub_sport=20)
    assert is_fit(data)
    meta = catalog_fit(data)
    assert "fit_error" not in meta
    assert meta["file_id"]["type"] == "activity"
    assert meta["sessions"][0]["sport"] == "training"
    assert meta["sessions"][0]["sub_sport"] == "strength_training"
    assert meta["message_counts"] == {"file_id": 1, "session": 1}


def test_catalog_fit_records_crc_error():
    meta = catalog_fit(make_fit(corrupt=True))
    assert "CRC" in meta["fit_error"] or "crc" in meta["fit_error"].lower()
