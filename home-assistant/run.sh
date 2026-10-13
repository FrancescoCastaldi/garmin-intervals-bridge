#!/bin/sh
# Home Assistant app: the bridge with the settings of the app's form. Writes to Intervals only
# once "Write to Intervals" is switched on; until then a dry run that shows what it would do.
set -e
# Moving in from a NAS or another computer: its data folder, copied to Home Assistant's
# share/garmin-bridge (Samba share), is taken over once – never over existing data.
if [ ! -e /data/bridge.db ] && [ -e /share/garmin-bridge/bridge.db ]; then
    echo "Taking over the data folder from /share/garmin-bridge (Garmin session, archive)"
    cp -a /share/garmin-bridge/. /data/
fi
export BRIDGE_DATA_DIR=/data GARMIN_TOKEN_DIR=/data/tokens BRIDGE_OPTIONS_FILE=/data/options.json
export BRIDGE_UI_PORT=8099 BRIDGE_UI_HOST=0.0.0.0 BRIDGE_UI_INGRESS=1
APPLY=$(python3 -c 'import json; print("--apply" if json.load(open("/data/options.json")).get("write_to_intervals") else "")')
exec garmin-intervals-bridge run $APPLY
