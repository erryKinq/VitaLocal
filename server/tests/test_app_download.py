"""Publishing the Android APK and downloading it (no database needed)."""

import hashlib

from fastapi.testclient import TestClient

AUTH = {"Authorization": "Bearer test-token"}
APK = b"PK\x03\x04" + b"fake apk" * 1000


def test_publish_and_download_apk(settings_env):
    from vitalocal.main import app

    c = TestClient(app)
    assert c.get("/api/v1/app/android", headers=AUTH).json() is None
    assert c.get("/api/v1/app/android/apk", headers=AUTH).status_code == 404

    form = {"version": "0.3.2", "version_code": "8"}
    bad = c.put("/api/v1/app/android", headers=AUTH, data=form, files={"file": ("x.apk", b"not a zip")})
    assert bad.status_code == 415
    r = c.put("/api/v1/app/android", headers=AUTH, data=form, files={"file": ("VitaLocal.apk", APK)})
    assert r.status_code == 200, r.text
    meta = r.json()
    assert (meta["version"], meta["version_code"], meta["size"]) == ("0.3.2", 8, len(APK))
    assert meta["sha256"] == hashlib.sha256(APK).hexdigest()
    assert c.get("/api/v1/app/android", headers=AUTH).json() == meta

    dl = c.get("/api/v1/app/android/apk", headers=AUTH)
    assert dl.content == APK and dl.headers["content-type"] == "application/vnd.android.package-archive"
    assert "VitaLocal-0.3.2.apk" in dl.headers["content-disposition"]
    assert c.get("/api/v1/app/android/apk").status_code == 401  # the token is required
