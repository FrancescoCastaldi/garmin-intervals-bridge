# Running the bridge on Windows

No server, no Docker, no programming. About ten minutes. Everything happens
in one PowerShell window; the commands are copy-and-paste.

## 1. Install Python

Download Python 3.12 or newer from <https://www.python.org/downloads/windows/>
and run the installer. **Tick "Add python.exe to PATH"** on the first screen,
then *Install Now*.

## 2. Install the bridge

Open PowerShell (press the Windows key, type `PowerShell`, Enter) and paste:

```powershell
py -m pip install garmin-intervals-bridge
```

Check that it worked:

```powershell
garmin-intervals-bridge --help
```

If Windows says the command is unknown, close PowerShell, open it again and
try once more (the PATH change from step 1 needs a new window). If it still
does not work, use `py -m garmin_intervals_bridge` instead of
`garmin-intervals-bridge` in every command below.

## 3. A folder for the bridge and your API key

```powershell
mkdir C:\GarminBridge
cd C:\GarminBridge
notepad .env
```

Notepad asks whether to create the file: *Yes*. Put one line into it and
save:

```
INTERVALS_API_KEY=paste-your-key-here
```

Your key is in Intervals.icu under *Settings → Developer Settings → API
key*. Nothing else is needed; the athlete id is taken from the key.

The folder `C:\GarminBridge\data` will hold the Garmin session, the
original FIT files and the daily archive. Keep it private, it is yours only.

If you use ImReady4 for HRV-guided training, the bridge can write its daily
advice for you: add the line `BRIDGE_IMREADY4_ENABLED=true` to the same file
(see [IMREADY4.md](IMREADY4.md)).

## 4. Sign in to Garmin once

```powershell
garmin-intervals-bridge login
```

Enter your Garmin Connect e-mail, password and, if asked, the MFA code.
The password is used once and not stored; the session tokens are saved in
`C:\GarminBridge\data\tokens` and refresh themselves for about a year. If
Garmin answers with "429" or "too many requests", wait an hour and try
again — Garmin rate-limits logins.

## 5. See what it would do, then let it do it

```powershell
garmin-intervals-bridge sync
```

This is a dry run: it lists the activities it would enrich and the wellness
values it would write, without changing anything. When that looks right:

```powershell
garmin-intervals-bridge sync --apply
```

## 6. Keep it running

```powershell
garmin-intervals-bridge run --apply
```

Leave the window open: it polls Intervals every minute, does a full run every
30 minutes and writes a short health line once a day. `Ctrl+C` stops it.

To start it automatically whenever you log in to Windows, create a file
`C:\GarminBridge\run-bridge.cmd` with these two lines

```
@cd /d C:\GarminBridge
@garmin-intervals-bridge run --apply
```

and register it once (PowerShell, copy as one line):

```powershell
schtasks /Create /SC ONLOGON /TN "Garmin Intervals Bridge" /TR "C:\GarminBridge\run-bridge.cmd"
```

A console window will appear at every logon; minimise it. (*Task Scheduler*
in the Start menu shows and removes the task.)

## 7. The past, and the charts

Backfill last year's wellness (one run, takes a while, polite to Garmin):

```powershell
garmin-intervals-bridge backfill --scope wellness --from 2026-01-01 --apply
```

Activities from the past: `backfill --scope activities --from 2026-09-01 --apply`.

Charts: in Intervals.icu open the *Fitness* page, a tab, *custom charts*,
search **Garmin Bridge** and tick what you like.

## Updating

```powershell
py -m pip install --upgrade garmin-intervals-bridge
```

## If something goes wrong

- *"Garmin login needed"*: run `garmin-intervals-bridge login` again.
- *"ZoneInfoNotFoundError: No time zone found"*: an older version; update it
  (`py -m pip install --upgrade garmin-intervals-bridge`, see *Updating*).
- *"Another bridge instance is running"*: a second window is already running
  `run`; close one.
- Everything the bridge did is in the window; nothing is deleted on either
  side, so a mistake is at most a missing value.
- Questions and reports: <https://github.com/futureweb/garmin-intervals-bridge/issues>.

Written for Windows 11 and PowerShell; the same steps work in `cmd.exe`.
