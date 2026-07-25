# VMOS Device-Side Touch Executor Design

## Goal

Raise one VMOS TikTok account to at least 10,000 confirmed target visits in a
20-hour productive day while preserving the existing identity, qualification,
interaction, verification, quota, retry, checkpoint, and coverage contracts.

The minimum sustained promotion rate is 500 confirmed visits per device-hour.
The preferred operating buffer is 550 or more per hour. Measured throughput is
reported separately from projected daily capacity.

Automatic browser follow-back and direct-message replies are outside this
increment and remain disabled.

## Evidence And Decision

The completed representative Appium canary in
`/Users/Shared/TikPoc/vmos-rate/vmos-touch-representative-100-v2.db` produced
100 confirmed visits in 1,814.621 seconds, or 198.4 per hour. Identity averaged
7.202 seconds before eligible targets paid another 7.084 seconds for video and
6.926 seconds for action. Appium command time was dominated by repeated remote
accessibility-tree requests over the VMOS ADB tunnel.

Selector-level Appium tuning cannot close a 2.5-times capacity gap while keeping
visible verification. The selected architecture moves semantic observation and
control onto the VMOS device through a TikPoc Android AccessibilityService. The
Python worker remains the sole owner of durable state and policy.

Rejected approaches are:

- repeated remote `uiautomator dump`, because hierarchy transfer remains on the
  latency-critical path;
- further Appium polling changes, because identity alone exceeds the complete
  per-target budget;
- coordinate-only ADB tapping, because it cannot prove semantic identity or
  action state.

## Architecture

### Android helper

`android-touch-executor/` is a small Android application containing an
AccessibilityService and a localhost-only command endpoint forwarded through
ADB. It does not contain target queues, qualification rules, outcome selection,
quotas, retry policy, or account credentials.

The service maintains one current accessibility snapshot for TikTok. It indexes
visible nodes by normalized text, content description, resource ID, class, and
bounds. It exposes bounded semantic commands:

- `health`: TikTok foreground state, service enabled state, visible surface,
  helper version, and current command occupancy;
- `open_profile`: launch one stable target route and wait for a new visible
  profile identity;
- `observe_profile`: return the visible username, following, followers, video
  count evidence, privacy/access state, and post handles from one coherent tree;
- `open_video`: activate one selected visible post handle and return visible
  video-control evidence;
- `observe_action`: return the current like, favorite, repost, and share state;
- `apply_action`: activate one uniquely matched semantic control and wait for
  its visible resulting state;
- `diagnostics`: return bounded redacted surface metadata without target text,
  screenshots, credentials, or network configuration.

The helper never chooses whether a target is eligible or which action is due.
It never clicks when a selector is absent, hidden, stale, or ambiguous.

### Host adapter

Python adds a `DeviceSideTikTokDevice` implementation of the existing
`VerifiedTikTokDevice` protocol. Fleet configuration selects `device-side` as a
backend and provides one ADB endpoint plus one forwarded helper port per device.

The adapter translates existing worker calls into helper commands. It validates
the helper version, device/account fence identity, command ID, target identity,
and response schema before returning domain objects. Appium remains available as
a rollback backend and is not used concurrently for the same device/account.

### Ownership and concurrency

One fleet worker owns one device/account fence exactly as today. Every helper
command carries:

- a random command ID;
- device ID and account ID;
- current worker fence token;
- assignment ID and expected phase;
- a monotonic deadline.

The helper accepts one command at a time. Repeating the same command ID returns
the stored bounded result. A different command while busy receives `busy`; it is
never executed concurrently. Restarting the helper clears only its short-lived
command cache, while SQLite remains authoritative.

## Command And Evidence Contract

Requests and responses use length-bounded JSON over an ADB-forwarded loopback
socket. The endpoint binds only inside the Android application sandbox. ADB owns
the host forwarding rule; no device LAN listener is exposed.

Every successful response includes helper version, command ID, elapsed time,
TikTok package/activity, accessibility event sequence, and a semantic evidence
digest. Profile responses include the exact normalized visible username and the
source resource IDs used for following, followers, and posts. Action responses
include before and after semantic states plus the uniquely matched control.

The host rejects responses with a mismatched command ID, fence, target identity,
phase, unsupported helper version, expired deadline, or incomplete evidence.

## Existing Business Behavior

The Python state machine remains unchanged:

- eligibility remains the currently approved
  `following > followers AND video_count >= 1` rule;
- every target requires exact visible username confirmation before recording a
  visit;
- eligible profiles use the existing immutable random video/action plan;
- like, favorite, repost, and trace selection and rolling quotas remain owned by
  Python;
- repost completes only after the visible repost control and resulting state are
  verified;
- `uncertain` receives one read-only reconciliation and never an immediate
  second click;
- missing or suspended profiles use existing explicit terminal handling;
- loading, route, identity, observation, video, and action ambiguity keep their
  current deferred behavior;
- all configured accounts process the same logical batch with durable coverage.

No speed gate may weaken these behaviors or convert a due interaction into trace.

## Failure And Recovery

Errors are classified at the adapter boundary:

- transport loss, helper crash, disabled accessibility, or version mismatch
  aborts the device worker so the fleet supervisor rebuilds it with backoff;
- target-local missing/suspended evidence follows current terminal handling;
- stale or ambiguous profile/video/action evidence defers the assignment;
- an action command that may have been applied but lacks final evidence returns
  `uncertain` and permits only existing read-only reconciliation;
- a command deadline stops further helper interaction for that command.

Before each restart, the worker retains the durable assignment phase and action
plan. Recovery does not generate a new plan or action reservation.

## Performance Instrumentation

Persist per-stage helper command count, helper processing time, host round-trip
time, accessibility tree age, event wait time, and fallback reason beside the
existing route, identity, metrics, video, and action timings. Target identifiers
and visible content remain out of aggregate logs.

The first optimization target is one coherent profile tree for identity,
metrics, and visible posts. A fresh tree is required after navigation or when
the cached tree lacks complete, matching evidence.

## Delivery And Gates

1. Build and unit-test the helper protocol, semantic parser, exclusive command
   gate, and idempotent command cache with synthetic accessibility trees.
2. Add the Python transport and `VerifiedTikTokDevice` adapter with contract,
   timeout, fence, and failure tests.
3. Install the helper on one VMOS canary, enable AccessibilityService, forward
   the loopback port, and verify health without target actions.
4. Run a fresh 20-target canary. Require exact identity/action audits, no
   duplicates or quota violations, and at least 400 confirmed visits per hour.
5. Run a fresh representative 100-target canary. Require 100/100 confirmed
   coverage and at least 500 visits per hour.
6. Run a 30-minute promotion canary. Require at least 500 measured visits per
   hour, projected 20-hour capacity of at least 10,000, mean below 6.5 seconds,
   and P90 below 8.64 seconds.
7. Only after the single-device gate passes, repeat with two devices and then the
   configured multi-account fleet. Seven-account capacity remains the reference
   `70,000` confirmed device-profile visits for 10,000 unique targets.

Short peaks, synthetic tests, or partial samples do not establish capacity.

## Deployment And Rollback

The helper APK is built from repository source and installed through the current
VMOS ADB lease. Enabling its accessibility service is a visible canary setup
step. No TikTok credentials, proxy configuration, or VMOS API secret is embedded
in the APK.

Fleet backend selection is per device. Rollback stops the device-side worker,
removes its ADB forward, disables the helper service, and restores the existing
Appium backend at the same durable round checkpoint. The two backends never own
the same device/account fence concurrently.

The device-side backend is rejected if any correctness audit regresses, if the
20-target gate stays below 400 per hour, or if the 100-target gate stays below
500 per hour. The failed evidence remains documented rather than being reported
as production capacity.
