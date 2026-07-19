# TikPoc Mobile Fleet Runbook

## MYT Slot 1 Functional Gate (2026-07-19)

The first complete live functional round used MYT slot 1 with TikTok `44.8.42`
and the current 372-row comment export. Import produced 326 stable targets and 46
duplicate source rows. The round finished with:

- 326/326 completed assignments and confirmed visits;
- 326 immutable qualification snapshots;
- 119 eligible profiles and 37 private profiles;
- 21 confirmed favorites, 37 likes, 37 reposts, and 231 trace outcomes;
- zero duplicate visits, nonterminal plans, uncertain quota reservations, or
  active worker leases at completion.

Use an ignored local configuration and database:

```bash
export ANDROID_HOME="${ANDROID_HOME:-$HOME/Library/Android/sdk}"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
"$ANDROID_HOME/platform-tools/adb" -s ADB_ENDPOINT forward --remove tcp:8200 || true
"$ANDROID_HOME/platform-tools/adb" -s ADB_ENDPOINT forward --remove tcp:9100 || true
npm run appium -- --address 127.0.0.1 --port 4723

uv run tikpoc pool-import --db var/tikpoc.db --csv PATH_TO_CSV
uv run tikpoc round-create \
  --db var/tikpoc.db --pool POOL_ID \
  --devices config/settings.yaml --starts-at ISO_8601_WITH_TIMEZONE
uv run tikpoc fleet-run \
  --db var/tikpoc.db --round ROUND_ID --devices config/settings.yaml
```

Start Appium in a separate terminal before `fleet-run`. `--starts-at` must be at
or before the intended execution time; a future value leaves the healthy worker
waiting with every assignment still pending. If an earlier Appium process was
terminated while a worker session existed, remove that slot's `systemPort` and
MJPEG forwards before starting the replacement server.

TikTok 44.8.42 uses the current profile IDs `rgn`, `rfd`, `rfc`, `efq`, and
`z9h`. Fleet Appium sessions must set `waitForIdleTimeout=0`. Favorites expose
no reliable selected/checked accessibility state; verify the bounded favorite
button screenshot contains the active yellow icon. Stable `user_id` profile
routes handle renamed accounts; require a visible profile transition, using the
Inbox baseline when the device is already on the same renamed profile.

## Capacity Result

This functional round is not a capacity pass. Debugging and repeated recovery
were retained in the durable history. The measured mean was 170.718 seconds,
P90 was 15.572 seconds, and projected 20-hour capacity was 421 targets/day.
The capacity command correctly failed on timing and historical identity mismatch
evidence. Run a fresh, calibration-free round before evaluating promotion.

## Paced Slot 1 Final-Attempt Gate (2026-07-19)

A new database and round reused the available 326-unique-target CSV after the
rolling pacing and route-recovery changes. Durable business state finished with:

- 326/326 completed assignments, confirmed visits, and complete coverage;
- 45 confirmed likes, 7 favorites, 11 reposts, and 263 trace-only visits;
- zero uncertain plans and zero active assignment leases;
- no quota integrity, false-completion, identity, or cardinality findings.

The actions were created across approximately 26 minutes. Their relative counts
track the configured hourly rates of 100/14/25 rather than firing at round start.

Capacity uses the final `profile_opening` attempt for a recovered assignment and
accepts `pacing_not_due` only for a quota-free trace plan. The resulting audit was:

- mean 4.658 seconds, P90 6.980 seconds;
- 772.8 confirmed targets/hour;
- projected 15,455 unique targets per 20-hour day;
- route/identity/metrics/video/action P90 values of 1.302/2.461/0.996/2.022/2.461 seconds.

One target exposed a stable-user-ID route that remained blank for more than 60
seconds. The bounded recovery now tries the stable route, Inbox baseline, one app
restart, then the public username URL with exact visible-username verification.
That target completed through the final fallback.

This was measured 326-target final-attempt evidence, not the planned clean
500-unique-target promotion gate. The run was interrupted for route diagnosis
and the affected assignment accumulated earlier failed attempts before the fix.
The next section records the later 500-target result.

## Mixed-Build 500-Target Diagnostic (2026-07-19)

A fresh database imported 500 unique targets from the deduplicated 16,384-target
export and ran them on MYT slot 1. The final durable state was:

- 500/500 completed assignments, confirmed visits, and 1/1 coverage;
- 10 confirmed favorites, 67 likes, 15 reposts, and 408 trace-only outcomes;
- 342 ineligible traces, 64 pacing traces, and 2 `repost_unavailable` traces;
- zero deferred assignments, uncertain plans, uncertain quota reservations,
  active assignment leases, false completions, quota overruns, identity
  mismatches, or assignment-cardinality findings.

Two selected videos opened a visibly complete share surface containing controls
such as Copy link but no Repost control. They remained deferred under the prior
build and accumulated only `not_applied`/`uncertain` attempts. The accepted build
distinguishes that visible unavailable state from an unknown UI state, preserves
the requested repost, releases the repost quota reservation, and confirms the
already verified video visit as trace-only. It never reports either case as a
repost.

The final capacity audit reported:

- measured assignment time: 2,394.932 seconds;
- mean 4.790 seconds and P90 7.756 seconds;
- 751.6 confirmed targets/hour;
- projected 15,031 unique targets per 20-hour day;
- no capacity rejection reasons.

The final state meets the numeric single-device thresholds, but this round
resumed two deferred assignments after the implementation changed. It is useful
root-cause and final-state evidence, not the clean final-build fresh-500 gate.
Run the final build from a new database and round before promotion. It is also
not evidence for multi-device coverage, four-/eight-hour endurance, or the
seven-device 70,000-visit daily benchmark.

## Final-Build 100-Target Preflight (2026-07-19)

After the unavailable-repost implementation and tests were committed, slot 1
ran an unchanged build against a new database, round, and 100-unique-target CSV.
The Appium server started with explicit Android SDK environment variables and
stale slot-1 ADB forwards removed before session creation. The final durable
result was:

- 100/100 completed assignments and 1/1 coverage;
- mean 6.812 seconds and P90 10.847 seconds;
- 528.5 confirmed targets/hour;
- projected 10,570 unique targets per 20-hour day;
- zero uncertain results.

The capacity command failed only with `device timing threshold exceeded`.
Several one-attempt video and action paths produced real long tails, so this was
not a retry-integrity failure. Do not promote this build to the clean 500-target
gate until another fresh 100-target preflight is below both 6.5-second mean and
8.64-second P90 thresholds. The database is
`var/myt-slot-01-preflight-100-final-v4-20260719.db` and remains ignored local
evidence.
