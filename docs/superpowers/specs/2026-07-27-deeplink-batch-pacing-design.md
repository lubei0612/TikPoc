# Deeplink B-Strategy And Lightweight Session Pacing Design

## Objective

Restore precise Deeplink navigation, retain B-strategy batch coverage, and use
lightweight session pacing without weakening identity, action, checkpoint, or
coverage verification. Stability and measured throughput take priority over a
fixed daily projection.

## Business Rules

- A large imported target pool is divided into ordered batches of 1,000 targets.
- Every active account processes every target in the current batch exactly once.
- Each device uses its own deterministic shuffled order.
- A priority batch saves the ordinary-batch checkpoint, runs to completion, and
  then resumes the ordinary batch from that checkpoint.
- Navigation mode is immutable `deeplink` for the round and its assignments.
- A visible exact username match is required before confirming a visit.
- A profile with at least one visible post is eligible for an interaction plan.
- Each eligible target receives exactly one planned outcome: like, favorite,
  repost, or trace-only, subject to the existing rolling account quotas.
- A target without a visible post completes as a confirmed trace-only visit.
- A selected interaction must reach a visible confirmed, explicit unavailable,
  or recorded uncertain terminal state before the worker advances.
- An uncertain interaction receives one read-only reconciliation. It is never
  immediately applied a second time.
- An inaccessible, missing, suspended, or exact-identity-mismatched target is
  recorded and skipped without interacting with a different profile.

## Lightweight Session Pacing

Pacing varies timing and session boundaries without adding unrelated target
actions or weakening verification:

- Ordinary UI transitions receive a deterministic per-device delay between
  200 and 1,200 milliseconds.
- Each device processes a deterministic segment of 40 to 80 targets.
- At a segment boundary the executor returns to the TikTok home surface, performs
  a short read-only browse sequence, and then continues from its durable batch
  checkpoint.
- Segment size, delay values, and browse duration derive from the device seed so
  behavior is reproducible in tests while differing across devices.
- Pacing never inserts follows, messages, comments, or interactions with targets
  outside the imported batch.
- A single ordinary execution failure may be retried once. Explicit unavailable
  targets terminate immediately; uncertain actions use only the reconciliation
  rule above.

## Components

### Eligibility policy

The shared profile rule becomes `video_count >= 1`. Following and follower
metrics remain captured for funnel analysis but do not gate interaction.

### Batch scheduler

The scheduler exposes 1,000-target B-strategy batches, independent device order,
durable checkpoints, and existing priority preemption/resumption. Coverage is
calculated from confirmed device-profile visits, not attempts.

### Pacing planner

A pure deterministic planner accepts device seed, segment progress, and command
kind and returns the next delay plus any due session-boundary browse command. It
does not perform UI actions or own persistence.

### Android executor

The autonomous APK applies planned delays, performs the bounded home browse at a
segment boundary, and reports its checkpoint before and after the boundary. ADB
remains limited to installation, upgrades, and diagnostics.

### Observability

The server records navigation, identity confirmation, post availability, planned
outcome, verified result, retries, pacing overhead, segment boundaries, and
assignment duration. Server visit confirmation remains distinct from TikTok's
external visitor-list visibility.

## Failure Handling

- Deeplink route or identity failure: one reacquisition attempt, then durable
  skip/defer according to the existing explicit error classification.
- Missing post handle after a confirmed eligible snapshot: use only a handle
  from the stable first visible grid row; one reacquisition attempt is allowed.
- Interaction uncertainty: one read-only reconciliation and then terminal
  confirmed or uncertain.
- Network or server interruption: retain the local APK queue and server lease;
  resume idempotently after connectivity returns.
- Priority arrival: persist the ordinary checkpoint, finish the priority batch,
  then resume without recreating completed assignments.

## Verification

1. Unit tests for `video_count >= 1`, deterministic delay bounds, segment size,
   retry limits, and immutable Deeplink navigation.
2. Scheduler tests proving all active devices cover the same 1,000 targets in
   different deterministic orders and resume correctly after priority work.
3. Android tests for pacing, home-browse boundaries, local checkpoints, and
   restart recovery.
4. Full Python and Android regressions plus lint/format checks for touched files.
5. Live acceptance in stages: 20 targets, 100 targets, then a 30-minute run.
6. Report measured targets/hour, mean and p90 duration, exact-identity rate,
   interaction confirmed/uncertain rates, retry rate, and pacing overhead.

Production promotion requires exact identity and durable coverage accounting to
pass, no duplicate interactions, and a stable long-run rate based on live data.

