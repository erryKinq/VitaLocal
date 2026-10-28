# VitaLocal Companion (Android)

The phone's entry point for drinks, food, habits, training and the journal, with a short overview
of the day; analysis and corrections stay in the WebUI. It also brings watch data to the server
(Gadgetbridge or Health Connect). The app follows the phone language (German or English).

## What it does

### Tabs

- **Übersicht** (Overview): what is still open today (habits, supplements), a check-in (mood,
  energy, symptoms, notes) and the day's values (sleep, HRV, resting HR, steps, energy, intake, …)
  against the 28-day median. ‹ › switches to the previous six days.
- **Ernährung** (Nutrition): the food diary of one of the last 7 days against the nutrient goals.
  "+" per meal searches the local copy of the catalog (favorites, recently used, dishes, meal
  templates), Open Food Facts through the server on demand, or scans a barcode on the device
  (CameraX + zxing-cpp, no Google services); quick entries and new foods too.
- **Gewohnheiten** (Habits), **Training** (next workout, live logging of sets) and **Journal**
  (the day's text, saved like in the WebUI). Areas switched off on the server are hidden.
- **Offline first**: every entry goes to an outbox and shows immediately; it is sent when the
  network is back (idempotent through client UUIDs). Entries the server rejects (4xx) are logged
  and dropped; network/5xx/401 keep them queued.
- **Quick settings tile** "+250 ml" and home screen widgets (drinks and open habits, habit garden,
  streaks).

### Live sync

- **Every 12 minutes by day, every 4 hours at night (22–06, adjustable):** an exact alarm starts a
  short foreground service (type dataSync, silent notification). It asks Gadgetbridge to sync the
  watch (`ACTIVITY_SYNC`, waits for `ACTIVITY_SYNC_FINISH`), then to export its database
  (`TRIGGER_DATABASE_EXPORT`, waits for the broadcast or the file to change), and uploads only the
  database pages that changed since the last confirmed upload (`POST /api/v1/ingest/gadgetbridge/db`,
  VLDD delta, a few KB; the whole file when the server lacks the base). Data reaches VitaLocal
  within about 14 minutes.
- No "sync finished" (nothing new) while it usually comes: the export is skipped. Gadgetbridge
  silent three times: the interval doubles (at most hourly) and one notification says so. Android's
  dataSync budget (6 h per day) is tracked; near it the interval doubles, at the limit live sync
  pauses until the app is opened.
- Settings → Live-Sync: switch, intervals, night window, a checklist and "Testlauf" (step by step
  which part answered). The sync status in the settings shows the last cycle, the next run and the budget.
- **Gadgetbridge setup:** Settings → Developer options → Intent API: "Allow activity sync trigger",
  "Broadcast on activity sync finish", "Allow database export", "Broadcast on database export".
  Automations → Auto export (database): create the export file (Gadgetbridge.db) in the VitaLocal
  folder, interval 0. Set the ZIP auto export to 6 h or more (its FIT files still come that way).
  Battery of Gadgetbridge: unrestricted.

### FIT files

- **Workouts:** Gadgetbridge's FIT export (Automations, one original watch file per workout) into
  the Gadgetbridge folder or a subfolder: every live cycle sends new files (`fit_export/<name>`).
- **Everything else** (monitoring/floors, sleep start and sub-scores, skin temperature): when the
  server reports `files_wanted` (or every 6 h by day) the app asks Gadgetbridge for a ZIP export
  (`TRIGGER_ZIP_EXPORT`) and picks it up in a later cycle. Of a ZIP only the files the server
  lacks are sent (`/ingest/artifacts/missing` + `/ingest/gadgetbridge/files`), never the database.
- Setup: Intent API "Allow zip export" + "Broadcast on zip export", ZIP auto export interval 0.

### Export sync

- **Gadgetbridge**: watches the folder that holds Gadgetbridge's automatic ZIP export
  (`Gadgetbridge.zip`, written in place by Gadgetbridge). A new or changed export is hashed,
  checked against `GET /api/v1/ingest/artifacts/{sha256}`, uploaded if unknown, and the import is
  followed until it finishes. With live sync on, the app also triggers syncs and database exports (above).
- **Cleanup**: Gadgetbridge exports from before today are deleted once the server imported them
  or a newer export. The newest file and `Gadgetbridge.zip` are never deleted (Gadgetbridge
  writes to that exact file).
- **Share target**: "Share" from any app (Files, …; until the takeover also Waistline's "share
  database") sends ZIP, JSON
  or CSV files to the server; they wait in the app until the server is reachable. A shared PDF
  or photo of a lab report goes to the server's import (the local model reads it); the values
  are reviewed and confirmed in the WebUI under Gesundheit.
- **Status**: per source the last file, upload, import number and result, plus a log. Periodic
  sync every 1/3/6/12 h (WorkManager) and "sync now". Error notifications are optional.

The API token is encrypted with an Android Keystore key. Plain `http://` is only accepted for
local/VPN addresses (private ranges, `.arpa`, `.lan`, …); a private CA installed on the
phone is trusted for a reverse proxy with an own certificate.

**Data source** (Settings → Data source): a watch via Gadgetbridge (below), Health Connect
(grant read access in the app; with "read in the background" it also syncs while closed,
otherwise whenever the app is opened; the first transfer fetches 30 days), or none (logging
only; the server estimates the burn from the profile). The app follows the phone language
(German or English).

## Build

```bash
./build.sh
```

Needs JDK 17 (`JAVA_HOME`) and the Android SDK (platform 36, `ANDROID_HOME`); see the top of
`build.sh`. The first run creates a signing key under `~/Library/Application Support/VitaLocal/`
(macOS) or `~/.config/vitalocal/` (elsewhere), or in `VITALOCAL_SIGNING_DIR` – back it up: updates
only install over an app signed with the same key. Output: `android/dist/VitaLocal.apk`.
Unit tests: `./gradlew testDebugUnitTest`.

To give the app to others, publish one signed build (e.g. as a release asset named
`VitaLocal-<version>-<versionCode>.apk`); they upload it on their server under Settings →
Android app, and their apps update from there.

With `VITALOCAL_PUBLISH_URL` (and `VITALOCAL_API_TOKEN`) in the repo's `.env`, `build.sh` then
uploads the APK to the server (`publish.sh`, also callable alone). The WebUI offers it under
Data → Android app (also linked from ⚙︎): download on the phone, open, install over the old version. The
app itself checks the server at start (and under Einstellungen → App-Update) and
offers "Installieren": it downloads the build, checks its SHA-256 and hands it to Android's
installer. The first time Android asks to allow installs from VitaLocal; confirm, then tap
"Erneut". Updates need a higher `versionCode` and the same signing key.

## Setup on the phone

1. Install the APK (first time: via USB/adb or any file transfer; later from the WebUI), open the app, enter the server address and API token, "Speichern & testen".
2. Create a folder such as `Documents/Gadgetbridge` and choose it in the app (Android does not
   allow `Download` itself). In Gadgetbridge: Settings → Automations → Auto export zip, create
   the export file in that folder and pick an interval.
3. Food is logged in the Ernährung tab. Coming from Waistline: share a fresh Waistline export to
   VitaLocal, then "Waistline ablösen" on the WebUI's Data page.
