# VMOS Touch Throughput Recovery Design

## Goal

Raise the current VMOS mobile-touch worker from the measured diagnostic pace to
at least 10,000 confirmed target visits per account in a 20-hour productive day,
without changing qualification, interaction, identity, verification, quota, or
coverage behavior.

The minimum sustained rate is 500 confirmed visits per device-hour. The preferred
operating buffer is 550 or more per device-hour. A short recovery canary may use
400 per hour as the first gate, but 400 per hour is not the daily-capacity pass.

## Current Evidence

A fresh isolated 20-target VMOS diagnostic on 2026-07-25 was stopped after
429.45 seconds. It produced 13 confirmed visits, six terminal completions, seven
deferred assignments, six pending assignments, and one in-flight assignment.
That is about 109 confirmed visits per wall-clock hour, or about 33 seconds per
confirmed visit. The observed stage evidence was:

- route: 14 samples, 1.26-second mean, 3.15-second maximum;
- identity: 13 samples, 6.29-second mean, 12.52-second maximum;
- metrics: 10 samples, 1.48-second mean, 5.63-second maximum;
- video: 6 samples, 6.64-second mean, 7.72-second maximum;
- action: 6 samples, 4.98-second mean, 12.40-second maximum.

The diagnostic contained four `RuntimeError` and three `ValueError` deferred
results. It was intentionally interrupted before all 20 assignments exhausted
their retry policy, so it is diagnostic evidence rather than a capacity result.

An earlier 50-target VMOS diagnostic recorded about 287 visits per hour. The
regression between the two windows means the first task is evidence collection,
not a global timeout reduction.

## Business Invariants

- Eligibility remains `following > followers AND video_count >= 1`.
- A qualifying profile opens one deterministic randomly selected visible video.
- The requested outcome remains like, favorite, repost, or trace before quota
  constraints, with the existing rolling limits.
- Every confirmed visit requires visible target identity evidence.
- Video opening and non-trace actions retain visible result verification.
- A due interaction is never converted to trace for speed.
- `uncertain` receives one reconciliation and is not clicked again automatically.
- Explicit missing/suspended targets keep the existing bounded terminal skip
  behavior. Ordinary route, loading, identity, metric, or action errors remain
  deferred according to the current state machine.
- One device remains bound to one account and one worker lease.
- Optimization does not add Inbox navigation, coordinate fallbacks, duplicate
  actions, or false completion records.

## Proposed Architecture

### 1. VMOS measurement adapter

Add per-assignment counters around the existing device adapter for:

- Appium command count and total command wall time;
- page-source reads by stage;
- element queries by stage;
- route attempts and recovery branches;
- waits that reached their timeout versus waits satisfied by visible state.

The counters are diagnostic metadata only. They do not change assignment state
or business decisions and remain out of public logs when they contain target
identifiers.

### 2. Evidence-driven semantic fast path

Promote only optimizations supported by the baseline counters. The intended
fast path reuses one current profile observation across identity, following,
followers, and visible-video discovery when all required fields are present.
It reuses the already verified visible video element for opening rather than
performing a second equivalent hierarchy search.

If the combined observation is incomplete, stale, hidden, or mismatched, the
worker falls back to the existing semantic slow path. The fast path never
supplies default metrics and never bypasses the username check.

### 3. Bounded state waits

Replace repeated fixed sleeps or repeated equivalent hierarchy reads with
condition-based waits for the exact next visible state. Existing stage timeouts
remain the slow-path ceiling. No global Appium timeout reduction is allowed,
because the previous 30-to-20-second experiment increased uncertain results.

### 4. Deferred-tail classification

Separate target-local terminal evidence from device/runtime failures. The
optimizer may remove repeated equivalent work inside one attempt, but it may not
reduce the existing retry count or reclassify a route, identity, metric, video,
or action failure merely to improve throughput.

## Implementation Sequence

1. Reproduce the current VMOS result in a new isolated database and record RPC,
   stage, error, identity, visit, and action evidence.
2. Rank latency by total wall contribution, including deferred attempts.
3. Add one failing behavioral test for the dominant redundant operation.
4. Implement one optimization variable and rerun the same bounded target slice.
5. Retain the change only if throughput improves without any invariant failure.
6. Repeat for the next dominant stage, stopping when further changes are small
   or the capacity gate passes.

The first candidates are combined profile observation reuse, visible post
reuse, and removal of duplicate state reads. Startup staggering, Appium process
splitting, and global timeout reductions are excluded unless new evidence
contradicts the earlier measured results.

## Verification

### Functional gate

Every candidate runs focused Python tests for device, rules, mobile worker,
fleet runtime, and capacity accounting, followed by Ruff, format, and diff
checks. A real VMOS canary verifies:

- exact username identity;
- qualification snapshot correctness;
- eligible interaction execution;
- visible action evidence;
- no duplicate action attempts;
- no quota or lease violations;
- confirmed, skipped, deferred, and uncertain accounting.

### Throughput gates

1. **20-target diagnostic:** confirms the change affects the measured dominant
   stage and introduces no correctness regression.
2. **100-target canary:** at least 400 confirmed visits per device-hour, with
   clean identity and action evidence.
3. **30-minute promotion canary:** at least 500 confirmed visits per hour and a
   projected 20-hour capacity of at least 10,000 targets.
4. **Preferred buffer:** at least 550 per hour, mean below 6.5 seconds and P90
   below 8.64 seconds when measured by the accepted capacity audit.

Measured throughput and projected daily capacity are reported separately. A
short peak, unit test, or partially completed diagnostic does not establish the
daily goal.

## Rollback

Each optimization is a separate commit. A candidate is reverted if it raises
identity mismatches, incomplete qualification snapshots, trace substitution,
uncertain actions, duplicate attempts, deferred rate, or P90 latency. The
isolated diagnostic database and target slice remain available for comparison;
production rounds and checkpoints are not rewritten.
