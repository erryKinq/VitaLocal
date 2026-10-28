"""Updates of the published APK from GitHub releases (Settings → Android app).

On by default (opt-out); switched off, nothing here touches the network. While on, a background
thread checks the repository's latest release once a day (or on "check now"): its asset
`VitaLocal-<version>-<versionCode>.apk` is downloaded when its version code is higher than the
published APK's, verified against the `<asset>.sha256` next to it (`sha256sum` output) and then
published exactly like an upload (`apk.py`), so installed apps update on their next check.

GitHub's REST API is used without a token (60 requests per hour per IP; a check needs one, plus two
downloads when there is something new). Prereleases and drafts are not "latest" and never fetched.
The switch, repository and last result live in `_app/github.json` next to the APK.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import threading
from datetime import UTC, datetime, timedelta

import httpx

from vitalocal import __version__
from vitalocal.apk import ApkRejected, ApkWriter, app_dir, read_meta

log = logging.getLogger(__name__)

DEFAULT_REPO = "erryKinq/VitaLocal"
REPO_PATTERN = r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$"
API_BASE = "https://api.github.com"
APK_ASSET = re.compile(r"^VitaLocal-([0-9A-Za-z.+]{1,40})-([0-9]{1,9})\.apk$")
CHECK_EVERY = timedelta(days=1)
POLL_S = 600
TIMEOUT = httpx.Timeout(30.0, connect=10.0)

# Tests replace this with an httpx.MockTransport (no network in tests).
_TRANSPORT: httpx.BaseTransport | None = None

# one check (and one APK write) at a time; the upload endpoint takes it too
LOCK = threading.Lock()
_CFG_LOCK = threading.Lock()


class UpdateError(Exception):
    """GitHub unreachable, an unexpected answer, or an APK that does not match its checksum."""


def _path():
    return app_dir() / "github.json"


def load() -> dict:
    cfg = {"enabled": True, "repo": DEFAULT_REPO, "last_check_at": None, "last_result": None}
    with contextlib.suppress(OSError, ValueError):
        cfg.update(json.loads(_path().read_text()))
    return cfg


def _save(cfg: dict) -> None:
    app_dir().mkdir(parents=True, exist_ok=True)
    tmp = _path().with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg))
    tmp.replace(_path())


def configure(enabled: bool, repo: str) -> dict:
    with _CFG_LOCK:
        cfg = load()
        if repo != cfg["repo"]:  # the last result was about another repository
            cfg["last_check_at"] = cfg["last_result"] = None
        cfg.update(enabled=enabled, repo=repo)
        _save(cfg)
        return cfg


def due(now: datetime | None = None) -> bool:
    cfg = load()
    if not cfg["enabled"]:
        return False
    if not cfg["last_check_at"]:
        return True
    return (now or datetime.now(UTC)) - datetime.fromisoformat(cfg["last_check_at"]) >= CHECK_EVERY


def check() -> dict:
    """Checks the configured repository now; returns the settings with the new last result."""
    with LOCK:
        repo = load()["repo"]
        try:
            result = _check(repo)
        except UpdateError as e:
            result = {"status": "error", "message": str(e)}
        except Exception as e:  # noqa: BLE001 - recorded and shown instead of failing the thread
            log.exception("app update check failed")
            result = {"status": "error", "message": f"{type(e).__name__}: {e}"}
        log.info("app update check (%s): %s", repo, result)
        with _CFG_LOCK:
            cfg = load()
            cfg.update(last_check_at=datetime.now(UTC).isoformat(), last_result={"repo": repo, **result})
            _save(cfg)
            return cfg


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=API_BASE,
        timeout=TIMEOUT,
        follow_redirects=True,  # asset downloads redirect to GitHub's object storage
        headers={"User-Agent": f"VitaLocal/{__version__}", "X-GitHub-Api-Version": "2022-11-28"},
        transport=_TRANSPORT,
    )


def _check(repo: str) -> dict:
    try:
        with _client() as c:
            return _check_with(c, repo)
    except httpx.HTTPError as e:
        raise UpdateError(f"GitHub not reachable: {type(e).__name__}") from e


def _check_with(c: httpx.Client, repo: str) -> dict:
    r = c.get(f"/repos/{repo}/releases/latest", headers={"Accept": "application/vnd.github+json"})
    if r.status_code == 404:
        raise UpdateError(f"no published release in {repo} (or no such repository)")
    if r.status_code in (403, 429) and r.headers.get("x-ratelimit-remaining") == "0":
        raise UpdateError("GitHub's rate limit for this address is used up, try again in an hour")
    if r.status_code != 200:
        raise UpdateError(f"GitHub answered HTTP {r.status_code}")
    try:
        release = r.json()
        assets = {a["name"]: a for a in release.get("assets") or []}
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise UpdateError("unexpected answer from GitHub") from e
    info = {"release": release.get("tag_name"), "url": release.get("html_url")}
    apks = [(int(m[2]), m[1], name) for name in assets if (m := APK_ASSET.match(name))]
    if not apks:
        return {"status": "no_apk", **info}
    code, version, name = max(apks)
    info.update(version=version, version_code=code)
    current = read_meta()
    if current and current.get("version_code", 0) >= code:
        return {"status": "up_to_date", **info}

    checksum = assets.get(f"{name}.sha256")
    if checksum is None:
        raise UpdateError(f"{name} has no checksum ({name}.sha256) in the release")
    r = c.get(checksum["browser_download_url"])
    expected = r.text.split()[0].lower() if r.status_code == 200 and r.text.strip() else ""
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise UpdateError(f"{name}.sha256 could not be read (HTTP {r.status_code})")

    w = ApkWriter()
    try:
        with c.stream("GET", assets[name]["browser_download_url"]) as r:
            if r.status_code != 200:
                raise UpdateError(f"{name} could not be downloaded (HTTP {r.status_code})")
            for chunk in r.iter_bytes(1 << 20):
                w.write(chunk)
        if w.sha.hexdigest() != expected:
            raise UpdateError(f"{name} does not match its checksum; not published")
        w.publish(version, code, source="github")
    except ApkRejected as e:
        raise UpdateError(f"{name}: {e}") from e
    except BaseException:
        w.discard()
        raise
    return {"status": "updated", **info}


class Checker:
    """Checks once a day while the switch is on (the first time a few minutes after the start);
    idle (no network) while it is off."""

    def __init__(self):
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="app-updates", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(5)

    def _run(self) -> None:
        while not self._stop.wait(POLL_S):
            try:
                if due():
                    check()
            except Exception:  # noqa: BLE001 - keep checking tomorrow
                log.exception("app update check failed")


_checker: Checker | None = None


def start() -> None:
    global _checker
    _checker = Checker()
    _checker.start()


def stop() -> None:
    global _checker
    if _checker is not None:
        _checker.stop()
        _checker = None
