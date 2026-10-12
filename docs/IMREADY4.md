# ImReady4 training advice, without the morning MATLAB step

[ImReady4](https://forum.intervals.icu/t/how-to-guide-imready4-app-for-hrv-guided-training/25778)
is a script for HRV-guided training written by **@Inigo_Tolosa**, built on the
method and chart **@MedTechCD** reconstructed from the ithlete training guide
(with input from the work of Alan Couzens, Marco Altini, Andrew Flatt and
Daniel Rowland). Every morning it compares last night's HRV and resting heart
rate with your last 30 days and writes one of four answers into the
Intervals.icu wellness field `TrainingAdvice`: **Rest**, **LIT**, **Planned** or
**HIT**. Its users run it by hand in MATLAB Mobile each morning.

The bridge can do that step for you. It already notices when last night's
values have arrived in Intervals; with the feature switched on, it calculates
the same advice right then and writes it into the same field, so your
`TrainingAdvice` chart keeps working and nothing has to be started by hand. It
needs no MATLAB, no extra service and no Garmin request: it reads Intervals
only.

It is **off by default**.

## What it calculates

An independent implementation of ImReady4's published scoring (version 4.43):

1. **Inputs:** the wellness fields `hrv` (rMSSD in ms) and `restingHR` (bpm) –
   the same fields ImReady4 reads – of the wellness records of the 90 days up
   to the day, newest first.
2. **HRV** is put on ImReady4's scale, 20·ln(rMSSD); resting HR stays as it is.
3. **Reference:** the 30 records ending at the day, the day itself included.
   Days without a value are left out; mean and *population* standard deviation
   (MATLAB's `std(x, 1)`).
4. **Z-scores:** (value − mean) / standard deviation, once for HRV, once for
   resting HR.
5. **Zones**, exactly as ImReady4 draws them:

| HRV z | resting HR z | ImReady4 says | `TrainingAdvice` |
| --- | --- | --- | --- |
| above +1 | above −1, up to +1 | HIT – ready for intensive training | 4 HIT |
| from −1 up to 0 (not reached) | −2 or lower | LIT – low intensity | 2 LIT |
| 0 or above | −2 or lower | LIT! – acute fatigue signs | 2 LIT |
| −1 or above | below +1.7 (otherwise) | Normal – train as planned | 3 Planned |
| −1 or above | +1.7 or above | LIT – low intensity | 2 LIT |
| below −1 | −2 or lower | Rest – time to recover | 1 Rest |
| below −1 | above −2, up to +1.7 | LIT – recovery not complete | 2 LIT |
| below −1 | above +1.7 | REST! – illness or stress | 1 Rest |

The rows are checked in ImReady4's order; the first that fits wins.

It does **not** draw ImReady4's charts, use SDNN (ImReady4 only plots it), look
at the spread of the last seven days, or forecast tomorrow (the Python port in
the forum adds a VARMAX forecast; that part is not ImReady4's scoring).

## Where it differs from the script on purpose

- **No advice instead of a guess.** A day without its own HRV *and* resting HR,
  with fewer than 14 measured days in its 30-day reference, or with no
  variability at all gets **no** advice, and nothing is written. (The script
  writes an empty value then, and with very little history it still scores.)
- **Zero is not a measurement.** A zero or negative HRV or resting HR counts as
  missing; gaps are never filled with zeros.
- **Your values stay.** A `TrainingAdvice` the day already has – typed by you or
  written by the script – is never replaced unless you say so (`--overwrite`).
  Locked days are never written.

## Inputs, and what "HRV" means here

The bridge reads whatever your `hrv` and `restingHR` fields hold, exactly as
ImReady4 does. With Garmin's official integration that is Garmin's overnight
HRV (the night's average rMSSD) and Garmin's resting heart rate. ImReady4 was
designed around a morning measurement; many users feed it Garmin's night
values, and what matters for z-scores is that the source stays the same day
after day. The advice is calculated once the night is in Intervals, which is
when ImReady4 users run the script.

Garmin keeps updating a day's resting heart rate during the day, and a Health
Snapshot used to overwrite the night's HRV and resting HR (the bridge now puts
the night's values back, see the README). Both can make an advice calculated
later differ from the one of the morning.

## Turning it on

1. The `TrainingAdvice` field must exist in Intervals as ImReady4's guide
   describes: a wellness field of type *select* with the values 1 Rest, 2 LIT,
   3 Planned, 4 HIT. The bridge finds it by its code, checks those four values
   and never creates, changes or duplicates it (or its charts).
2. Set `BRIDGE_IMREADY4_ENABLED=true`:
   - systemd: add it to `/etc/garmin-intervals-bridge/env`;
   - a container: `-e BRIDGE_IMREADY4_ENABLED=true` (and `BRIDGE_TIMEZONE`);
   - Windows / Mac: add the line to the `.env` file in your bridge folder.
3. Check what it would write today, then restart the bridge (or let the next
   scheduled run pick it up):

```bash
garmin-intervals-bridge imready4
```

The scheduled runs only write when they run with `--apply`, as for everything
else the bridge writes.

## Every morning

Once the bridge has read last night's values (within about ten minutes of your
watch's morning sync), it:

1. reads the 90 days of `hrv`, `restingHR` and `TrainingAdvice` from Intervals
   (one request), after any Health Snapshot correction of the same run;
2. waits if today's HRV or resting HR is not in Intervals yet, and tries again
   after 10, 30, 60, 120 and then every 240 minutes; with no complete night by
   22:00 there is no advice that day;
3. calculates the advice and writes it if `TrainingAdvice` is still empty and
   the day is not locked – that field only, nothing else of the day;
4. reads the field back to make sure it holds the value, and records the day
   as done, so repeated runs write nothing more.

The log says what happened, for example:

```
INFO ImReady4 advice for 2026-10-12: Planned (Normal – go on, train as planned; HRV z +0.81, resting HR z -0.65)
INFO ImReady4 advice for 2026-10-12: Planned computed, the field already holds 2 - kept
INFO ImReady4 advice for 2026-10-12 waits: no restingHR for the day yet
```

## The command: preview, compare, fill the past

`imready4` uses Intervals only and works next to a running bridge
(`docker exec <container> garmin-intervals-bridge imready4 …` in a container).
It is a dry run unless `--apply` is given.

```bash
garmin-intervals-bridge imready4                                  # today: advice, z-scores, reference, what it would do
garmin-intervals-bridge imready4 --date 2026-10-08                # one day
garmin-intervals-bridge imready4 --from 2026-01-01 --compare      # against the values the field already holds
garmin-intervals-bridge imready4 --from 2026-10-01 --apply        # fill the days that have no advice yet
garmin-intervals-bridge imready4 --from 2026-10-01 --apply --overwrite   # replace existing values too (careful)
```

`--compare` lists the agreement rate and every day where the field holds a
different value, with both z-scores and how far they were from the nearest
zone border. Without `--compare`, every day is listed with its advice, its
z-scores, its reference (number of measured days, typical HRV, resting HR mean
and SD) and the action: `would write`, `written`, `same`, `keep` (a different
value is there), `locked`, `wait` or `none` (no advice, with the reason).

## How close is it to ImReady4?

- **Reference results:** the .NET translation of the script
  ([Creastoff/ImReady4.Net](https://github.com/Creastoff/ImReady4.Net)) ships
  30 days of advice its author produced with the original script; this
  implementation gives the same advice on **30 of 30** days.
- **A real account:** recalculated for every day on which the author of the
  bridge had run ImReady4 since May 2023, the advice agrees on **1,196 of
  1,225 days (97.6 %)**. 22 of the 29 other days have a Health Snapshot in
  their 30-day window, whose values the bridge has since put back to the
  night's – the script saw different inputs then. The remaining 7 lie between
  0.06 and 0.79 standard deviations from a zone border, as when a resting HR
  changes after the morning.

No MATLAB code is copied; the scoring was written from the script's published
logic, with the author's permission for an open implementation.

## Troubleshooting

- *"Intervals has no 'TrainingAdvice' wellness field"*: create it as described
  above; the bridge does not create it.
- *"'TrainingAdvice' does not offer 1 Rest, 2 LIT, 3 Planned, 4 HIT"*: the field
  exists with other values; nothing is written until it matches.
- *"not enough history"*: fewer than 14 of the last 30 days have both values;
  the advice starts once there are enough.
- *"waits: no hrv / restingHR for the day yet"*: the watch has not delivered
  that value yet; it is tried again later the same day.
- *A different value than the script would give for a past day*: run
  `imready4 --date DAY` to see the z-scores and reference; data that changed
  later (resting HR, a corrected snapshot) is the usual reason.

## Limitations and training safety

The advice is a statistical comparison with your own recent past, not a
diagnosis. It knows nothing about how you feel, about illness, or about your
plan. ImReady4's zones come from coaching practice, not from a validated
medical model, and a single day close to a zone border means little – look at
the trend, as MedTechCD recommends. A consistent measurement (the same device,
the same kind of night) matters more than any threshold.
