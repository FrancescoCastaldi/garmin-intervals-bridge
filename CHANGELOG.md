# Changelog

## Unreleased

### Changed

- **More of the morning.** Recovery time, acute load, endurance and hill
  scores, cycling VO₂max, fitness age and race predictions now arrive with the
  morning read, as Garmin reports them at wake-up, instead of the day after;
  the day's final read replaces them with the evening's values. Five more
  endpoints in the morning read: about 80 Garmin requests a day instead of 75.
- `GarminSleepSpO2Avg` and `GarminSleepRespirationAvg` are written in the
  default profile again (an AI coach reading Garmin's names finds them).

### Added

- **Fluid and food per activity:** what you log in Garmin (`waterConsumed`
  in ml, `caloriesConsumed` in kcal) goes into the activity fields
  `GarminFluidIntake` and `GarminCaloriesConsumed`, created by the bridge.
  The summary is read once more half a day after the activity (one request
  per activity); `backfill --scope activities --from DATE --intake` fills
  the past from the archive without asking Garmin.

### Fixed

- `health` no longer alerts "weight, bodyFat stopped arriving" after two days
  without stepping on the scale. The dropout check now looks only at finished
  days (the morning's file for today has no weigh-in yet) and only at what the
  device measures by itself, not at what you log or start (weight, food,
  drink, Health Snapshots).

### Documentation

- README: "Use it with an AI coach (MCP)" with the sister project's install
  channels (PyPI via `uvx`, Docker Hub, Claude Desktop bundle, MCP Registry).
- A project icon in the style of the sister project's: the Futureweb Intervals
  MCP's bars and endpoints, here as a bridge deck between two endpoints carried
  by the bars as piers (`docs/assets/icon-bridge*`, shown above the README).

## 0.3.1 — 2026-10-10

Easier diagnosis next to a running container.

### Added

- `--version`.

### Changed

- `gap` takes no lock: it only reads (and caches the two files it compares),
  so it can diagnose an activity next to a running `run` container.

## 0.3.0 — 2026-10-10

The night's values within minutes of the morning watch sync, a quarter of the
Garmin requests, and a mirror that now includes what the watch itself
recorded: the original wellness files of every day, Health Snapshots among
them, which Intervals now shows too.

Upgrading: systemd users add `--activity-interval 120` to the 30-minute
unit's `ExecStart` (the copy in `deploy/systemd/` has it). The first run
takes over the new schedule on its own. To mirror the past once:
`backfill --scope wellness --from 2020-01-01 --wellness-files --pause 3`,
`backfill --scope activities --from 2020-01-01 --archive-only --pause 3`
(adds Garmin's splits) and `snapshot-account --history-from 2020-01-01`.
If Intervals' Garmin settings import SpO2, untick it: the official
integration only ever delivers it from Health Snapshots.

### Changed

- **Wellness is read on evidence, not on a clock.** Last night's values
  (sleep, SpO₂, respiration, sleeping HR, HRV detail, sleep stages, Training
  Readiness, skin temperature) reach Intervals within about ten minutes of
  the morning watch sync instead of up to four hours later. The watch checks
  Intervals' record for today every ten minutes, which costs no Garmin
  request, and reads four Garmin endpoints once the official integration has
  delivered the sleep; Training Readiness gets two more tries if it lags. A
  finished day is read in full once the device has synced after it ended
  (proven by Garmin's last-sync time), so its totals are complete; a day
  without any sync is read anyway at 20:00 the day after. Without the
  official wellness sync, Garmin is checked at a few fixed morning times.
- The scheduled runs ask Garmin far less: no more four-hourly re-reads of
  today and yesterday, no re-read of the day before yesterday, no login on
  runs with nothing to do, and in enrich mode the Garmin activity list is
  scanned every two hours (`sync --activity-interval`, also in `run`); the
  watch enriches new activities in between. About 75 requests on a normal
  day instead of about 310.
- Manual container builds move `latest` only when asked; a release always
  does. The tag of a manual build is validated before it reaches the shell.

### Added

- **The device's original wellness files.** Every finished day's ZIP of the
  watch's own wellness FIT files is mirrored to `wellness-files/<day>.zip`
  with its final read (one request a day): all-day heart rate, respiration
  per minute, stress, SpO₂ readings, overnight HRV, sleep, skin temperature,
  and Health Snapshots, which exist nowhere else. An index next to it lists
  the files (each CRC-checked) and summarises every Health Snapshot (heart
  rate, RMSSD and SDRR, respiration, SpO₂, stress). `backfill --scope
  wellness --wellness-files` fetches the past.
- Garmin's own splits of every activity (typed splits and split summaries)
  in the activity extras; `backfill --archive-only` adds them to older
  archive entries without fetching anything else again.
- **Health Snapshots in Intervals**: the day's first snapshot (heart rate,
  RMSSD, SDRR, respiration, SpO₂, stress) in `GarminSnapshot…` fields with
  a new chart. Where the official integration writes a snapshot into
  resting HR, HRV or SpO₂, the night's value is put back, on the same day
  (one read of today's files when Intervals' HRV moves away from the
  night's); that is the only case besides a filtered file in which the
  bridge replaces a value.
- More of the night: lowest SpO₂, lowest and highest respiration, Body
  Battery change during sleep and Garmin's HRV baseline band, all from the
  archive (the HRV and SpO₂ charts show them).
- The container image is also on Docker Hub,
  `futurewebat/garmin-intervals-bridge`, for NAS container managers that
  only know Docker Hub.
- The account snapshot also keeps goals, gear defaults, training plan
  details, the calendar of scheduled workouts, Garmin's FTP (latest and
  daily history, cycling and running), running tolerance and the activity
  type table; `snapshot-account --history-from DATE` reaches back once.

### Fixed

- The regular enrich and upload runs now archive Garmin's summary, weather,
  gear and exercise sets of every activity they handle. Before, only
  `backfill --archive-only` did, so a mirror built once drifted as new
  activities came in (the recording was kept, the rest was not). Extras that
  Garmin has not attached yet to a fresh activity are asked for again on the
  next run instead of being archived as empty.
- Exercise sets are also fetched when the activity comes from the detail
  endpoint (`activityTypeDTO`), not only from the list.

### Documentation

- README links the sister project, the
  [Futureweb Intervals MCP](https://github.com/futureweb/intervals-mcp-server):
  it lets ChatGPT, Claude and other MCP clients read and analyse every field
  and stream the bridge writes. A table maps the bridge's data to the MCP
  tools that read it.

## 0.2.1 — 2026-10-09

The day Garmin's new partner terms surfaced: Intervals expects to have to
block Garmin-sourced data in its API; files an athlete uploads are expected
to stay accessible. This release makes the bridge's upload mode the
first-class answer and completes the local mirror.

### Added

- **Upload mode, first-class and live-verified.** `run --mode upload` polls
  Garmin every ten minutes and uploads the original of every new activity
  as the athlete's own file; `backfill --scope activities --mode upload`
  does the same for the past; a 10-minute upload timer in `deploy/`.
  Intervals computes every custom field and stream from the complete file
  itself, nothing is flagged as edited.
- A complete local mirror: eight more daily endpoints in the archive
  (all-day heart rate, intraday steps and floors, Body Battery and all-day
  events, four-week load balance, resting HR course, daily training status),
  `--endpoints` takes a list of keys to add to days fetched earlier,
  `backfill --scope activities --archive-only` downloads every original FIT
  plus Garmin's summary, weather, gear and exercise sets without touching
  Intervals, and `snapshot-account` archives profile, settings, devices,
  zones, gear, personal records, badges, workouts and training plans.
- Container image on ghcr.io (amd64 + arm64), built by GitHub Actions on
  every release; compose pulls it.
- Monthly `snapshot-account` timer in `deploy/`.
- README: legal note (your account, your data, Garmin's terms, trademarks),
  how login works in the container, screenshots.

### Changed

- Readiness: the score is the morning's, recovery time and acute load are
  the day's last reading (a hard session shows its recovery on its own day);
  `GarminRecoveryTimeHours` backs the chart; `backfill --rewrite` replaces
  the bridge's own `Garmin…` values from the archive after such a correction.
- VO₂max is written once the day is over, weigh-ins the same day, an
  unlogged hydration day stays empty, floors are whole numbers.
- Weight in the charts as a 7-day average; daily weigh-ins as dots.

### Fixed

- Chained FIT files (two complete files back to back in one Garmin
  original) are accepted and decoded together.
- A 4xx answer from a Garmin endpoint that did not exist for an old date no
  longer counts as an outage; a port number in an error message is no longer
  read as an HTTP status.
- The scheduled sync does the free scope while a backfill holds the other,
  instead of failing (and alerting).

## 0.2.0 — 2026-10-08

First version used in anger. Everything below was verified against a live
Garmin and Intervals.icu account; see `docs/PLAN.md` for the evidence.

### Changed

- **Enrich instead of upload.** The default mode keeps the official Garmin
  import and adds to the imported activity what its filtered file lacks,
  via `PUT /activity/{id}` and `PUT /activity/{id}/streams`. Intervals
  de-duplicates uploads by file hash, so the v0.1 upload created a
  duplicate whenever the official import was on. Upload remains available
  as `--mode upload` for accounts with the import switched off.
- **Field definitions come from the athlete's own custom items**
  (`fit_session_field`, `fit_record_field`, single-field stream scripts,
  first-import conversions). Nothing Garmin-specific is hard-coded.
- **`fitparse` replaced by Garmin's official FIT SDK.** CRC is verified;
  unknown messages and fields keep their numeric IDs with sample values,
  which is where the stripped data lives.
- Custom wellness fields are written as **top-level keys**; v0.1.1 used a
  nested `customFields` object that the API does not have, so every custom
  write would have been ignored.
- Activity matching recognises the official sync's bare Garmin ID, manual
  uploads (`<id>_ACTIVITY.fit`) and the bridge's own `garmin:<id>`.

### Added

- `gap`: original vs. the copy Intervals holds, per activity.
- `enrich`, `watch` (one-minute poll, Garmin only on new activities),
  `backfill` (paced, resumable), `--refresh-own-streams`.
- Per-activity failure isolation with exponential backoff; a Garmin-wide
  block aborts the run without blaming an activity; uploads with unknown
  outcome stay pending.
- Native wellness fields `spO2`, `respiration`, `avgSleepingHR`,
  `floorsClimbed`, `weight`, `bodyFat`, and `kcalConsumed`,
  `carbohydrates`, `protein`, `fatTotal` from Garmin's nutrition log.
  `BRIDGE_WELLNESS_PROFILE` (`recommended` / `all`).
- Per-scope instance locks, so a long wellness backfill does not stall the
  activity watcher.
- Hardened systemd units (`deploy/`), container image smoke-tested with
  Podman, CI with ruff, pip-audit and pytest.

- `setup-charts`: nineteen private fitness charts for the synced values, using
  Intervals' real chart field ids (read from its app bundle), unique item
  indexes (otherwise the chart picker hides them), and completion of the
  bridge's own charts when fields appear later.
- Past days are re-read once after local midnight so finished totals do not
  wait for the throttle; default throttle 4 h.
- The watcher logs in to Garmin lazily: a poll that finds nothing new makes
  no Garmin request at all.

- `health`: daily probe of both services with a configurable stale window
  and alert mail; failure alerts for the timers (`deploy/`).
- `run`: the three timers in one long-running process, for Windows and
  anything without systemd; `.env` file support; file locks on Windows;
  `python -m garmin_intervals_bridge`. Step-by-step Windows guide.

### Fixed after the code review

- A Garmin outage or rate limit at login time was reported as "login
  needed" and, in the watcher, booked against the activity being processed.
  It now ends the run as a Garmin block, and `health` tells the two apart.
- The library's own retries (4 attempts per endpoint) are off; a day whose
  endpoints fail three times in a row aborts the run instead of trying the
  remaining twenty.
- `watch` exits 0 when the sync or a backfill holds the activities lock; it
  used to fail, which fired the alert unit and used up its throttle.
- Dry runs now count as Garmin reads for the throttle, and the first
  `--apply` maps the archived snapshot instead of fetching again.
- Yesterday is re-read once after midnight and then every second refresh
  period, older days once after midnight; refreshes within a day read only
  the measurement endpoints. About 160 instead of 450 wellness requests a day.
- `backfill --scope wellness --from-archive` completes the past from the
  local archive after the mapping gained fields, without Garmin requests.
- An activity that failed once and then succeeded no longer stays "failed"
  in `status` and `health`.
- Intervals `Retry-After` is capped at 30 s; custom items are read once per
  process; Intervals activity ids are validated before they enter a URL.
- Matching: a bare numeric `external_id` counts only for `GARMIN_CONNECT`
  activities; Garmin's timer duration is compared with moving and elapsed
  time; numeric-string select options are understood.
- The alert unit template was committed empty; `deploy/` now ships it with
  the install step for `gib-alert`.
- Streams are only written when a stream both sides have (heart rate, else
  cadence) lines up point by point after the same alignment; a shifted
  origin is caught instead of written.
- Activities enriched earlier are revisited when the athlete's field
  definitions change; the partner copy is re-read when the Intervals
  activity was replaced.
- Today's resting HR is left to tomorrow (Garmin revises it during the
  day); a short activity window costs one Garmin request instead of two;
  the enrich candidate list is fetched with a field list; FIT downloads are
  capped at 32 MB.

### Known limitations

- Writing streams sets `icu_intervals_edited` on the activity; the API
  does not reset it.
- Garmin's endpoints are private; the mobile login path may answer 429.

## 0.1.1 — developer preview

Original-FIT download and archive, upload mode with pending state,
wellness mapping, Docker, synthetic tests. Never live-tested.
