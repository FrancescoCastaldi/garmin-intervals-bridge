# Running the bridge on Unraid

The bridge comes with an Unraid container template: a form with the API key,
your time zone and the data folder. The template is new; if something in
these steps reads differently on your server, say so in the
[forum thread](https://forum.intervals.icu/t/garmin-intervals-bridge-your-complete-garmin-fit-files-and-wellness-data-in-intervals-icu-with-or-without-the-official-garmin-integration/132863)
or in an [issue](https://github.com/futureweb/garmin-intervals-bridge/issues).

## 1. The template and the data folder

Open Unraid's terminal (the `>_` icon top right) and paste:

```bash
wget -O /boot/config/plugins/dockerMan/templates-user/my-garmin-intervals-bridge.xml https://raw.githubusercontent.com/futureweb/garmin-intervals-bridge/main/deploy/unraid/garmin-intervals-bridge.xml
mkdir -p /mnt/user/appdata/garmin-intervals-bridge && chown 10001:10001 /mnt/user/appdata/garmin-intervals-bridge && chmod 700 /mnt/user/appdata/garmin-intervals-bridge
```

The first line adds the template; the second makes the data folder and
hands it to the user the bridge runs as inside the container (id 10001).
The folder will hold the Garmin session, the original FIT files and the
daily archive – keep it private.

## 2. The container

*Docker → Add Container → Template:* **garmin-intervals-bridge** (under
*User templates*). Fill in:

- **Intervals.icu API key** – Intervals.icu → *Settings → Developer Settings
  → API key*
- **Time zone** – yours, e.g. `Europe/Vienna`: it decides which date last
  night's sleep belongs to
- **Web page password** – your own, 8 characters or more
- optional: **Push messages** – install the free app *ntfy*, subscribe to a
  topic name only you know and enter `https://ntfy.sh/<that name>`: the
  bridge tells you when Garmin wants a new sign-in or something has been
  failing for a day
- optional, under *Show more settings*: the same by e-mail (address and the
  password to send from it), ImReady4's training advice, the experimental
  trend advice

*Apply*. The container starts as a **dry run**: it shows what it would do
without writing anything.

## 3. Sign in to Garmin once

Click the container's icon → *WebUI*, sign in with your web page password,
then **Garmin** → your Garmin Connect e-mail and password, and the code if
Garmin sends one. The password is used once and not stored; the session
renews itself for about a year, and the bridge starts working within a
minute. If Garmin answers "429", wait an hour – Garmin rate-limits
sign-ins. (Without the web page: icon → *Console*,
`garmin-intervals-bridge login`.)

## 4. Let it write

The container's log (icon → *Logs*) lists what the dry run would enrich and
write. When that looks right: icon → *Edit*, switch to the *Advanced View*
(top right), change **Post Arguments** from `run` to `run --apply`, *Apply*.

From now on it polls Intervals every minute, does a full run every 30
minutes and checks itself once a day; Unraid restarts it with the array.

## Gear, the past, updating

The container's console takes every command of the README, for example
`garmin-intervals-bridge gear` (the gear plan) and the backfill commands of
[SYNOLOGY.md](SYNOLOGY.md#8-the-past-optional-once) (there with a second
container; on Unraid the same works with the template's settings and other
Post Arguments). Updating: *Docker → Check for Updates*, then *apply update*.

## If something goes wrong

- *"The data folder /data is not writable"*: step 1's second line again.
- *"Garmin login needed"* in the log, or a note "Garmin bridge: Garmin login
  needed" in your Intervals calendar: step 3 again.
- Times or dates a few hours off: the time zone is missing or wrong.
