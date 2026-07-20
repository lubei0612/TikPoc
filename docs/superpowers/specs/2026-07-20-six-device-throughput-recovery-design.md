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
