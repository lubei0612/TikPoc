# Six-Device Throughput Recovery Design

## Goal

Restore the paused six-device acquisition round from roughly 110-162 confirmed
visits per device-hour to at least 400 per device-hour without weakening visible
profile identity, action verification, rolling quotas, leases, or durable resume.
The stretch target remains 500 or more per device-hour.

## Measured Root Cause

The paused round completed 7,781 of 98,304 assignments. MYT measurements showed:

- `adb shell am start -W` took 4.4-10.7 seconds on average per device, while
  fire-and-verify dispatch took 0.18-0.26 seconds;
- one Appium page source or targeted query took 0.4-9 seconds depending on the
  MYT slot and visible screen;
- separate Appium server processes did not materially reduce that latency;
- compressed UiAutomator2 hierarchy retained the required profile identity,
  statistics, and structured parsing nodes on all six profile screens while
  reducing a full page source to 0.81-1.26 seconds;
- one metrics-incomplete profile retried 48 times after its visible visit was
  already confirmed.

The dominant costs are duplicated device-side hierarchy generation and an ADB
route wait that is redundant with the following visible identity gate.

## Runtime Design

ADB profile routing dispatches the intent without `-W`. Dispatch success is not
visit success: the existing Appium identity and profile-surface checks remain
mandatory before `visit_confirmed_at_ms` is written.

Every UiAutomator2 session enables compressed hierarchy in addition to
`waitForIdleTimeout=0`. Profile readiness reads one complete page source per poll
and parses identity, privacy, metrics, and visible post keys together. A
successful readiness snapshot is cached on the device adapter and reused by the
snapshot publisher so the same visible state is not fetched repeatedly.

If visible identity is confirmed but metrics remain incomplete for the full
bounded observation window, publish an inaccessible, ineligible snapshot and
complete the confirmed visit as trace-only. Identity mismatch, missing profile
surface, route failure, and unknown UI state remain deferred failures.

Interaction clicks continue through visible semantic controls. Post-action
success still requires a visible selected/reposted state; uncertain results are
never immediately clicked again.

## Resume And Acceptance

The production round remains paused in `/Users/Shared/TikPoc/tikpoc.db`. Its
checkpoint is `round-ae3616f70853b1901e95` with 7,781 completed assignments and
no active leases. After automated regression, resume that same round for a
15-minute six-device canary, then pause it cleanly for audit.

Promotion requires:

- six healthy workers and six healthy TikTok proxy probes;
- at least 400 completed assignments per device-hour averaged over the canary;
- no visible identity mismatch or false visit completion;
- no uncertain quota reservation or quota overrun;
- no duplicate action caused by retry;
- all deferred work either recovering normally or carrying an explicit bounded
  reason;
- measured stage and wall-clock throughput reported separately.

If the gate passes, continue the same round from its new durable checkpoint. If
it fails, pause again and use the stage evidence for the next single-variable
optimization.

## Second Canary: Semantic Action Query Consolidation

The first clean five-minute window after deferred recovery completed 239
assignments in 394.563 seconds, or 363.4 assignments per device-hour. Route and
metrics costs fell to roughly 0.5 seconds and single-digit milliseconds, while
eligible video actions still paid for repeated UiAutomator hierarchy queries.
Like averaged 10.8 seconds, favorite 24.5 seconds, and repost 25.5 seconds in the
action stage alone.

Keep semantic controls and visible post-action verification, but query each
outcome's active and inactive controls with one XPath union. Return both the
observed state and the reusable inactive control from that query. Execution may
reuse that control for the click; reconciliation only consumes the state. Every
post-click poll performs a fresh combined semantic query, so selected/reposted
state remains the terminal acceptance condition.

Do not use coordinates, cached XML, or an HTTP/Appium command response as action
confirmation. A missing or ambiguous combined result remains uncertain, and a
visible repost share surface still distinguishes unavailable from uncertain.

## Third Canary: Reuse Semantic Post Elements

The semantic action query canary completed 217 assignments in 354.872 seconds,
or 366.9 assignments per device-hour. All 26 completed interaction attempts were
confirmed and action mean fell to 9.6 seconds, but video mean remained 10.1
seconds. The adapter currently queries the same visible post containers once to
produce random-selection keys and again immediately before clicking the selected
post.

Cache the visible semantic WebElement tuple returned by `list_video_keys()` and
consume it in the immediately following `open_and_confirm_video()` call. The
selected element must still be clicked through Appium and the visible Share
control must still confirm that the video opened. Invalidate the tuple on every
route, back, restart, and after consumption. A stale element or missing visible
Share control remains an explicit deferred failure; do not fall back to bounds,
coordinates, compressed XML, or an unverified click result.
