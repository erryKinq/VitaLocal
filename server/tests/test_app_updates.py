"""APK updates from GitHub releases against a mocked GitHub (no network, no database)."""

import hashlib
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

AUTH = {"Authorization": "Bearer test-token"}
APK = b"PK\x03\x04" + b"release apk" * 5000
DL = "https://github.com/erryKinq/VitaLocal/releases/download/v2026.10.28"


def release(name="VitaLocal-1.9.0-37.apk", assets=None):
    if assets is None:
        assets = [
            {"name": name, "browser_download_url": f"{DL}/{name}"},
            {"name": f"{name}.sha256", "browser_download_url": f"{DL}/{name}.sha256"},
            {"name": "notes.txt", "browser_download_url": f"{DL}/notes.txt"},
        ]
    return {"tag_name": "v2026.10.28", "html_url": "https://github.com/x/releases/1", "assets": assets}


@pytest.fixture
def github(settings_env, monkeypatch):
    """A fake GitHub; `state` sets its answers, `calls` records every request."""
    from vitalocal import app_updates

    state = {"release": release(), "apk": APK, "sha": hashlib.sha256(APK).hexdigest(), "status": 200}
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        if req.url.host == "api.github.com":
            if req.url.path != "/repos/erryKinq/VitaLocal/releases/latest":
                return httpx.Response(404, json={"message": "Not Found"})
            if state["status"] != 200:
                return httpx.Response(state["status"], headers={"x-ratelimit-remaining": "0"})
            return httpx.Response(200, json=state["release"])
        if req.url.path.endswith(".sha256"):
            return httpx.Response(200, text=f"{state['sha']}  {req.url.path.rsplit('/', 1)[1][:-7]}\n")
        if req.url.path.endswith(".apk") and req.url.host == "github.com":  # redirected to storage
            return httpx.Response(302, headers={"location": "https://objects.example/apk"})
        if req.url.host == "objects.example":
            return httpx.Response(200, content=state["apk"])
        return httpx.Response(404)

    monkeypatch.setattr(app_updates, "_TRANSPORT", httpx.MockTransport(handler))
    return state, calls


def client():
    from vitalocal.main import app

    return TestClient(app)


def test_on_by_default_and_no_network_when_off(github):
    from vitalocal import app_updates

    _, calls = github
    c = client()
    cfg = c.get("/api/v1/app/android/github", headers=AUTH).json()
    assert cfg == {"enabled": True, "repo": "erryKinq/VitaLocal", "last_check_at": None, "last_result": None}
    assert app_updates.due()  # a fresh server checks on its own
    body = {"enabled": False, "repo": "erryKinq/VitaLocal"}
    off = c.put("/api/v1/app/android/github", headers=AUTH, json=body)
    assert off.status_code == 200 and off.json()["enabled"] is False
    assert not app_updates.due()
    assert c.post("/api/v1/app/android/github/check", headers=AUTH).status_code == 409
    assert calls == []


def test_check_downloads_verifies_and_publishes(github):
    _, calls = github
    c = client()
    on = {"enabled": True, "repo": "erryKinq/VitaLocal"}
    r = c.put("/api/v1/app/android/github", headers=AUTH, json=on)
    assert r.status_code == 200 and r.json()["enabled"] is True
    cfg = c.post("/api/v1/app/android/github/check", headers=AUTH).json()
    res = cfg["last_result"]
    assert res["status"] == "updated", res
    assert (res["version"], res["version_code"], res["release"]) == ("1.9.0", 37, "v2026.10.28")
    assert cfg["last_check_at"]

    meta = c.get("/api/v1/app/android", headers=AUTH).json()
    assert (meta["version"], meta["version_code"], meta["source"]) == ("1.9.0", 37, "github")
    assert meta["size"] == len(APK)
    assert meta["sha256"] == hashlib.sha256(APK).hexdigest()
    assert c.get("/api/v1/app/android/apk", headers=AUTH).content == APK
    assert not any("notes.txt" in u for u in calls)

    # the same release again: nothing downloaded
    calls.clear()
    res = c.post("/api/v1/app/android/github/check", headers=AUTH).json()["last_result"]
    assert res["status"] == "up_to_date"
    assert calls == ["https://api.github.com/repos/erryKinq/VitaLocal/releases/latest"]


def test_older_release_than_upload_is_kept(github):
    from vitalocal import app_updates

    c = client()
    form = {"version": "2.0.0", "version_code": "40"}
    up = c.put("/api/v1/app/android", headers=AUTH, data=form, files={"file": ("a.apk", b"PK" + b"x" * 100)})
    assert up.status_code == 200
    app_updates.configure(True, "erryKinq/VitaLocal")
    assert app_updates.check()["last_result"]["status"] == "up_to_date"
    assert c.get("/api/v1/app/android", headers=AUTH).json()["version_code"] == 40


def test_checksum_mismatch_is_not_published(github):
    from vitalocal import app_updates
    from vitalocal.apk import app_dir

    state, _ = github
    state["sha"] = "0" * 64
    app_updates.configure(True, "erryKinq/VitaLocal")
    res = app_updates.check()["last_result"]
    assert res["status"] == "error" and "checksum" in res["message"]
    assert client().get("/api/v1/app/android", headers=AUTH).json() is None
    assert not (app_dir() / "VitaLocal.apk.tmp").exists()


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"release": release(assets=[])}, ("no_apk", None)),
        ({"release": release(assets=[{"name": "VitaLocal-1.9.0-37.apk", "browser_download_url": f"{DL}/a"}])},
         ("error", "no checksum")),
        ({"status": 403}, ("error", "rate limit")),
        ({"apk": b"<html>not an apk</html>"}, ("error", "not an APK")),
    ],
)
def test_check_failures(github, change, expected):
    from vitalocal import app_updates

    state, _ = github
    state.update(change)
    if "apk" in change:
        state["sha"] = hashlib.sha256(change["apk"]).hexdigest()
    app_updates.configure(True, "erryKinq/VitaLocal")
    res = app_updates.check()["last_result"]
    assert res["status"] == expected[0], res
    if expected[1]:
        assert expected[1] in res["message"]
    assert client().get("/api/v1/app/android", headers=AUTH).json() is None


def test_unknown_repo_and_validation(github):
    from vitalocal import app_updates

    c = client()
    bad = c.put("/api/v1/app/android/github", headers=AUTH, json={"enabled": True, "repo": "https://evil/x"})
    assert bad.status_code == 422
    app_updates.configure(True, "someone/else")
    res = app_updates.check()["last_result"]
    assert (res["repo"], res["status"]) == ("someone/else", "error")
    assert res["message"] == "no published release in someone/else (or no such repository)"
    # another repository forgets the last result
    assert app_updates.configure(True, "erryKinq/VitaLocal")["last_result"] is None


def test_due_once_a_day(github):
    from vitalocal import app_updates

    app_updates.configure(True, "erryKinq/VitaLocal")
    assert app_updates.due()
    last = datetime.fromisoformat(app_updates.check()["last_check_at"])
    assert not app_updates.due(last + timedelta(hours=23))
    assert app_updates.due(last + timedelta(days=1, minutes=1))
    app_updates.configure(False, "erryKinq/VitaLocal")
    assert not app_updates.due(datetime.now(UTC) + timedelta(days=30))
