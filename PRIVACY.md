# Privacy

The Garmin → Intervals.icu Bridge is software you run yourself – on your own computer,
NAS or server. There is no central service behind it, and its authors receive none of
your data.

**What it handles:** your Intervals.icu API key (or, where set up, an OAuth token), your
Garmin Connect session tokens (your Garmin password is used once to sign in and never
stored), and your training and health data: activity files, wellness values, gear.

**Where it keeps it:** only in its own folder on your machine (settings file, data
folder, log), readable by you – or by the bridge's own service user – only.

**Where it sends it:** to Garmin Connect and Intervals.icu, to read and write your own
data there. Besides that, only when you set them up: your own mail server and/or an ntfy
server for alerts (a headline, no details), and once a day pypi.org to look for a newer
version (no personal data; `BRIDGE_UPDATE_CHECK=false` turns it off). No analytics, no
telemetry, no third parties.

**Webhooks** (optional, own server only): Intervals.icu tells the bridge that an activity
arrived – its id and source – so it can act at once instead of asking every few minutes.

**Removing it:** stop the bridge and delete its folder; revoke its access in
Intervals.icu (*Settings → Developer Settings*, or the connected app) and, if you like,
sign out its Garmin session in Garmin Connect.

Questions: <https://github.com/futureweb/garmin-intervals-bridge/issues>.
