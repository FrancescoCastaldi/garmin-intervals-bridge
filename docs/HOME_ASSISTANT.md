# Running the bridge as a Home Assistant app

Home Assistant runs the bridge as an app (formerly called add-on): settings in a form, the
web page – with the Garmin sign-in – right in the sidebar, no terminal, no extra password.
For Home Assistant OS and Supervised installations, on 64-bit Intel/AMD and ARM.

## Install

1. Add the repository – in one click:
   [add it to my Home Assistant](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Ffutureweb%2Fgarmin-intervals-bridge),
   or by hand: *Settings → Apps* (older versions: *Add-ons*) → *App store*, the **⋮** menu
   top right → **Repositories**, add `https://github.com/futureweb/garmin-intervals-bridge`.
2. Reload the store; **Garmin Intervals Bridge** appears at the bottom. Open it, *Install*.
3. Follow its **Documentation** tab: API key and mode in *Configuration*, start, sign in to
   Garmin in the sidebar, look at the log, then switch *Write to Intervals* on.

The app's documentation: [home-assistant/DOCS.md](../home-assistant/DOCS.md).

## Coming from a NAS or another computer

Stop the bridge there, copy its data folder to Home Assistant's `share/garmin-bridge` (the
*Samba share* app shows `share`), then start the app: it takes the folder over on its first
start – Garmin session, archive, gear choices – and carries on.

## Updating

Home Assistant shows a new version of the app like any other update.

This app is new: if something reads differently on your Home Assistant, say so in the
[forum thread](https://forum.intervals.icu/t/132863) or an
[issue](https://github.com/futureweb/garmin-intervals-bridge/issues).
