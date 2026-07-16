# TikTok interaction runbook

## Current task batch

- Source: `tiktok_comments_7440036951958244609_2026-04-15T08-03-55-614Z.csv`
- Unique targets: 326
- Duplicate comment authors skipped: 46
- Database: `tikpoc.db`
- Dashboard: `http://127.0.0.1:8766`

## Rule

A target is eligible only when both conditions are true:

- Following is greater than Followers.
- The profile has more than three videos.

For an eligible target, the worker chooses one visible video randomly, then
chooses exactly one outcome: Like, Favorite, Share, or trace-only. The current
weights are 25% each. If the selected action has exhausted its UTC hourly
quota, the result degrades to trace-only instead of selecting another action.

## Start the worker

The approved limits are 100 likes, 14 favorites, and 25 shares per UTC hour.

```bash
UV_CACHE_DIR=.uv-cache uv run tikpoc run \
  --db tikpoc.db \
  --udid emulator-5554 \
  --appium-url http://127.0.0.1:4723 \
  --like-probability 0.25 \
  --like-hourly-limit 100 \
  --favorite-probability 0.25 \
  --favorite-hourly-limit 14 \
  --share-probability 0.25 \
  --share-hourly-limit 25 \
  --trace-probability 0.25
```

The worker stops when no pending or retryable tasks remain. Use the dashboard
to pause, resume, or stop it. Quota reservations are stored in SQLite, so a
process restart does not reset the current hourly limits.

## Inbox status

The Android notification bridge detects TikTok follower and direct-message
notifications without opening Inbox. It recognizes message notifications by
Android category and common English, Simplified Chinese, Traditional Chinese,
Spanish, French, German, Italian, Portuguese, Russian, Japanese, and Korean
notification text. Failed HTTP deliveries are retried and then kept in a local
device queue until the notification listener reconnects or another event arrives.

The Inbox deep link is verified on TikTok 46.0.3. The current account still has
no real DM thread or new-follower row available for final selector calibration.
Detection is therefore event-driven, but follow-back and reply execution still
need one live sample of each page before they can be called production-verified.
`AiReplyClient` is wired to `TKAUTO_LLM_*` or `MODEL_MONITOR_LLM_*`
OpenAI-compatible environment variables and falls back to a fixed reply when the
model endpoint is unavailable.

## Event-driven multi-device mode

Importing with repeated device IDs creates one assignment for every target-device
pair. Seven devices therefore produce seven independently recoverable tasks per
CSV target.

```bash
uv run tikpoc import targets.csv --db tikpoc.db \
  --device-id phone-01 --device-id phone-02 --device-id phone-03 \
  --device-id phone-04 --device-id phone-05 --device-id phone-06 \
  --device-id phone-07
```

Run the event API on an address reachable by the cloud phones:

```bash
uv run tikpoc dashboard --db tikpoc.db --host 0.0.0.0 --port 8766
```

Install and configure `android-event-bridge/build/event-bridge.apk` on every
device. Replace the endpoint and device ID per phone:

```bash
adb install -r android-event-bridge/build/event-bridge.apk
adb shell cmd notification allow_listener \
  com.tikpoc.bridge/.TikTokNotificationService
adb shell am broadcast -a com.tikpoc.bridge.CONFIG \
  -n com.tikpoc.bridge/.ConfigReceiver \
  --es endpoint http://CONTROLLER_IP:8766/api/device-events \
  --es device_id phone-01
```

Start one Worker per device with the matching `--device-id` and
`--event-driven`. The Worker consumes pushed follower/DM events only between
atomic UI actions; it does not poll Inbox. Event-driven workers remain resident
when the CSV queue is temporarily empty. A failed event is retried up to three
times, and an event left in `running` state by a process interruption is returned
to `retry_wait` at startup.

## Daily 10,000-target capacity

The requirement is 10,000 unique targets per day with all seven device accounts
touching every target. That is 70,000 device visits per day and requires each
device to sustain:

- 416.67 targets/hour and at most 8.64 seconds/target when running 24 hours.
- 500 targets/hour and at most 7.20 seconds/target when running 20 hours.

The July 11 emulator sample has an active average of 10.46 seconds/target
(`p50=4s`, `p90=28s`). It projects to about 8,260 targets/device/day before
downtime, so the existing UI-only metric-reading path does not meet the target.

`TKAuto/Tiktok-Auto/xlsx/merged_followers_all.xlsx` contains 10,198 unique user
IDs with follower, following, video, private-account, profile URL, and `secUid`
fields. The scale path is to normalize and deduplicate that collector output once,
evaluate the rule centrally, then let all seven devices perform only the shortest
profile visit and the preplanned eligible interaction. The devices must not each
re-read the same profile metrics. The measured target for this optimized path is
an average below 6.5 seconds/target, leaving operational headroom.

The importer accepts that workbook format directly:

```bash
uv run tikpoc validate TKAuto/Tiktok-Auto/xlsx/merged_followers_all.xlsx

uv run tikpoc import TKAuto/Tiktok-Auto/xlsx/merged_followers_all.xlsx \
  --db capacity.db \
  --device-id phone-01 --device-id phone-02 --device-id phone-03 \
  --device-id phone-04 --device-id phone-05 --device-id phone-06 \
  --device-id phone-07
```

The July 15 capacity import produced 10,196 navigable unique targets, 71,372
device tasks, and complete seven-device assignment for every target in 17.59
seconds. The source workbook was collected on March 1 and must be refreshed
before live use: a smoke target had already changed its handle, while the old
handle had been reassigned to another account. A profile identity mismatch is a
failure and must never be counted as successful reach.

The current Appium prescreened smoke still took 29.77 seconds from cold start.
Appium is therefore the calibration and fallback backend, not the 10,000/day
production backend. The production path must use the TKAuto ADB/MYT RPC adapter,
coordinate/XML route assertions, a warm TikTok process, and a measured per-target
budget. Promotion requires a sustained average below 6.5 seconds and p90 below
8.64 seconds on every one of the seven devices.
