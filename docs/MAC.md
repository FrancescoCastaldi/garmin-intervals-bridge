# Running the bridge on a Mac

No server, no Docker, no programming. About ten minutes. Everything happens
in one Terminal window; the commands are copy-and-paste.

## 1. Install Python

Download Python 3.12 or newer from <https://www.python.org/downloads/macos/>
and run the installer. When it is done, Finder shows the new Python folder:
double-click **Install Certificates.command** there once.

Open Terminal (`Cmd`+`Space`, type `Terminal`, Enter) and check:

```bash
python3 --version
```

It should say 3.12 or newer. If it says 3.9, or macOS offers to install
developer tools, Python from python.org is not installed yet (macOS's own
`python3` is too old); install it, then open a new Terminal window.

## 2. A folder for the bridge

```bash
mkdir -p ~/GarminBridge && cd ~/GarminBridge
python3 -m venv venv
venv/bin/pip install garmin-intervals-bridge
```

This puts the bridge and everything it needs into `GarminBridge` in your
home folder; nothing else on the Mac is changed. Check that it worked:

```bash
venv/bin/garmin-intervals-bridge --help
```

All commands below are run in this folder. In a new Terminal window, start
with `cd ~/GarminBridge`. Leave the folder where it is: macOS does not let
background jobs into Desktop, Documents or Downloads, so the automatic start
in step 6 would fail there.

## 3. Your API key

```bash
touch .env && chmod 600 .env && open -e .env
```

TextEdit opens an empty file. Put one line into it, save (`Cmd`+`S`) and
close it:

```
INTERVALS_API_KEY=paste-your-key-here
```

Your key is in Intervals.icu under *Settings → Developer Settings → API
key*. Nothing else is needed; the athlete id is taken from the key.

The folder `~/GarminBridge/data` will hold the Garmin session, the original
FIT files and the daily archive. Keep it private, it is yours only.

## 4. Sign in to Garmin once

```bash
venv/bin/garmin-intervals-bridge login
```

Enter your Garmin Connect e-mail, password and, if asked, the MFA code.
Terminal shows nothing while you type the password; that is normal. The
password is used once and not stored; the session tokens are saved in
`~/GarminBridge/data/tokens` and refresh themselves for about a year. If
Garmin answers with "429" or "too many requests", wait an hour and try
again — Garmin rate-limits logins.

## 5. See what it would do, then let it do it

```bash
venv/bin/garmin-intervals-bridge sync
```

This is a dry run: it lists the activities it would enrich and the wellness
values it would write, without changing anything. When that looks right:

```bash
venv/bin/garmin-intervals-bridge sync --apply
```

## 6. Keep it running

```bash
venv/bin/garmin-intervals-bridge run --apply
```

Leave the window open: it polls Intervals every minute, does a full run every
30 minutes and writes a short health line once a day. `Ctrl`+`C` stops it.

To have it run in the background instead, started whenever you log in to the
Mac, stop the window's bridge with `Ctrl`+`C` and paste this block once. It
writes a small launch file and starts it:

```bash
mkdir -p ~/Library/LaunchAgents
cat > ~/Library/LaunchAgents/at.futureweb.garmin-intervals-bridge.plist <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>at.futureweb.garmin-intervals-bridge</string>
  <key>ProgramArguments</key>
  <array>
    <string>$HOME/GarminBridge/venv/bin/garmin-intervals-bridge</string>
    <string>run</string>
    <string>--apply</string>
  </array>
  <key>WorkingDirectory</key><string>$HOME/GarminBridge</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>300</integer>
  <key>StandardOutPath</key><string>$HOME/GarminBridge/bridge.log</string>
  <key>StandardErrorPath</key><string>$HOME/GarminBridge/bridge.log</string>
</dict>
</plist>
EOF
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/at.futureweb.garmin-intervals-bridge.plist
```

macOS then notifies you that a background item was added: that is the
bridge. It is listed under *System Settings → General → Login Items*, where
it can be switched off. What it does goes to `~/GarminBridge/bridge.log`
(watch it with `tail -f ~/GarminBridge/bridge.log`, `Ctrl`+`C` ends watching,
not the bridge). If it ever stops on an error, it is started again after five
minutes.

To remove the automatic start:

```bash
launchctl bootout gui/$(id -u)/at.futureweb.garmin-intervals-bridge
rm ~/Library/LaunchAgents/at.futureweb.garmin-intervals-bridge.plist
```

The bridge runs while the Mac is awake. A MacBook with the lid closed
catches up as soon as it wakes: last night's values arrive a few minutes
after you open it, if the watch has synced. For a bridge that never sleeps,
a Mac mini, a NAS or a small Linux box with the container image is the
better home.

## 7. The past, and the charts

Backfill last year's wellness (one run, takes a while, polite to Garmin):

```bash
venv/bin/garmin-intervals-bridge backfill --scope wellness --from 2026-01-01 --apply
```

Activities from the past: `backfill --scope activities --from 2026-09-01 --apply`.

Charts: in Intervals.icu open the *Fitness* page, a tab, *custom charts*,
search **Garmin Bridge** and tick what you like.

## Updating

```bash
cd ~/GarminBridge
venv/bin/pip install --upgrade garmin-intervals-bridge
launchctl kickstart -k gui/$(id -u)/at.futureweb.garmin-intervals-bridge
```

The last line restarts the background bridge; leave it out if you run it in
a window (stop it with `Ctrl`+`C` and start it again instead).

## If something goes wrong

- *"command not found"* or *"no such file"*: you are not in the bridge's
  folder; `cd ~/GarminBridge` first.
- *"Garmin login needed"*: run `venv/bin/garmin-intervals-bridge login`
  again, then restart the background bridge with the `launchctl kickstart`
  line from *Updating*.
- *"Another bridge instance is running"*: the background bridge and a
  Terminal window are both running `run`; stop the one in the window.
- Everything the bridge did is in the window or in `bridge.log`; nothing is
  deleted on either side, so a mistake is at most a missing value.
- Questions and reports: <https://github.com/futureweb/garmin-intervals-bridge/issues>.

Written for macOS 13 Ventura or newer, on Apple silicon and Intel Macs.
Already using Docker Desktop? The container image from the
[README](../README.md#running-it) (*Docker / Podman*) runs on a Mac as well.
