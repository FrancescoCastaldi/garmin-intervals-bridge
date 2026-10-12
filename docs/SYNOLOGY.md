# Running the bridge on a Synology NAS (Container Manager)

A NAS is a good home for the bridge: it is always on, so last night's values
arrive minutes after your morning sync even when your computer sleeps. This
guide follows **@DarKen's** write-up from the
[forum thread](https://forum.intervals.icu/t/garmin-intervals-bridge-your-complete-garmin-fit-files-and-wellness-data-in-intervals-icu-with-or-without-the-official-garmin-integration/132863/23) –
thanks! Menu names are those of Container Manager (DSM 7.2); the older Docker
app calls some of them slightly differently.

## 1. The image

*Container Manager → Registry*, search **`futurewebat/garmin-intervals-bridge`**
and download it (tag `latest`, or a version such as `0.6.0`). The image is built
for 64-bit models (Intel/AMD and ARM64); older 32-bit ARM models cannot run it.

## 2. A folder for the bridge's data

Create a shared folder or a sub-folder, e.g. `docker/garmin-bridge`. It will
hold the Garmin session, the original FIT files and the daily archive – keep it
private. The bridge runs as user id 10001 inside the container; if its log later
says *permission denied* for `/data`, give the folder read/write access for
everyone (or, over SSH, `sudo chown 10001:10001 /volume1/docker/garmin-bridge`).

## 3. The container

*Container → Create*, choose the image, then in the advanced settings:

- **Execution command** (*Command*): `run` – a dry run first; it shows what it
  would do without writing (step 6 switches it to `run --apply`).
- **Environment variables:**
  - `INTERVALS_API_KEY` = your key (Intervals.icu → *Settings → Developer
    Settings → API key*)
  - `BRIDGE_TIMEZONE` = your time zone, e.g. `Europe/Dublin` – a container has
    none of its own, and the zone decides which date last night's sleep
    belongs to
  - optional: `BRIDGE_IMREADY4_ENABLED=true` (ImReady4's training advice, see
    [IMREADY4.md](IMREADY4.md)), `BRIDGE_HRVTREND_ENABLED=true` (the
    experimental [trend advice](HRV_TREND.md))
- **Volume settings:** your folder from step 2 → mount path **`/data`**
  (read/write).
- **Auto-restart:** on.

Start it. Its *Log* begins with `garmin-intervals-bridge <version> running …`
and the day boundary; then it reports that a Garmin login is needed and pauses.

## 4. Sign in to Garmin once

Open the container → *Details* (or *Action → Open terminal*) → **Terminal** →
*Create* (a shell opens inside the running container) and type:

```bash
garmin-intervals-bridge login
```

Enter your Garmin Connect e-mail, password and, if asked, the MFA code. The
password is used once and not stored; the session tokens land in your data
folder and refresh themselves for about a year. The running bridge picks them
up on its own within 15 minutes (or restart the container). No second
container is needed for this. If Garmin answers "429" or "too many requests",
wait an hour – Garmin rate-limits logins.

## 5. See what it would do

The *Log* now lists what the dry run would enrich and write. The same terminal
also answers questions while the bridge runs:

```bash
garmin-intervals-bridge --version
garmin-intervals-bridge status
garmin-intervals-bridge gap --activity-id <Garmin activity id>
```

## 6. Let it write

Stop the container, change its execution command to **`run --apply`**, start
it again. From now on it polls Intervals every minute, does a full run every 30
minutes and writes a health line once a day.

## 7. Your gear (optional)

In the container's Terminal:

```bash
garmin-intervals-bridge gear                                        # the plan
garmin-intervals-bridge gear --create --include-retired --apply     # create your Garmin gear in Intervals, fill it in
```

`--include-retired` adds your retired shoes too (retired, with their dates). No
restart needed: the running bridge keeps new activities in line. Details:
[README – Gear](../README.md#gear).

## 8. The past (optional, once)

The bridge knows the gear and the original files of the activities it has
archived – those since you started it. To mirror your history once, the live
container has to be stopped (both would use the same data):

1. Stop the live container.
2. Create a second container from the same image with the **same data folder
   and variables**, execution command
   `backfill --scope activities --from 2025-01-01 --archive-only --pause 2`,
   auto-restart **off**. Start it and let it finish (19 months took about
   90 minutes on DarKen's NAS); it stops by itself.
3. Delete that second container and start the live one again.
4. In the live container's Terminal: `garmin-intervals-bridge gear --create --include-retired --apply`.

Wellness history works the same way with
`backfill --scope wellness --from 2025-01-01 --pause 3 --apply`.

## Updating

*Registry* → download the new tag; then stop the container and rebuild it
with the new image (*Action → Reset* / recreate with the same settings). Tip
from DarKen: once everything works, **export the container's settings**
(*Action → Export*, settings only) – recreating it after an update is then a
matter of importing them. The first log line shows which version runs.

## If something goes wrong

- *"Garmin login needed"* in the log: step 4 again.
- *"Another bridge instance is running"*: two containers use the same data
  folder; stop one.
- *Permission denied* for `/data`: see step 2.
- Times or dates a few hours off: `BRIDGE_TIMEZONE` is missing (the log's
  second line says which zone it uses).
- Questions and reports: the forum thread above, or
  <https://github.com/futureweb/garmin-intervals-bridge/issues>.
