# VitaLocal API

Reference for clients: the WebUI, the Android companion, AI agents (e.g. for journaling) and scripts.
The live OpenAPI schema is served at `/docs` (Swagger UI) and `/openapi.json`; this document adds
the semantics the schema does not carry (idempotency, day boundaries, units, what is safe to retry).

## Basics

| | |
|---|---|
| Base URL | `http://<host>:8000` (binds to `127.0.0.1` unless `VITALOCAL_BIND` is set) |
| Prefix | all data endpoints live under `/api/v1` |
| Auth | `Authorization: Bearer $VITALOCAL_API_TOKEN` on every `/api/v1` call; `/health` is open |
| Format | JSON in and out, except uploads (`multipart/form-data`) |
| Time stamps | ISO 8601. Inputs **must** carry a time zone (`2026-09-28T13:51:00+02:00`); outputs are UTC |
| Days | `YYYY-MM-DD`, local calendar day in `VITALOCAL_TIMEZONE` (default `Europe/Berlin`) |
| Errors | `{"detail": "..."}` (or a list of validation errors), with 401, 404, 409, 415, 422 or 502 |

```bash
export VL=http://localhost:8000 TOKEN=...   # value of VITALOCAL_API_TOKEN
curl -H "Authorization: Bearer $TOKEN" $VL/api/v1/meta
```

### Retry safety

Clients that work offline (Android, agents) can queue writes and replay them:

| Write | Safe to replay? |
|---|---|
| `POST /ingest/upload` | yes, identical bytes return the existing batch |
| `POST /ingest/gadgetbridge/db` | yes, the same database (`sha256`) returns the existing batch |
| `POST /ingest/gadgetbridge/files` | yes, the same manifest returns the existing batch |
| `POST /checkins`, `POST /drinks` | yes, when the client generates `id` once and keeps it |
| `PUT /drinks/{id}` | yes, the body is the full corrected entry |
| `PUT /goals/{metric}`, `PUT /settings/…` | yes, last write wins |
| `PUT /journal/…` | yes with `version`/`If-Match` (a replay of the same text is a no-op; a stale one gets `409`); without, last write wins |
| `PUT /habits/{id}/log/{day}`, `PUT /supplements/{id}/log/{day}` | yes, last write wins |
| `POST /habits` | yes, when the client sends `id` |
| `POST /labs/reports` | yes, when the client sends `id` |
| `POST /training/exercises`, `POST /training/plans` | yes, when the client sends `id` |
| `POST /training/sessions` (manual workout) | yes, `manual_key` is required |
| `PUT /training/sessions/{id}`, `PUT /training/plans/{id}` | yes, the body is the full state |
| `PUT /labs/reports/{id}`, `PUT /labs/targets/{key}` | yes, the body is the full state |
| `POST /labs/imports` | yes, the same file while its import is open returns that import |
| `POST /labs/imports/{id}/confirm` | yes, a repeat returns the report created the first time |
| `DELETE …` | yes, deleting a missing row is a 204 |

## System

### `GET /health`
No auth. `200 {"status":"ok","version":…,"database":"ok","raw_store":"ok"}`, or `503` with
`"status":"degraded"`.

### Android app
- `GET /api/v1/app/android`: the published APK's `version`, `version_code`, `size`, `sha256`,
  `uploaded_at`, or `null`.
- `GET /api/v1/app/android/apk`: the APK itself (needs the token; the WebUI's Data page fetches
  it and saves it as `VitaLocal-<version>.apk`).
- `PUT /api/v1/app/android` (multipart: `file`, `version`, `version_code`): publish a build,
  replacing the previous one. Used by `android/publish.sh`. `409` while an update is being
  downloaded from GitHub.
- `GET /api/v1/app/android/github`: updates from GitHub releases: `enabled` (default `true`),
  `repo` (default `erryKinq/VitaLocal`), `last_check_at` and `last_result` (`null` before the first
  check). `last_result.status` is `updated` (a newer APK was published), `up_to_date`, `no_apk`
  (the latest release has none) or `error` (`message` says why: GitHub unreachable, rate limit,
  missing or mismatching `.sha256`, not an APK); with `repo`, `release` (tag), `url`, `version`
  and `version_code` of the release's APK where known. Published APKs carry `"source": "github"`
  in `GET /api/v1/app/android` (uploads: `"upload"`).
- `PUT /api/v1/app/android/github` `{"enabled": bool, "repo": "owner/name"}`: switch it on or off;
  another repository clears the last result. While off the server makes no requests to GitHub.
  While on it checks the latest (non-prerelease) release once a day: the asset
  `VitaLocal-<version>-<versionCode>.apk` is published when its version code is higher than the
  current APK's and its SHA-256 matches `<asset>.sha256`.
- `POST /api/v1/app/android/github/check`: check now; returns the same as `GET` with the new
  result. `409` while switched off.

### `GET /api/v1/capabilities`
What the clients should offer: `ai` (`configured`, `external`, `vision`, `features` with
`summary`/`coach`/`labs` usable, `journal` shared), `modules` (areas shown, see below),
`profile_complete`, `setup_done`, `has_watch_data` (energy data in the last 30 days), `units`, `lang` (`de`|`en`, the app follows it once `setup_done`).

### `GET|PUT /api/v1/profile`
`sex` (`female`|`male`), `birth_year`, `height_cm`, `pal` (everyday activity without training,
1.2–1.9), `energy_source` (`auto`|`watch`|`estimate`|`manual`), `manual_tdee_kcal` (own daily
burn without training), `units` (`metric`|`imperial`), `goal` (`lose`|`keep`|`gain`),
`setup_done`. `GET` adds `complete` and `estimate` (`weight_kg` from the latest body measurement,
`bmr`, `everyday_kcal`, `avg_burn_14d`, `suggested_kcal` for the goal).

### `GET|PUT /api/v1/modules`
`{nutrition, drinks, training, habits, journal, health, supplements, watch}` → shown or not.
Only the UI hides areas; the endpoints stay. `watch` defaults to whether watch data exists.

### `GET /api/v1/energy?from=…&to=…`
Burned energy per day: `{day: {rest, active, total, source}}`. `source` is `watch` (resting
metabolic rate + active energy), `estimate` (Mifflin-St Jeor × PAL plus workouts as
(MET − 1) × kg × h, intensity from RPE and exercise ratings) or `manual` (`manual_tdee_kcal` plus
workouts). With `energy_source: auto` a day uses the watch when it has both values. Days without
any basis are missing. The same object is `energy` in `/today` and `/days/{day}`.

### `GET /api/v1/meta`
Version, time zone, `first_day`/`last_day` with heart-rate data, the metric catalog (only metrics
that have data, plus the check-in metrics) and the sleep stage codes.

## Ingest

### `POST /api/v1/ingest/upload`
Multipart field `file`. The source is detected from the content:

| Content | `source` |
|---|---|
| ZIP (`PK…`), Gadgetbridge "Export zip" | `gadgetbridge_export` |
| Waistline diary CSV (`;`-separated daily totals) | `waistline_diary_csv` |
| JSON (`{…}` or `*.json`), Waistline database export/backup | `waistline_export` |

Anything else is a `415`. Response: `202 {"batch_id", "created": true, "source"}` for new bytes
(processing runs in the background), `200` with `"created": false` for bytes already stored.
After the Waistline takeover (see Nutrition) Waistline files are refused with
`409 {"detail": {"code": "waistline_retired", "message"}}`.

Imports run in a queue, one at a time and oldest first, in a background worker of the server
(restarted after a crash; a batch interrupted by a restart goes back to the queue). A
Gadgetbridge database is parsed incrementally: each table from the newest row of the snapshot
applied before minus an overlap (`VITALOCAL_GB_WINDOW_HOURS`, default 72), because Gadgetbridge
rewrites rows in place and late rewrites (the night written at the morning sync, revised day
totals) are only visible through their timestamps. A snapshot whose export time (`as_of`) lies
before the applied one and whose data reaches no further is not parsed at all (its FIT files
still are), so an old export uploaded late never overwrites newer data. Once a night (03:30 local,
`VITALOCAL_GB_FULL_PARSE_AT`) the applied snapshot is parsed in full; its `full_reparse` result
on the batch reports changes older than the overlap (`drift`). FIT files already parsed by the
current parser version (same bytes, from any export) are not parsed again.

Optional multipart field `client_meta`: a JSON object (max 4096 characters) about the uploader,
stored as `client_meta.client` on the batch, e.g.
`{"app": "vitalocal-android", "app_version": "0.1.0", "device": "Pixel 8", "trigger": "auto"}`.

```bash
curl -H "Authorization: Bearer $TOKEN" -F file=@Gadgetbridge.zip \
     -F 'client_meta={"device":"laptop","trigger":"manual"}' $VL/api/v1/ingest/upload
```

### Exports of other apps (same `POST /api/v1/ingest/upload`)
Recognized from the first 4 KiB:
- **Workouts** (`workout_csv`): Strong (`Date;Workout Name;…;Exercise Name;Set Order;Weight;Reps;…`;
  weights in the profile's unit) and Hevy (`title,start_time,…,exercise_title,…,weight_kg|weight_lbs`).
  Each workout becomes a reviewed manual workout (key: app + start time, so a later export adds only
  new ones). Exercise names map to an own exercise of that name, a library entry (by its English key
  words; the equipment must agree) or a newly created own exercise. Set RPEs become the workout's RPE.
- **Daily values** (`daily_csv`): any CSV with a date column and daily values (MyFitnessPal nutrition
  summary per meal, summed per day; Cronometer daily summary; `date,weight[,body_fat]` lists).
  Columns are matched by English/German words, sodium becomes salt (× 2.5), kJ and lb are converted.
  Values are samples of a per-app device; a newer export of the same app replaces its span.

`client_meta.app` of the batch says which app was recognized.

### `POST /api/v1/ingest/health-connect`
JSON body from the Android app: the Health Connect records of a time window (`range`), see
`server/src/vitalocal/ingest/health_connect.py` for the shape. Steps, active/total calories, basal
metabolic rate, weight, body fat, heart rate, resting HR, SpO₂, HRV, sleep stages and exercise
sessions map onto the same metrics the watch path writes (energy, sleep and trends work alike).
Values of the window that are no longer sent are retracted. ≤ 32 MB per batch.

### `GET /api/v1/export`
A ZIP of everything: `tables/<table>.csv` for every table (import bookkeeping and raw files left
out, the AI API key removed), `food_diary.csv`, `body.csv`, `strength_sets.csv`,
`daily_values.csv` (one row per day, one column per metric) and `manifest.json` (version, time
zone, row counts).

### `GET /api/v1/ingest/artifacts/{sha256}`
Check before uploading: `404` if the server has never seen this file, otherwise
`{"sha256", "size", "media_type", "first_seen_at", "pruned_at", "stored", "batches": [...]}`.
`batches` are the imports of exactly this file (newest first, same fields as the batch list).
A client can skip the upload when a batch exists, and can delete its local copy once that batch is
`completed` or `completed_with_errors`.

`POST /api/v1/ingest/gadgetbridge` does the same but skips detection (Gadgetbridge ZIP only).

### `POST /api/v1/ingest/gadgetbridge/db`
Gadgetbridge's database alone, as its database export writes it, for near-live data (the Android
app sends one every few minutes). Multipart:

- `meta` (JSON, max 4096 characters): `sha256` and `size` of the database, `encoding` (`full`
  or `vldd1`), `base_sha256` (for `vldd1`: the database the delta builds on), `exported_at`
  (ISO time with time zone; the snapshot time, at most 5 minutes ahead of the server's clock),
  optional `gadgetbridge` (`package`, `version`) and `client` objects
- `data`: gzip of the database (`full`) or of a VLDD delta: only the blocks that changed since
  `base_sha256` (block size = SQLite page size), so an upload is a few KB instead of the file

VLDD v1 (all integers big-endian): `"VLDD"`, version `1`, 3 reserved zero bytes, `block_size`
(u32, power of two 512–65536), `base_size` (u64, must equal the base), `new_size` (u64),
`count` (u32), `count` block indices (u32, strictly increasing, index × block_size < new_size),
then the blocks in index order (block i has min(block_size, new_size − i × block_size) bytes).
The new file is the base cut or extended to `new_size` with the blocks written over it; every
block reaching past the end of the base must be included. Example: base `A B C`, new `A B' C D`
(512-byte blocks) → indices `[1, 3]`, then `B'` and `D`.

The server checks the result's SHA-256 and SQLite header, then queues it (source
`gadgetbridge_db`, parsed incrementally; queued snapshots coalesce into the newest). Responses:
`202 {"batch_id", "created": true, "source", "sha256", "stored": "full"|"delta",
"bytes_received"}`, `200` with `"created": false` for a known database, `409
{"detail": {"code": "base_unavailable"}}` (send the whole file), `422` with `code`
`sha_mismatch`, `invalid_delta`, `not_sqlite` or `truncated` (send the whole file, forget the
base), `413` beyond `VITALOCAL_LIVE_DB_MAX_BYTES` (default 256 MiB). `GET /ingest/status`
lists `gb_db_v1` and `gb_db_delta_v1` under `capabilities`.

### `POST /api/v1/ingest/artifacts/missing`, `POST /api/v1/ingest/gadgetbridge/files`
File sets without re-sending known bytes: the files of a Gadgetbridge ZIP export (the database
comes live, see above) or of Gadgetbridge's FIT export (one original watch file per workout,
paths `fit_export/<name>`). First `POST /artifacts/missing {"sha256": [...]}` →
`{"missing": [...]}` (unknown or pruned), then the multipart upload: `manifest` (JSON
`{"v": 1, "origin": "zip_export"|"fit_export", "exported_at", "entries": [{"path", "sha256",
"size"}]}`), optional `client_meta`, and one `files` part per missing file, named by its SHA-256.
`202 {"batch_id", "created": true, "source": "gadgetbridge_files", "entries", "received_files"}`,
`200` for the same manifest again, `409 {"detail": {"code": "missing_blobs", "missing": [...]}}`,
`422` for an invalid manifest or a file whose content does not match. Only files (path + content)
no earlier Gadgetbridge batch had become members of the batch, and their FIT files are parsed. A
FIT-export workout file counts as `ACTIVITY` of the watch that delivered the last workouts.

`GET /ingest/status` → `files_wanted`: `{"wanted", "reasons": [{"kind": "activity_fit", "name",
"start"}, {"kind": "sleep_fit", "night_end"}, {"kind": "monitor", "since"}]}`: a recent workout
without its FIT file, a night without the SLEEP file (sleep start), or by day no monitoring file
for over 6 h. The app then triggers a ZIP export. Capability `files_v1`.

### `GET /api/v1/ingest/batches[?limit=100&before_id=…&source=…]`, `GET /api/v1/ingest/batches/{id}`
Import history, newest first, `limit` (1–1000) per page; the next page starts `before_id` = the
last id. `status` (`pending`, `processing`, `completed`, `completed_with_errors`, `failed`,
`superseded` = a queued live snapshot that a newer one replaced before it ran; not an error),
`received_at`, `finished_at`, `as_of` (when the snapshot was taken: the export time, else the
upload time), `client_meta` (e.g. Gadgetbridge version and export time, plus the uploader's
`client`), `error`, `raw_pruned_at` (set once retention removed the original export). The
single-batch view adds `stats` (incl. `processing_s`; `gb_sqlite_parse_run.mode` is `full` or
`incremental`, or `status: "skipped"` with `reason: "older_than_applied"`) and every `parse_runs`
entry (parser name/version, statistics). Poll this after an upload to learn when the data is in.

### `GET /api/v1/ingest/status`
Cheap enough to poll every minute: `data_version` (changes whenever an import finished or the
nightly full parse ran; reload a view only then), `last_heart_rate_at`, `queue` (`pending`,
`processing`), `worker` (`ok`, `stopped`, `off`), `sources` (latest batch per source with
`status`, `received_at`, `finished_at`, `exported_at`, `as_of`, `processing_s`) and `gb_db`
(the applied Gadgetbridge database snapshot: `applied_batch_id`, `applied_sha256`, `as_of`,
`last_full_at`, or `null`). The WebUI's day view uses it to follow new data.

### `POST /api/v1/ingest/batches/{id}/reprocess`
Queues a stored batch again: inventory and parsers run anew, every file is parsed again (also
files parsed before by the same parser version), the database in full. A snapshot older than the
applied one still does not overwrite newer data; to parse the newest snapshot in full use
`python -m vitalocal.cli reparse --gb-full`. `409` while it is running, `410` if its raw export
was pruned.

### Retention
Exports are full snapshots. Once a newer successful export of the same source exists, an older
export's original file and its superseded members (same archive path, newer content in a kept
export) are deleted after `VITALOCAL_RAW_RETENTION_DAYS` (default 7, `0` keeps everything). Kept
always: exports inside the window, the latest successful export per source, files still used by a
kept export or an activity, and files that only exist in old exports. Imported data is never
touched. The applied Gadgetbridge database snapshot is always kept. Live databases
(`gadgetbridge_db`, one every few minutes) do not stay for the whole window: kept are the newest
two (the base of the phone's next delta) and, within the window, the last successful one of each
local day. Pruning runs at most every 10
minutes in the import worker (and after `cli drain`).

- `GET /api/v1/ingest/retention`: stored and pruned bytes, plus a dry run of the next prune
- `POST /api/v1/ingest/prune[?dry_run=true]`: prune now; returns `artifacts`, `bytes`, `batches`
  (or `skipped` while an import is running)

## Day view and trends

### `GET /api/v1/today[?day=YYYY-MM-DD]`
Compact overview for mobile clients (a few KB, no minute series). `day` defaults to today.

- `metrics`: fixed set of daily values (sleep score, HRV, resting HR, steps, energy, stress, body
  energy, SpO₂, respiration, skin temperature, intake, drinks, mood, energy, weight). Each has
  `value` (`null` without data), `label`, `unit`, `decimals`, `better`, `baseline` (median of the
  previous 28 days) and `baseline_days`
- `sleep`: `asleep_minutes`, `bed_start`, `bed_end`, `minutes` per stage
- `drinks`: `total_ml`, `by_drink` (`water`, `tea`, …), `entries` (`id`, `ts`, `drink`, `ml`,
  `caffeine`, `source`), so a client can list, correct and delete them; `goal_ml` (the day's drink
  goal, `null` when switched off) and `goal` (its parts, see Goals)
- `goals`: the day's goals with status (as `GET /goals`)
- `training`: next workout of the active plan (`plan`, `plan_day`, `unreviewed_sessions`,
  `exercises`: `exercise`, `target`, `next` = the progression's suggestion when it differs), or `null`
- `supplements`: `items` (`id`, `name`, `brand`, `form`, `per_day`, `timing`, `count`, `done`,
  `streak`, `done_last_30`) and the day's `ingredients` (as in `/supplements/status`) `by_label` and the entries' `label`/`value`
  are deprecated aliases for clients up to app 0.2
- `checkins`: the day's other check-ins; `nutrition`: `entries` and `totals` (or `null`)
- `activities`, `habits` (the habit object plus `today` — the day's entry as in
  `/habits/status`, or `null` —, `streak`, `this_week`, `icon`)
- `habit_rewards`: the gamification summary for today — `flames` (`total`, `today`, `week`,
  `best_day`), `level` (`level`, `title`, `progress`, `flames`, `next`), `best_current` and
  `best_ever` (`name`, `icon`, `streak`, `unit`, or `null`), `perfect` (`current`, `best`, `total`,
  `today`), `garden`, `challenges`, `unlocked`, `achievements` (count) and `new` (`key`, `icon`,
  `title`); habits also carry `jokers` and `hard`; supplements also carry `per_week`, `week_count`,
  `week_done` and `streak_unit`
- `sync`: `last_heart_rate_at` and per source the latest batch (`status`, `received_at`,
  `exported_at`)

### `GET /api/v1/days/{day}`
Everything for one local day:

- `summary`: `{metric: {value, n, label, unit, decimals}}` for every metric with data
- `series`: intraday points `[epoch_ms, value]` per metric (heart rate, stress, SpO₂, …)
- `sleep`: the night ending that morning (segments, minutes per stage, bed times, HRV points, `skin_temp` points of `skin_temp_relative`)
- `activities`, `nutrition` (food diary: meals with entries and totals, `null` without entries;
  each item has `id`, `food_id`, `source`, `editable`), `drinks` (as
  `GET /drinks?day=`), `goals` (as `GET /goals`), `supplements` (as in `/today`), `journal` (text and check-ins), `coverage` (minutes with heart rate),
  `sync` (as in `/today`)

This is the heavy call behind the WebUI day page. For a compact dashboard use `summary` and ignore
`series`.

### `GET /api/v1/insights[?day=YYYY-MM-DD]`
Values derived from the stored data, relative to `day` (default today). Nothing here is a watch
value; each section says what it is based on.

- `readiness`: VitaLocal's own training-readiness estimate from Garmin's six factors, fitted
  against the watch's score: `score` 0–100, `level` in Garmin's bands (`poor` 0–24, `low` 25–49,
  `moderate` 50–74, `high` 75–94, `prime` 95–100) and `components`, each with `key`, `value`,
  `baseline`, `unit` and `impact` (points it costs, ≤ 0). The score is 100 minus: `sleep_score`
  0.95 per point below 94, `sleep_history` (last 3 nights) 0.15 per point below 94, `hrv_status`
  0.5 per % the 7-day HRV average lies below the lower edge of the watch's balanced range (else
  the 28-day median), `stress_history` (average of the 3 days before) 2.5 per point above 34 and
  `acute_load` 40 per unit of acute : chronic above 1.3. `recovery_time` (hours left of the
  watch's latest recovery time) caps it: no cap up to 10 h, then down to 75 at 15 h and 0.5 per
  hour after that. Needs last night's sleep score and two more components, else `score: null`.
  `garmin`: the watch's own readiness of that day up to the reference time (`score`, `level`
  1 poor … 5 prime, `ts`) or null. A past day is judged as of 09:00 that day.
- `sleep_rhythm`: `last_7` and `last_28` nights with average `bedtime` and `wake_time` (in bed,
  `HH:MM`), their average deviation in minutes (`*_spread_min`), `asleep_minutes`, `nights`;
  `weeks` (8 Monday–Sunday weeks, `partial` for the running one) with the same plus `sleep_score`;
  and `habits`: every bedtime / wake time habit with `min_value`, `max_value` and how often the
  window was hit in the last 7 days (`done` of `measured`).
- `recovery` (last 60 days): the next night's `hrv`, `resting_heart_rate` and `sleep_score` after
  workouts (≥ 20 min) vs. after days without any activity; per recent workout the next night's
  deltas to the 28-day median and `recovered_after_days` (first day with HRV ≥ 97 % and resting HR
  ≤ +1 bpm of the median, `null` if not within 4 days); `typical_recovery_days` (median, needs 3).
- `energy`: `last_7` and `last_28` complete days: `burned` (resting metabolic rate + active energy),
  `active`, `resting`, `intake` (over `intake_days` with a food log), `balance` (days with both),
  `steps`, `drinks_ml`.
- `weeks`: 8 weeks of averages (sleep score, asleep, HRV, resting HR, steps, burned, intake, stress,
  mood) plus `workouts` and `workout_minutes`.

### `GET /api/v1/readiness[?day=YYYY-MM-DD]`
Only the `readiness` section of `/insights` (cheaper; used by the app's training tab).

### `GET|PUT|DELETE /api/v1/settings/day-layout`
The WebUI's day-page layout, shared by all browsers: `{"today": [{"id", "size"}], "past": […]}`
(`size`: `half`|`full`, ids `[a-z0-9_]`). `GET` returns `null` while the default applies; `DELETE`
resets.

### `GET /api/v1/trends?metrics=steps,weight&from=2026-09-01&to=2026-09-28`
Daily values per metric (`to` inclusive, at most 800 days), each day aggregated by the metric's
rule (`sum`, `avg`, `min`, `max`, `last`). Days without data are absent, not zero.

### Metric catalog
Each metric: `key`, `label`, `unit`, `daily` (aggregation), `group`, `decimals`, `better`
(`higher`/`lower`/`null`), `note`. Groups: `activity`, `heart`, `body`, `sleep`, `nutrition`,
`journal`. Check-in metrics are prefixed `checkin.` (`checkin.mood`, `checkin.energy`,
`checkin.rpe`); drinks are `nutrition.drinks` (ml per day; was `checkin.water` before server 0.2).
Full list in `server/src/vitalocal/metrics.py`. From the watch besides the obvious ones:
`floors_climbed` / `floors_descended` (3.048 m of ascent = 1 floor, from the MONITOR files),
`ascent` / `descent` (m), `vo2max`, `vo2max_daily` (24/7 estimate), `race_time_5k`,
`race_time_10k`, `race_time_half_marathon`, `race_time_marathon` (s), `recovery_time` (min left),
`garmin.training_readiness` (+ `_level`), `workout_respiration_rate`, `workout_stress`,
`workout_temperature` (during recorded workouts, at most one value per 30 s) and
`skin_temp_relative` (°C every 3 minutes over 24 h, relative to an undocumented reference: the
course is meaningful, the level is not).

### `GET /api/v1/fitness[?day=YYYY-MM-DD]`
Latest `vo2max`, `vo2max_daily` and race predictions up to the end of `day`:
`{"day", "metrics": {key: {"value", "ts", "previous"}}}`; `previous` is the latest value 4 weeks
earlier (null if none).

## Raw data

### `GET /api/v1/metrics`
Stored metrics with `unit`, `samples` count and `first`/`last` time stamp.

### `GET /api/v1/samples?metric=heart_rate&from=…&to=…[&bucket=15m][&device_id=1]`
Raw samples (`ts`, `device_id`, `value`, `unit`), capped at 50,000 rows (`"truncated": true`
beyond that). With `bucket` (`<n>m`, `<n>h`, `<n>d`) you get `avg`, `min`, `max`, `sum`, `n` per
bucket instead.

### Activities
- `GET /api/v1/activities[?from=&to=&sport=]`: list, newest first, with source flags
  (`from_gadgetbridge_db`, `from_fit`), `active_sets`, `manual`, and from the training layer `title`
  and `reviewed` (see Training).
- `GET /api/v1/activities/{id}`: detail with FIT attributes (besides numbers `hr_zones`:
  `seconds` per zone, below zone 1 first, `bounds` (upper limit per zone, bpm), `max_hr`,
  `resting_hr`, `calc`; and `laps` when there is more than one: `start`, `elapsed_s`,
  `distance_m`, `avg_hr`, `max_hr`, `kcal`, `avg_speed_ms`, `ascent_m`), the Gadgetbridge summary, all sets
  (including rest) and the latest RPE check-in. Each set carries the effective values, `edited` and
  the watch's `original` values.
- `PUT /api/v1/activities/{id}/sets/{set_index}`: correct a set
  (`exercise_category`, `exercise_name`, `repetitions` 0–1000, `weight_kg` 0–1000; null keeps the
  watch value). Corrections survive reprocessing.
- `DELETE /api/v1/activities/{id}/sets/{set_index}/edit`: drop the correction.
- `GET /api/v1/exercises`: FIT exercise categories and names for the correction form.

## Drinks

Drinks are nutrition, not journal entries.

### `POST /api/v1/drinks`

```json
{"id": "8f0c2c4e-3a51-4c43-9d07-4b0a3f6f1e21", "drink": "tea", "ml": 250, "caffeine": false,
 "ts": "2026-09-28T13:51:00+02:00", "source": "android"}
```

`drink`: `water` (default), `tea`, `coffee`, `other`; `ml`: > 0 and ≤ 5000. `caffeine` marks the
entry as caffeinated (it drives the caffeine cut-off habit); left out, `coffee` and `tea` count as
caffeinated, everything else not. Idempotent like check-ins (`201` new, `200` known id). `ts`
defaults to now.

### `PUT /api/v1/drinks/{id}`
Correct an entry afterwards (e.g. the widget's quick water was a coffee): `{"ts", "drink", "ml",
"caffeine"}`, the full entry; `caffeine` left out follows the kind as above. `404` for an unknown
id. Replaying the same body gives the same result.

### `GET /api/v1/drinks?day=YYYY-MM-DD`, `GET /api/v1/drinks?from=…&to=…`, `DELETE /api/v1/drinks/{id}`
With `day`: `{total_ml, by_drink, entries}`; with a time range: the list of entries.

## Goals

Daily goals live under nutrition. **Nutrient goals** are set in the WebUI or here
(`PUT /goals/{metric}`). Until the Waistline takeover, each import of the Waistline JSON
export also takes over `settings.goals` (a "minimum goal" is a lower bound, otherwise an upper bound;
per-weekday and percent goals are skipped with a warning in the parse stats). A changed goal becomes
a new version valid from the day the export arrived; the very first version applies to all earlier
days, so every day is judged by the goal valid then. The **drink goal** is computed per day:
`body weight × ml_per_kg` (latest weight up to that day, else `fallback_ml`) `+ hours of recorded
activity × ml_per_training_hour`, rounded to 100 ml.

### `GET /api/v1/goals[?day=YYYY-MM-DD]`
`{"day", "goals": [...]}`, one entry per goal valid that day: `metric`, `label`, `unit`,
`decimals`, `min_value`, `max_value` (either may be `null`), `valid_from` (`null` = since
always), `source` (`waistline`, `vitalocal`, `formula` for drinks), `value` (the day's total,
`null` without data), `status` (`below`, `within`, `above`, `null`) and `missing` (how many of
the day's diary entries do not declare the nutrient; they count as 0). `incomplete` is always
false (kept for older clients). The drink goal adds `formula`: `goal_ml`,
`base_ml`, `training_ml`, `weight_kg`, `training_min`.

### `GET /api/v1/goals/history[?metric=…]`
Every stored version (`metric`, `min_value`, `max_value`, `valid_from`, `source`, `created_at`);
both bounds `null` = the goal was removed from `valid_from` on.

### `PUT /api/v1/goals/{metric}`, `DELETE /api/v1/goals/{metric}[?valid_from=…]`
Set a goal yourself (e.g. a range Waistline cannot express): `{"min_value", "max_value",
"valid_from"}` (at least one bound; `valid_from` defaults to today). Possible metrics:
`nutrition.energy`, `.protein`, `.carbohydrates`, `.fat`, `.saturated_fat`, `.sugars`, `.fiber`,
`.salt`, `.caffeine`, `.alcohol`. Before the takeover it holds until the goal changes in
Waistline. `DELETE` removes
the goal from `valid_from` (default today) on; earlier days keep theirs.

### `GET|PUT /api/v1/settings/drink-goal`
`{"enabled": true, "ml_per_kg": 35, "ml_per_training_hour": 500, "fallback_ml": 2000}`.

## Nutrition

The food diary lives in VitaLocal (WebUI, Android app, agents); Waistline is replaced once by the
takeover below. Nutrient keys are Waistline's / Open Food Facts' names (`calories` in kcal,
`proteins`, `carbohydrates`, `fat`, `saturated-fat`, `sugars`, `fiber`, `salt` in g, `sodium`,
`caffeine` in mg, …; `GET /api/v1/nutrients` lists all with unit and label). A nutrient a food
does not declare stays empty in the catalog; in sums (daily totals, goals) it counts as 0.

**Daily totals** (`nutrition.energy`, `.protein`, …) are the sums of the day's entries, used by
trends, goals, habits, `/today` and insights; an entry without the nutrient counts as 0.
Imported daily totals (Waistline diary CSV) count only on days
without entries.

### Foods (catalog)

A food: `{"id", "name", "brand", "barcode", "base_amount": 100, "base_unit": "g"|"ml"|"portion",
"portions": [{"label": "1 Scheibe", "amount": 30}], "nutrients": {...per base_amount},
"components": [{"food_id", "amount"}], "favorite", "archived"}`. Responses add `source`
(`vitalocal`, `off`, `waistline`), `updated_at` and `usage` (`count`, `last_used`, `last_amount`,
`last_quantity`, `last_meal_index`). A **dish** has `components` (amount in each component's base
unit); its nutrients are computed from them when it is saved (`base_amount` = how many portions it
makes).

- `GET /api/v1/foods?q=&limit=30` — matches in name, brand or barcode, favorites first, then by
  use. Without `q`: favorites and recently used foods. `updated_since=<ISO time>`: every change
  since (archived too), oldest first, for a client cache.
- `POST /api/v1/foods` — create; `id` optional (client-generated), `201` new, `200` known id
  (unchanged). `PUT /api/v1/foods/{id}` — the full new state; applies to later entries only,
  logged entries keep their snapshot. `DELETE` — deleted when never used, else archived.
- `GET /api/v1/foods/barcode/{code}` — from the catalog (`"found": "catalog"`), else looked up at
  Open Food Facts and stored (`"found": "off"`). `404` unknown, `503` OFF not reachable, `429`
  more than 15 lookups a minute.
- `GET /api/v1/foods/online?q=` — search Open Food Facts (on demand; at most 10 a minute). Hits
  are not stored; `food_id` is set when the product is already in the catalog.
  `POST /api/v1/foods/online/{code}` takes one over (`201`, `200` when already there).

### Diary

- `GET /api/v1/nutrition/{day}` — `meals` (`index`, `name`, `entries`, `totals` of the main
  nutrients), `totals` per nutrient (`value`, `missing`), `goals` (as `GET /goals`,
  without drinks), `waistline_retired`.
- `POST /api/v1/nutrition/entries` — `{"id", "logged_at" (with zone; gives the day), "meal_index",
  "food_id", "amount" (in the food's base unit, default its base amount), "quantity" (default 1),
  "entered_via": "android"|"webui"|"agent"}` or a quick entry `{"id", "logged_at", "meal_index",
  "name", "nutrients"}`. The server computes the entry's nutrients (food × amount / base amount ×
  quantity). `201` new, `200` known id (unchanged).
- `PUT /api/v1/nutrition/entries/{id}` — the full corrected entry (`logged_at`, `meal_index`,
  `amount`, `quantity`; a different `food_id` recomputes, `nutrients` replaces). Without them the
  entry's snapshot is scaled to the new amount. `DELETE` removes it. Waistline entries answer `409`
  until the takeover.
- `POST /api/v1/nutrition/copy` — `{"day", "meal_index" (optional target), "items": [{"from":
  <entry id>, "id": <new id>}]}`: log entries again at the same time of day ("like yesterday");
  idempotent through the new ids.
- `GET|PUT /api/v1/settings/meals` — meal names, e.g. `["Frühstück", "Mittagessen", "Abendessen",
  "Snacks"]`; `meal_index` points into it.

Agent example — log 150 g of a food found by name:

```bash
FOOD=$(curl -s -H "Authorization: Bearer $TOKEN" "$VL/api/v1/foods?q=skyr&limit=1" | jq -r '.[0].id')
curl -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' $VL/api/v1/nutrition/entries \
     -d "{\"id\": \"$(uuidgen)\", \"logged_at\": \"$(date -Iseconds)\", \"meal_index\": 0,
          \"food_id\": \"$FOOD\", \"amount\": 150, \"entered_via\": \"agent\"}"
```

### Meal templates
A template (Waistline's "meals") is a saved set of foods with amounts; logging it writes one entry
per food (amounts stay editable), marked with `template_id`. A dish (food with components) is the
other way: one entry.

- `GET /api/v1/templates[?include_archived=true]` — with `items` (food, amount, quantity, unit,
  nutrients), summed `nutrients`, `uses`, `last_used`; favorites first.
- `POST /api/v1/templates` `{"id", "name", "items": [{"food_id", "amount", "quantity"}], "favorite"}`
  (`201`/`200` like foods), `PUT`/`GET`/`DELETE /api/v1/templates/{id}` (deleting keeps logged entries).
- `POST /api/v1/templates/{id}/log` `{"logged_at", "meal_index", "ids": [one new entry id per item]}`
  — idempotent through the ids. Entries can also be posted one by one with `template_id`.
- `POST /api/v1/templates/waistline-import` — Waistline's meals from the newest export (the
  takeover does this too).

`GET /api/v1/foods` also takes `kind=dish|food` and `favorites=true`.

### Body measurements
`POST /api/v1/body` `{"id", "ts" (default now), "metric": "weight"|"body_fat"|"body.waist"|
"body.hips"|"body.neck", "value", "source"}` (`201`/`200` like entries), `DELETE /api/v1/body/{id}`,
`GET /api/v1/body?from=&to=&metric=` (entered and imported values, newest first, `editable`). The
drink goal uses the latest weight from either.

### Waistline takeover
`GET /api/v1/nutrition/waistline` — `retired_at` and the newest Waistline export (`received_at`,
`age_days`, `status`). `POST /api/v1/nutrition/waistline-takeover` — reads `foodList` and
`recipes` of that export into the catalog (`source: waistline`, recipes with their items as
components and Waistline's own totals), links the existing entries to them, takes over the meal
names and sets `retired_at`: from then on Waistline uploads are refused and Waistline entries are
editable. Send a fresh export first. `409` when already done or no export is available.

## Supplements

Taken **X of Y** units a day (`per_day` = Y); done when X ≥ Y. Active ingredients per unit are
summed per day and compared with the EFSA tolerable upper intake level for adults (it applies to
the total intake including food).

### Supplement object
| Field | Meaning |
|---|---|
| `id` | UUID (client may supply it) |
| `name`, `brand` | 1–80 characters; brand optional |
| `form` | `capsule`, `tablet`, `drops`, `powder`, `other` |
| `per_day` | units a day, 1–50 |
| `per_week` | days a week it is taken, 1–7 (7 = daily); with fewer, `/supplements/status` gives `week_count`, `week_done` and weekly streaks |
| `ingredients` | per unit: `[{"key": "vitamin_d", "amount": 1000}]` from the catalog (its unit), or `[{"name": "Ashwagandha", "unit": "mg", "amount": 300}]` |
| `timing`, `note` | free text (e.g. "morgens") |
| `started_on` | optional: taken since (also before it was logged here); lab facts use it |
| `position`, `archived` | stored order (unused) and archiving; lists are ordered by type (automatic, tick off, count), then by name |

### Endpoints
- `GET /api/v1/supplements/ingredients`: the catalog (`key`, `label`, `unit`, `upper_level`, `note`);
  vitamin D in IE (upper level 4000 IE = 100 µg).
- `GET /api/v1/supplements[?include_archived=true]`, `POST`, `PUT /api/v1/supplements/{id}`, `DELETE`.
- `PUT /api/v1/supplements/{id}/log/{day}` with `{"count": 2}`: units taken that day; `0` removes it.
- `GET /api/v1/supplements/status[?day=]`: active supplements with `count`, `done`, `streak`,
  `done_last_30`, `days` (365 days, `{value, done}`), plus `ingredients`: per ingredient `label`,
  `unit`, `amount`, `upper_level`, `share_of_upper_level`, `above_upper_level` and `from` (which
  supplements).

## Lab results

A **report** is one blood draw: `collected_on` (day the sample was taken, the date of every value),
`reported_on`, `lab`, optionally the original PDF/image. Original files are kept outside the
export retention and are deleted with their report. Each **value** keeps what was printed and is
linked to an **analyte** of the catalog, so values from different labs compare after unit
conversion. VitaLocal does not interpret results: `status` is the comparison with the printed
reference range (a printed flag such as `H`/`L` wins), `target_status` the one with a personal
target. Interpreting them is left to an agent or a person (`GET /labs/facts`).

### Value (in and out)
| Field | Meaning |
|---|---|
| `analyte_key` | catalog key (`vitamin_d`, `ldl`, `magnesium_wb` …); omitted: matched from `name`, `material` and `unit`, `null` if unknown |
| `name` | as printed, e.g. `"Cholesterin, HDL-"` |
| `value` | as printed: `4.4`, `"4,40"`, `"< 0,5"` (comparator parsed) or a text result like `"negativ"`; out: `value` (number), `comparator`, `value_text`, `printed` (scale kept: `"2.90"`) |
| `unit` | as printed; omitted with a catalog analyte: its default unit |
| `reference` | range as printed (`"< 2,3"`, `"1.3 - 1.8"`, `"> 75"`); bounds parsed into `ref_low`/`ref_high` (can be given directly) |
| `flag`, `material`, `method`, `note` | as printed; `note` is the lab's comment on the value (target values, classifications) |
| out only | `status` (`low`/`normal`/`high`/`null` = cannot be judged, e.g. `"<3"` against `"< 1"`), `status_source` (`flag`/`reference`), `display` (`unit`, `value`, `ref_low`, `ref_high` in the display unit), `target_status` |

Where the specimen changes the meaning there are separate analytes: magnesium, zinc and selenium
in serum (`magnesium`) vs. whole blood (`magnesium_wb`, "EDTA-Blut"/"Vollblut"); the white cell
differential in % (`lymphocytes_pct`) vs. absolute (`lymphocytes_abs`), chosen by the unit.

### Catalog, units, targets
- `GET /api/v1/labs/catalog`: `groups`, `analytes` (`key`, `label`, `group`, `unit` (SI default),
  `conventional_unit`, `units` (all it can be shown in), `display_unit`, `material`, `molar_mass`,
  `synonyms`, `ingredients` (related supplement ingredients), `custom`, `target`) and
  `learned_synonyms`. Conversions: within a dimension (mg/dl ↔ mg/l, G/l = /nl = Tsd/µl), mass ↔
  molar via the molar mass (vitamin D 1 ng/ml = 2.496 nmol/l), HbA1c % ↔ mmol/mol.
- `GET|PUT /api/v1/settings/labs`: `{"system": "si"|"conventional", "units": {"vitamin_d": "ng/ml"}}`.
- `POST /api/v1/labs/analytes` `{"label", "unit", "group"?, "material"?, "molar_mass"?}`: own
  analyte (key `x_…`); `DELETE /api/v1/labs/analytes/{key}` (values stay, compared by name).
- `POST /api/v1/labs/synonyms` `{"name", "material"?, "analyte_key"}`, `DELETE ?name=&material=`:
  printed names mapped by hand. Saving a report learns them automatically when a value's
  `analyte_key` differs from the automatic match (`"learn_synonyms": false` to skip).
- `PUT /api/v1/labs/targets/{key}` `{"min_value"?, "max_value"?, "unit"?, "note"?}` (unit defaults
  to the display unit), `DELETE`.

### Reports and overview
- `GET /api/v1/labs/reports`: newest first, with `n_values`, `n_outside`, `has_file`, `source`.
- `POST /api/v1/labs/reports` `{"id"?, "collected_on", "reported_on"?, "lab"?, "note"?, "values": [...]}`,
  `GET|PUT|DELETE /api/v1/labs/reports/{id}` (PUT = full state; values not listed are removed).
- `PUT /api/v1/labs/reports/{id}/file` (multipart `file`), `GET …/file`: the original.
- `GET /api/v1/labs/overview`: per `groups[].rows[]` one analyte (uncatalogued values:
  `key` = `name:<normalized name>`) with `unit` (display), `latest`, `previous`, `status`,
  `target`, `target_status`, `delta`, `trend` (`up`/`down`/`stable`, ±5 %), `count` and `points`
  (every value with `date`, `lab`, `y`/`y_ref_low`/`y_ref_high` in the display unit, `note`).
- `GET /api/v1/labs/analytes/{key}/history`: one such row.
- `GET /api/v1/labs/facts[?day=][&new_days=30]`: prepared for agents: `latest_report`, new
  values, latest values outside the lab reference or the personal target, `all_latest`, each with
  `previous`, `change_pct`, `history`, `lab_comment`, `as_printed` (when converted) and
  `related_supplements_before_draw` (`taking_since`, complete days in the 90 days before the
  draw), plus `supplements_taken` (since when, how regularly). The day and week summary get the
  compact form (without history and comments) under `lab_results`.

### Import (local model)
1. `POST /api/v1/labs/imports` (multipart `file`: PDF or PNG/JPEG/TIFF/WebP, ≤ 30 MB,
   `client_meta` optional) → `202` with the import (`status: pending`). `409` when no model is
   configured that reads images (`/settings/ai`: `enabled`, `base_url`, `model`, `vision`), or
   when an external model may not see lab reports (`share.labs`).
2. In the background every page (PDF rendered with pdfium, plus its text layer) goes to the model
   with a JSON schema (`response_format`, retried without it if the server refuses). The model
   only transcribes; no personal data, no conversion, no judgement.
3. `GET /api/v1/labs/imports/{id}`: `status` `running` → `ready` (or `failed` with `error`);
   `proposal`: `lab`, `collected_on`, `reported_on`, `values` (as read, plus `analyte_key`,
   `comparator`, `ref_low`/`ref_high`, `status`, `found_in_text` (value appears in the PDF text
   layer; `null` for images), `warnings`, `source_line`, `page`), `page_errors`,
   `possible_duplicates`. `GET …/pages/{n}` is the page image the model saw, `GET …/file` the upload.
4. `POST /api/v1/labs/imports/{id}/confirm` with a report body (as `POST /labs/reports`) saves
   **exactly the values sent** — the ones confirmed in the review — with the upload as original
   and the proposal in `import_meta`. Nothing is saved before this call.
- `GET /api/v1/labs/imports[?include_done=true]`, `POST …/retry`, `DELETE …/{id}` (discards the
  import and the uploaded file unless a report uses it).

## Training

Strength training on top of the activities. The watch records sets, but its exercise recognition
is rough, so each workout is **reviewed**: VitaLocal proposes the plan day and an exercise per set,
you correct and confirm and rate each exercise **too easy / good / too hard**. Only reviewed
workouts feed the progression and the stats. Everything survives re-imports (it is keyed by the
activity and the FIT set index; added sets live in their own table).

### Exercises
Own exercise library, linked to the watch through the Garmin category (and optionally name).

| Field | Meaning |
|---|---|
| `id`, `name` | UUID (client may supply it), unique name |
| `garmin_category`, `garmin_name` | FIT `exercise_category` and exercise name (`GET /api/v1/exercises` lists them) |
| `kind` | `reps` or `time` (plank) |
| `muscles`, `muscles_secondary` | keys of `GET /training/muscles` (incl. `serratus`); omitted on create: the category's default (curl → biceps, forearms) |
| `increment_kg` | weight step of the progression (default 2.5) |
| `bodyweight` | weight is extra load (pull-ups); no 1RM |
| `equipment` | keys of `GET /training/equipment` (barbell, dumbbell, cable, machine, band …); none: body weight only; omitted on update: unchanged |
| `library_key` | the built-in library entry the exercise was taken from (set on create or by `adopt`) |

- `GET /api/v1/training/exercises[?include_archived=]`, `POST`, `PUT /training/exercises/{id}`,
  `DELETE` (only unused ones; archive the others).
- The whole Garmin catalog (52 categories, 1846 exercises) is `GET /api/v1/exercises`; the WebUI
  searches it in every exercise picker and creates the own exercise on first use.
- `GET /api/v1/training/exercises/suggestions`: categories/names the watch recorded that no own
  exercise covers yet, with set counts.
- `GET /api/v1/training/library[?muscle=&equipment=&equipment=…&q=]`: the built-in library (about 140
  common exercises with German names, Garmin exercise, muscles, equipment, kind, weight step, note);
  `equipment` lists what is available (once empty: none), entries needing anything else are left
  out; `exercise_id` is the own exercise taken over from an entry. `POST
  /api/v1/training/library/{key}/adopt` makes an entry an own exercise (201) or returns the one
  already linked or with the same name/watch exercise (200).

### Plans
A plan has **days in rotation order** (A → B → C → A …); each day has entries: `exercise_id`,
`sets`, `reps_min`–`reps_max` (or `duration_s`), `rest_s`, the current target `target_reps` /
`target_weight_kg`, `note`. The same exercise may appear twice on a day (heavy and light).
One plan is `active`: its rotation is suggested.

- `GET /api/v1/training/plans`, `POST` (plan with days and entries in one body),
  `PUT /training/plans/{id}` (full state: days/entries not listed are removed, listed ids are kept
  so workouts stay linked), `DELETE`.
- `GET /api/v1/training/plan-days/{id}/targets`: per entry `current`, `next` (suggestion: `sets`,
  `reps`, `weight_kg`, `duration_s`, `text`, `reason` = rating it builds on, `based_on`, `unrated`,
  `notes`), `differs` and `last` (date, rating, sets).
- `PUT /api/v1/training/plan-exercises/{id}/target` `{"target_sets"?, "target_reps", "target_weight_kg", "duration_s"?}`:
  take over a suggestion (`target_sets` = the entry's `sets` or omitted: stored as none, `current` shows the entry's sets).
- `PUT /api/v1/training/plan-exercises/{id}/declined` `{"suggestion": "3×9 @ 23 kg" | null}`: decline a
  suggestion. While the progression suggests exactly that text, the entry shows `differs: false`,
  `declined` (the text) and `next` = the current target (`reason: "declined"`), so the next workout
  keeps the target; another suggestion is offered as usual. `null` offers it again; taking a target
  over clears it.
- `GET /api/v1/training/next`: the next day of the active plan (after the last reviewed workout or
  skip) with its targets as above, plus `unreviewed_sessions`; `null` without an active plan.
- `POST /api/v1/training/skips` `{"plan_day_id"?, "note"?}`: skip a plan day (default: the next one);
  the rotation goes on with the day after it. Returns `id`, `skipped_at`, `plan_day_id`, `plan_day`,
  `plan`, `note`. `DELETE /api/v1/training/skips/{id}` undoes it. Skips do not touch the progression.
- `GET /api/v1/training/plan/current`: the active plan with every day and its targets as above
  (`days[].exercises`), `next_day_id` = the day due in the rotation; `null` without an active plan
  (the Android app's training tab).

**Progression** (double progression per entry, replayed over the last 12 reviewed performances;
`sets` of the entry is the standard): a target is met when that many sets reach its reps at its
weight (a weaker extra set does not count against it) and the rating is not too hard. Met → +1 rep
(from the best `sets` sets) until `reps_max`, then +1 weight step and back to `reps_min`; too easy
→ +1 step now; missed → same again (`missed once, same again`). Missed twice in a row → volume variant: one set more with two
reps less at the same weight (`notes`: `volume variant: 4×7 instead of 3×9`); met there → back to
the target that was missed (`back to 3×9`). Missed twice in the volume variant, or two reps less
would fall below `reps_min` → −1 weight step at `reps_max` (`weight down after two misses`).
Unrated counts as good (`unrated: true`). Time exercises: +5 s (good), +10 s (too easy) from the
shortest set; a loaded hold (farmer's walk) keeps the load it was done with, and its targets read
`3×45 s @ 30 kg` (`current` of a time exercise always shows seconds, never the rep range).

### Workouts (sessions)
- `GET /api/v1/training/sessions[?from=&to=]`: strength workouts (watch or manual) with
  `display_title`, `plan_day`, `sets`, `reviewed`, `unassigned` (work sets without an exercise).
- `GET /api/v1/training/history[?from=&to=&offset=0&limit=20]`: the same plus skipped plan days,
  one page at a time: `{"total", "offset", "limit", "items": [...]}` (newest first; `limit` ≤ 200;
  `kind` `session` or `skip`; a skip has `id`, `start_ts` = when skipped, `plan_day`, `plan`, `note`).
- `GET /api/v1/training/sessions/{activity_id}[?plan_day_id=]`: `sets` (work sets in order: `key` `w:<set_index>`
  for watch sets, `m:<uuid>` for added ones; `exercise_id`, `plan_exercise_id`, `repetitions`,
  `weight_kg`, `duration_s`, `warmup`, `hidden`, the watch's `garmin_category`/`garmin_name` and
  `original` values), `exercises` (per exercise or entry: sets as text, `volume_kg`,
  `best_e1rm_kg`, `rating`; for a plan entry also `planned` (the target text the progression set
  for this workout) and `suggestion` `{rating, why}`: a missed set, rep, weight or time → `hard`,
  every set ≥ 3 reps / 15 s over the target → `easy`, else `good`, counting the best `sets` sets at the weight; `why` is `fewer sets`,
  `target missed`, `lighter weight`, `target met` or `target exceeded`; null without work sets),
  `ratings`, `planned` (targets of the plan day as of before the workout)
  `unassigned_sets` and, while not reviewed, while work sets have no exercise yet or with `plan_day_id`
  (the day picked in the review: proposal and `planned` for that day), `proposal`
  (`only_unassigned: true` for a reviewed workout: only its missing exercises are proposed): `plan_day_id`, `reason` (`rotation`, `exercises match` or `chosen`)
  and per watch set `exercise_id`, `plan_exercise_id`, `warmup`, `hidden`. A day other than the
  rotation's is proposed when it covers at least half of the recognised sets and a quarter more than
  the rotation's day. An entry matches its exercise's category and the categories the watch recorded
  for that exercise in at least two reviewed workouts (incline curls → `curl`). The proposal treats the
  warm-up block and sets under 10 s as no work sets, follows the watch's category (in any order of the
  exercises: to the entry with that category that still lacks sets), keeps a set with
  an unchanged weight with the exercise before and uses an own exercise for categories the day
  does not have.
- `PUT /api/v1/training/sessions/{activity_id}`: review: `title`, `note`, `plan_day_id`,
  `reviewed` (default true), `sets` (watch sets listed are updated, others keep their state;
  added sets are the full list: without `key` they are new, `position` orders them among the watch
  sets), `ratings` `[{"exercise_id", "plan_exercise_id"?, "rating": "easy"|"good"|"hard"}]` (full state).
- `PUT /api/v1/training/sessions/{activity_id}/ratings` `{"ratings": [...]}`: only the ratings
  (full state), sets and review stay as they are; returns the session.
- `GET /api/v1/training/sessions/{activity_id}/as-plan-day`: the workout as a template for a plan
  day (an element of `days` for `POST /training/plans`): exercises in order (an exercise done in two
  separate blocks gives two entries), sets as done, rep range from fewest to most reps, target =
  fewest reps at the weight used most often. `422` while no set is assigned to an exercise.
- `POST /api/v1/training/sessions`: manual workout: `manual_key` (client UUID), `start_ts`,
  `duration_min`, plus the review fields; sets without `key`.
- `GET /api/v1/training/sessions/{activity_id}` also lists `overlaps`: strength workouts of the other
  kind (watch vs. entered by hand) whose time overlaps, i.e. the same workout recorded twice.
  `POST /api/v1/training/sessions/{manual_id}/merge` `{"into": <watch activity id>}` makes them one:
  the watch workout keeps heart rate and energy and takes the hand-entered sets, ratings, title, note
  and plan day (reviewed); its own sets are hidden; the hand-entered workout is deleted.
- `DELETE /api/v1/training/sessions/{activity_id}`: a manual workout is deleted; for a watch
  workout only the review layer is removed.

### Stats and facts
- `GET /api/v1/training/stats[?weeks=12][&progress_weeks=]`: per week `sessions` and `hard_sets`
  per muscle (work sets of reviewed or assigned workouts; primary muscles 1, secondary ½;
  warm-up/hidden sets not counted), per rep exercise the best estimated 1RM per workout (`e1rm`;
  Epley, 1–12 reps, not for bodyweight exercises) and per time exercise the best set per workout
  (`holds`: `duration_s`, `weight_kg`; heaviest load first, then longest). The two series cover
  `progress_weeks` (default `weeks`, up to 520), starting at `progress_from`.
- `GET /api/v1/training/facts[?day=][&days=28]`: for agents: workouts with exercises, sets,
  ratings and notes, hard sets per muscle by week, the next workout with targets and suggestions,
  muscles not yet recovered (`muscle_recovery`), 1RM series. The day and week summary get the
  compact form under `strength_training`.

### Deload and structural balance
- A plan may carry `deload_every_weeks` (2–16, null: only when needed). `GET /api/v1/training/deload`:
  the active plan's deload week (`active`, `from`, `until`), `every_weeks` and whether one is
  `suggested` with `reasons` (`scheduled` with `weeks`; `stalls` with `exercises` rated too hard twice in
  a row, two or more; `hard_ratings` with `hard`/`of` when most ratings of two weeks were too hard).
  `POST /api/v1/training/deload` `{"days": 7}` starts one today, `DELETE` ends it (yesterday was its
  last day). In the week the targets (`/next`, `/plan/current`, day targets) are one set less at the
  last trained weight with reps or seconds at the bottom of the range (`deload: true`, `regular`: the
  usual suggestion, `differs: false`); workouts in it are marked `deload` (sessions take `deload` to set
  it by hand) and left out of the progression. `/next` carries the status under `deload`.
- `GET /api/v1/training/balance[?weeks=8]`: work sets per week by movement pattern (from the Garmin
  category: horizontal/vertical push and pull, knee- and hip-dominant; biceps/triceps from hard sets)
  in pairs with a sensible range, and the best estimated 1RM per pattern against rough references
  (row ≈ bench press, overhead press ≈ 60 % of it, squat ≈ 80 % of the deadlift; dumbbells count both
  hands). `state`: `ok`, `low`, `high` (one side without sets counts as an imbalance), `missing`.
  The plan review gets both as facts.

### Muscles
- `GET /api/v1/training/muscles/state[?at=]`: recovery per muscle (all keys of `/training/muscles`):
  `fatigue` (one full session ≈ 1), `state` (`ready` < 0.25 ≤ `recovering` ≤ 0.5 < `fatigued`),
  `ready_in_h`, `half_life_h`, `last_trained` (within six weeks), `hard_sets_7d` and `sessions`
  still contributing (`id`, `ts`, effective `sets`, `exercises`, `remaining` share, `provisional`
  when the workout is not reviewed yet and counts with VitaLocal's proposal). Model: work sets ×
  rating (too hard 1, good/none 0.85, too easy 0.6), secondary muscles ½, at most 12 per workout,
  scaled by the last 7 days against the 28 before (0.5–1.5), halving every 18 h (arms, shoulders,
  core), 24 h (chest, back, traps) or 36 h (legs, glutes, lower back). `garmin_recovery_h`: the
  watch's own recovery time left, for comparison.
- `GET /api/v1/training/muscles/volume[?weeks=4]`: hard sets per muscle over the weeks (as in the
  stats) and per week.
- `GET /api/v1/training/muscles/of?exercise_id=` or `?plan_day_id=`: what an exercise (primary 1,
  secondary ½) or a plan day (its sets × those shares) trains.
- The body outlines the WebUI and app draw are `/body-paths.json` (MuscleMap by Melih Colpan, MIT).

## AI coach

The local model (settings as for the summaries) reads facts VitaLocal prepares and answers in a
fixed JSON shape; a validator decides what is accepted (one repair round, then the request fails).
It never changes plan, targets or data.

- `GET /api/v1/coach/status`: `enabled` (a model is set up) and `model`.
- `POST /api/v1/coach/debrief` `{"activity_id", "lang": "de"|"en"}` → 202 with the request
  (`status: pending`): the debrief of one workout from the session, each exercise's previous times
  (`days_before`), ratings too hard in a row, the progression's next targets, muscles not yet
  recovered before it and the body weight trend. 409 without a model or without assigned sets.
- `GET /api/v1/coach/messages[?activity_id=&limit=20]`, `GET /api/v1/coach/messages/{id}`: requests
  newest first with `status` (`pending` → `done`/`error`), `result` (`summary`, `score` 1–10,
  `highlights`, `watch`, `next_time`), `model`, `repaired`, `duration_ms`, `error`, `title`,
  `session_date`. Poll a pending one; a request cut off by a restart turns into an error.
- Plans from a questionnaire (WebUI): `GET /api/v1/coach/profile` (last answers or null);
  `POST /api/v1/coach/plan` `{"answers": {goal: muscle|strength|fitness|fat_loss, experience:
  beginner|intermediate|advanced, days_per_week, minutes, equipment: [keys], limitations, focus: [muscle
  keys ≤ 6], likes, dislikes, cardio}, "lang", "refines"?, "change"?}` → 202, a coach message of kind
  `plan`. The model may only use own exercises and library entries the equipment allows; the validator
  checks ids, sets 1–6, rep range or seconds (timed exercises), rest 30–300 s, minutes per day against
  the wished length (+30 %) and that every focus muscle is trained. Result: `name`, `summary`, `days`
  (rotation order; exercises with `id`, `name`, `sets`, `reps_min`/`reps_max` or
  `seconds_min`/`seconds_max`, `rest_s`; `minutes` per day), `notes`, `hard_sets_per_week`.
  `refines` + `change` revises a finished proposal.
- Plan review (WebUI): `POST /api/v1/coach/review` `{"note"?, "lang"}` → 202, kind `review`: the active
  plan against the last 12 weeks (each entry's last sessions and ratings, too hard in a row, the
  progression's next targets, adherence and skips per day, session length, hard sets per muscle,
  profile, catalog). Result: `nochange`, `reading`, `changes` (types `swap_exercise`, `add_exercise`,
  `remove_exercise`, `sets`, `rep_range`, `duration_range`, `rest`, `reorder`, `rename_day`, `remove_day`,
  `reorder_days`; each with `why`, `where`, `before`, `after`), `notes`. Never weights or targets.
  `POST /api/v1/coach/messages/{id}/take-over` `{"changes": [indexes]}` makes the chosen changes (409 when
  the plan changed since the review; a swapped entry restarts its progression); `POST …/undo` restores
  the plan as it was (409 when it changed afterwards). `DELETE /api/v1/coach/messages/{id}` discards a
  proposal, review or debrief.
- `POST /api/v1/coach/messages/{id}/apply` `{"active": false}` → 201, the new plan (library entries
  become own exercises; no weights or targets: the progression sets them from the first session).

## Journal and check-ins

A check-in is one timestamped manual observation.

### `POST /api/v1/checkins`

```json
{
  "id": "8f0c2c4e-3a51-4c43-9d07-4b0a3f6f1e21",
  "kind": "mood",
  "ts": "2026-09-28T13:51:00+02:00",
  "value": 4,
  "source": "agent"
}
```

| `kind` | `value` | Notes |
|---|---|---|
| `mood` | 1–5, required | |
| `energy` | 1–5, required | felt energy |
| `water` | deprecated | still accepted from old clients and stored as a drink (`label` = drink) |
| `symptom` | 1–5 severity, optional | `label` (symptom name) required |
| `rpe` | 1–10, required | set `activity_id` to attach it to a workout |
| `note` | none | `note` text required |

`id` is a client-generated UUID: `201` when stored, `200` with the stored row when the id already
exists. `ts` defaults to now. `source` is free text (max 40 characters), e.g. `webui`, `android`,
`agent`. `note` (max 4000 characters) can go on any kind.

### `GET /api/v1/checkins?from=…&to=…[&kind=mood]`, `DELETE /api/v1/checkins/{id}`
List by time range (timestamps, not days) and delete. The delete also removes a drink with that id
(drinks were check-ins before server 0.2).

### `GET /api/v1/symptoms`
The 30 most-used symptom names, for quick re-entry.

### Texts: day entry, weekly and monthly review

All three are one Markdown text (max 20,000 characters) with the same write rules:

- `GET` returns `text`, `version` (0 = never written), `source` (who wrote last) and `updated_at`,
  plus the header `ETag: "<version>"`.
- `PUT {"text": "…", "version": 3, "source": "agent"}` (or header `If-Match: "3"` instead of
  `version`) writes only if the stored version is still 3, and returns the new state. Otherwise
  `409` with `detail.current` (the stored text and version), so nothing is overwritten silently.
  Without `version`/`If-Match` it is last-write-wins. Writing the stored text again is a no-op.
- An empty text clears the entry (the version keeps counting).
- The replaced text is kept in the revision history, except while the same `source` rewrites its
  own text within 10 minutes (autosave); a cleared text is always kept.
- To append (e.g. an agent adding a thought): `GET`, then `PUT` the combined text with the
  `version` you read; on `409` read again and retry.

### `GET /api/v1/journal/{day}`, `PUT /api/v1/journal/{day}`
The day's entry, plus (on `GET`) the day's check-ins.

### `GET|PUT /api/v1/journal/week/{day}`, `GET|PUT /api/v1/journal/month/{day}`
Review of the Monday–Sunday week or the calendar month containing `day`; the response has `start`
and `end`. `GET` adds `facts`, the period's numbers computed now, and `facts_saved`, the snapshot
taken at the last write (so a review can be checked against what it was written on):
`metrics` (`avg`, `min`, `max`, `days` with a value and `previous_avg` of the preceding period of
equal length, for sleep score, HRV, resting HR, steps, active energy, stress, intake, drinks, mood,
felt energy), `sleep` (average bed/wake time, asleep minutes, nights), `activities` (count,
minutes, kcal, by name), `symptoms` (days, max severity), `weight` (first/last), `journal_days` and
`habits` (done days vs. goal). The WebUI shows the week on Sundays and the month on its last day.

### `GET /api/v1/journal/reviews?from=…&to=…`
Weekly and monthly reviews ending in the range, newest first, with an `excerpt`.

### `GET /api/v1/journal/revisions?day=…[&scope=day|week|month]`
Earlier texts, newest first (`version`, `text`, `source`, `written_at`). Restoring is a normal `PUT`.

### `GET /api/v1/journal/search?q=…[&limit=30]`
Full-text search (German stemming; `"exact phrase"`, `or`, `-word`) over day entries, reviews and
check-in notes/symptoms. Each hit: `scope` (`day`, `week`, `month`, `note`), `start`, `end`,
`snippet` with matches wrapped in `⟦ ⟧`, `rank`. Newest first.

### `GET /api/v1/journal?from=2026-09-01&to=2026-09-28`
Per day, newest first: `excerpt` (first 280 characters), average `mood`/`energy`, `symptoms`,
number of `checkins`.

## Habits

### Habit object

| Field | Meaning |
|---|---|
| `id` | UUID (client may supply it on create) |
| `name` | 1–80 characters |
| `icon` | optional emoji (≤ 16 characters); empty becomes `null` |
| `template` | key of the template the habit was added from (`/habits/templates`), else `null` |
| `hard` | hard habit: two flames per done day from `hard_since` (the day it was marked) on |
| `kind` | `check` (done/not done) or `count` (number against `target`) |
| `unit`, `target` | for `count`, e.g. `target: 20`, `unit: "pages"` |
| `per_week` | 1–7; 7 = daily, otherwise N times per week |
| `metric`, `min_value`, `max_value` | automatic habits: done when the metric's daily value lies within `min_value` … `max_value`; either bound may be `null` (open), at least one is set. `comparator` + `threshold` (`>=`/`<=`) from API 0.2 are still accepted on input and turned into a bound |
| `position`, `archived` | ordering and archiving |

**Clock metrics** (`unit: "clock"` in the catalog) take times of day as minutes after midnight
(`22:20` = 1340) and compare in the order of their own day, so a window may cross midnight
(`min_value` 1420, `max_value` 20 = 23:40 … 00:20):

| Metric | Value of a day |
|---|---|
| `sleep.bedtime` | start of the sleep the watch recorded; counts for the **evening** it began (bed at 00:30 on Tuesday counts for Monday) |
| `sleep.wake_time` | end of that sleep (including a final awake stage), for the morning it ended |
| `drinks.last_caffeine` | time of the day's last drink with `caffeine: true`; a day with drinks but none caffeinated is done (`none: true`), a day without any drink is unknown |

Derived values: `activities.minutes` is the duration of the day's recorded activities (watch and
manual workouts; 0 on a day the watch counted steps without one), e.g. a workout habit
`{"metric": "activities.minutes", "min_value": 20, "per_week": 3}`. `supplements.taken` is the
share (%) of the due supplements taken completely that day (a supplement with `per_week` < 7 counts
on the days it was taken, and as missed on Sunday when its weekly goal was not reached) (a supplement counts from its `started_on`; days before the first logged
intake are unknown), so `min_value: 100` means "all supplements taken". `floors_climbed` (and the
other ascent metrics) is 0 on a day the watch counted steps but no ascent, so such a day is a miss
rather than unknown.

Examples: bed between 21:50 and 22:20 → `{"metric": "sleep.bedtime", "min_value": 1310,
"max_value": 1340}`; no caffeine after 14:00 → `{"metric": "drinks.last_caffeine", "max_value": 840}`.

### Endpoints
- `GET /api/v1/habits[?include_archived=true]`
- `GET /api/v1/habits/metrics`: metrics an automatic habit can use
- `POST /api/v1/habits`, `PUT /api/v1/habits/{id}` (full object), `DELETE /api/v1/habits/{id}`
- `PUT /api/v1/habits/{id}/log/{day}` with `{"value": 1}`: log a manual habit
  (`check`: 1 = done, `count`: the number). `0` removes the log. `422` for automatic habits.
- `GET /api/v1/habits/status?from=…&to=…[&today=…]`: every active habit with
  `days: {"2026-09-28": {"value", "done", "auto"?}}`, `streak`, `streak_unit` (`days`/`weeks`),
  `done_last_30` and, for weekly habits, `this_week`. At most 800 days. For automatic habits a day
  without data is absent (unknown), not missed. Automatic entries also carry `delta`: how far the
  value lies outside the bounds (negative below/earlier, positive above/later, `0` within; minutes
  for clock metrics), and for clock metrics `display` (`"22:07"`).

- `GET /api/v1/habits/templates`: predefined habits (`key`, English `name`/`why`/`category` for
  the client to translate, `icon` and the habit fields); `added` when an active habit has that
  `template`.

**Jokers**: a daily habit earns a joker for every 7 days of a streak (at most 2 banked). A missed
day is bridged automatically while one is left: the streak goes on (the bridged day itself does
not count) and the day appears in `days` with `"joker": true`. `/habits/status` gives the jokers
left as `jokers`. Days without data of an automatic habit neither count nor break a streak.

**Streaks start when a habit is tracked**: an automatic habit's `days` reach back before it was
created (its history), but `streak` — and flames and achievements — count from its creation day.
Manual habits count from their first log.

### Rewards (flames, levels, achievements)
- `GET /api/v1/habits/rewards[?today=…]`: every done day of a habit (a count habit: target
  reached) earns one **flame**, archived habits included. `level`: level n needs
  `5·n·(n−1)` flames (10, 30, 60, 100, …), with an English `title` (Spark, Ember, Flame, Blaze,
  Bonfire, Beacon, Firestorm, Phoenix). `best_current` / `best_ever`: longest running and longest
  ever streak (weekly streaks compare by time span). `perfect`: days on which every active daily
  habit with a known status was done (`current`, `best`, `total`, `today`, `open_today` ids).
  `habits`: per habit `flames`, `streak`, `best_streak` (+ `best_from`/`best_to`), `unit`, `tier`,
  `next_tier`, `tracked_from`. `achievements`: every badge with `key`, `group`, `habit_id`,
  `title`, `icon`, `tier` (bronze/silver/gold/diamond), `target`, `progress`, `params`,
  `unlocked_on` (the day it was earned) and `new`; `new` repeats the unseen ones.
  Earned achievements are stored and stay earned even when logs are removed later.
- `POST /api/v1/habits/rewards/seen` (optional body: list of keys): marks new achievements as seen.
- The rewards also carry: `flames.habits` / `flames.bonus` / `flames.by_day`; `garden` (per active
  habit `plant`, `stage`, `stage_index`, `stage_title`, `growth`, `floor`, `next_at`, `next_plant`,
  `health` healthy/thirsty/wilting, `misses`; growth +1 per done day, −3 per missed day, joker days
  and days without data 0; weekly habits +7/per_week per done day, −7 per missed week; stages at
  0/1/3/7/21/66/100); `routines` (with `days`, `completions`, `streak`, `today`, `done_today`,
  `bonus` = one flame per habit, earned on every day the whole routine is done); `challenges` of the
  current week (`kind` habit/perfect/routine/flames, `target`, `progress`, `bonus`,
  `completed_on`) — created on the first look at a week from the previous week's numbers, and their
  bonus flames count on the day they were completed; `habits[].jokers`, `hard`, `suggest_hard`
  (done on less than 40 % of the goal over four weeks), `plant`.
- `GET/POST /api/v1/habits/routines`, `PUT/DELETE /api/v1/habits/routines/{id}`: routines
  (`name`, `icon`, `habit_ids` in order, 2–10 habits, `position`, `archived`).
- Every achievement carries `series` (achievements of one kind, e.g. `flames` or
  `habit:<id>:streak`), `rank` (0-based) and `ranks`; within a series `tier` is the rank's colour:
  bronze, silver, gold, platinum, diamond, master, grandmaster, legend. Clients show a series once
  its first rank is earned.
- Weekly and monthly review `facts.rewards`: `flames`, `previous_flames`, `perfect_days`,
  `challenges` (`done`, `total`), `routines_completed`, `achievements` earned in the period, `level`.

```bash
curl -X PUT -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{"value": 1}' $VL/api/v1/habits/$HABIT_ID/log/2026-09-28
```

## AI summaries

AI is optional. It uses a model through an OpenAI-compatible endpoint, in the local network or at
a hosted provider. A host outside the local network (not localhost, a private IP, a single-label
name or `.local`/`.lan`/`.arpa`/`.home`/`.internal`/`.test`) counts as `external`. It gets only
the features and data in `share`; journal text and lab reports are off by default.

- `GET /api/v1/settings/ai`, `PUT /api/v1/settings/ai`: `enabled`, `base_url`, `model`, `api_key`
  (write-only, stored encrypted; `null` keeps the stored key, `""` clears it), `temperature`,
  `timeout_s`, `focus`, `vision` (the model reads images; needed for lab imports), `share`
  (`{summary, coach, labs, journal}`; `null` keeps the stored choice). The answer also has
  `configured`, `external` and `has_api_key`.
- `POST /api/v1/ai/models` `{"base_url", "api_key"?}`: connection test, returns
  `{"ok", "models"}` or `{"ok": false, "error"}`.
- `GET /api/v1/ai/summary?day=…&scope=day|week&lang=de|en`: latest stored summary (or `null`).
- `POST /api/v1/ai/summary` `{"day", "scope", "lang"}`: generate one now (can take minutes;
  `502` if the model fails). The result includes the facts sent to the model (`context`).

## Data quality

- `GET /api/v1/quality/coverage?from=…&to=…[&tz=][&metric=]`: per metric and day: sample
  count, first/last sample, largest gap.
- `GET /api/v1/quality/reconciliation[?batch_id=]`: what a batch contained vs. what was parsed.
- `GET /api/v1/quality/activity-sets`: FIT sets vs. Gadgetbridge's set table.

## Examples for agents

```bash
H=(-H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json')

# mood check-in (generate the UUID once per message so retries stay idempotent)
curl "${H[@]}" -d "{\"id\":\"$(uuidgen)\",\"kind\":\"mood\",\"value\":4,\"source\":\"agent\"}" \
     $VL/api/v1/checkins

# write the day's journal, safely: read the version, write against it (409 = someone else was faster)
V=$(curl -s "${H[@]}" $VL/api/v1/journal/2026-09-28 | jq .version)
curl -X PUT "${H[@]}" -d "{\"text\":\"Gut geschlafen, Training war zäh.\",\"version\":$V,\"source\":\"agent\"}" \
     $VL/api/v1/journal/2026-09-28

# weekly review with the week's numbers
curl "${H[@]}" $VL/api/v1/journal/week/2026-09-27 | jq .facts
curl "${H[@]}" "$VL/api/v1/journal/search?q=Kopfschmerzen"

# the quick-add water was a coffee: correct the entry (full entry, safe to repeat)
curl -X PUT "${H[@]}" -d '{"ts":"2026-09-28T15:30:00+02:00","drink":"coffee","ml":250}' \
     $VL/api/v1/drinks/$DRINK_ID

# habits: in bed between 21:50 and 22:20; no caffeine after 14:00 (clock values = minutes after midnight)
curl "${H[@]}" -d '{"name":"Ins Bett","metric":"sleep.bedtime","min_value":1310,"max_value":1340}' \
     $VL/api/v1/habits
curl "${H[@]}" -d '{"name":"Kein Koffein nach 14 Uhr","metric":"drinks.last_caffeine","max_value":840}' \
     $VL/api/v1/habits

# goals of a day with status; a protein range from next Monday on
curl "${H[@]}" "$VL/api/v1/goals?day=2026-09-28" | jq '.goals[] | {label, value, min_value, max_value, status}'
curl -X PUT "${H[@]}" -d '{"min_value":120,"max_value":160,"valid_from":"2026-10-05"}' $VL/api/v1/goals/nutrition.protein

# supplements: add one, tick off today's 2 drops, see the day's ingredients against EFSA levels
curl "${H[@]}" -d '{"name":"Vitamin D3","form":"drops","per_day":2,"ingredients":[{"key":"vitamin_d","amount":1000}]}' \
     $VL/api/v1/supplements
curl -X PUT "${H[@]}" -d '{"count":2}' $VL/api/v1/supplements/$SUPPLEMENT_ID/log/2026-09-28
curl "${H[@]}" "$VL/api/v1/supplements/status?day=2026-09-28" | jq .ingredients

# lab results: what is new, outside the reference, how it developed, and the related supplements
curl "${H[@]}" "$VL/api/v1/labs/facts?new_days=60" | jq '.new_values_last_60_days'
curl "${H[@]}" $VL/api/v1/labs/analytes/vitamin_d/history | jq '[.points[] | {date, y, status}]'

# enter a report from a message (id once per message; names/units as printed, analytes are matched)
curl "${H[@]}" -d "{\"id\":\"$(uuidgen)\",\"collected_on\":\"2026-09-15\",\"lab\":\"Hausarzt\",
  \"values\":[{\"name\":\"Ferritin\",\"value\":\"85\",\"unit\":\"ng/ml\",\"reference\":\"30 - 400\"},
             {\"name\":\"TSH\",\"value\":\"2,3\",\"unit\":\"mU/l\",\"reference\":\"0,27 - 4,2\"}]}" \
     $VL/api/v1/labs/reports

# a personal target agreed with the doctor: LDL below 2.6 mmol/l
curl -X PUT "${H[@]}" -d '{"max_value":2.6,"unit":"mmol/l","note":"mit dem Hausarzt abgestimmt"}' $VL/api/v1/labs/targets/ldl

# a PDF from a message: upload, wait for the model, then the person reviews it in the WebUI
curl -H "Authorization: Bearer $TOKEN" -F file=@befund.pdf $VL/api/v1/labs/imports | jq '{id, status}'
curl "${H[@]}" $VL/api/v1/labs/imports | jq '.[] | {id, status, n_values}'

# training: what was done, how it was rated, what is next (the agent plans and comments, VitaLocal counts)
curl "${H[@]}" "$VL/api/v1/training/facts?days=28" | jq '.next_workout, .hard_sets_per_muscle_by_week'
curl "${H[@]}" $VL/api/v1/training/next | jq '.exercises[] | {exercise, current, next: .next.text, reason: .next.reason}'

# a workout reported in a message, without the watch (manual_key once per message)
curl "${H[@]}" -d "{\"manual_key\":\"$(uuidgen)\",\"start_ts\":\"2026-09-30T18:00:00+02:00\",\"duration_min\":40,
  \"plan_day_id\":\"$DAY_ID\",\"sets\":[{\"exercise_id\":\"$CURL_ID\",\"repetitions\":10,\"weight_kg\":12}],
  \"ratings\":[{\"exercise_id\":\"$CURL_ID\",\"rating\":\"good\"}]}" $VL/api/v1/training/sessions

# take over the suggestion for one entry
curl -X PUT "${H[@]}" -d '{"target_reps":9,"target_weight_kg":15.5}' $VL/api/v1/training/plan-exercises/$ENTRY_ID/target

# context for a reflective question: the day plus the last week
curl "${H[@]}" $VL/api/v1/days/2026-09-28
curl "${H[@]}" "$VL/api/v1/ai/summary?day=2026-09-28&scope=week"
```
