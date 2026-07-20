# Terminal Unavailable Target Design

**Date:** 2026-07-20

## Goal

Stop spending device capacity on a target after TikTok visibly confirms that the
target account is permanently unavailable. The first device that observes the
terminal page records the result durably; later device assignments for the same
round and target skip without opening TikTok.

## Classification Boundary

Only explicit visible terminal states qualify, including localized equivalents
of:

- `Account banned`
- `This account is no longer available`
- an explicit deleted/nonexistent-account page

A blank page, black video, missing marker, route timeout, Appium error, or
temporary loading failure is not terminal evidence. Those failures retain the
existing bounded retry policy.

## Durable State And Assignment Behavior

The round-level profile snapshot for the identity records:

- `access_state = permanently_unavailable`
- `eligible = false`
- `reason = permanently_unavailable`
- the observing device and observation time

On the first explicit terminal observation:

1. The current assignment becomes `skipped` after its first attempt.
2. No confirmed visit is recorded.
3. The assignment error is `profile_permanently_unavailable`.

For later assignments in the same round and identity:

1. The worker checks the durable marker immediately after claiming work.
2. The assignment becomes `skipped` without calling the device.
3. The assignment remains visible as missing confirmed coverage.

If another device already began navigating before the first observation was
persisted, that in-flight operation may finish. The durable marker prevents all
subsequent claims and retries.

## Component Changes

### Appium device adapter

Recognize explicit terminal page text during profile identity/surface
confirmation and raise a dedicated permanently-unavailable exception. Do not
infer permanence from generic loading failures.

### Mobile worker

Handle the dedicated exception separately from ordinary `ProfileUnreachable`:

- persist the round-level terminal marker;
- skip the current assignment immediately;
- preserve the existing three-attempt policy for ordinary unreachable errors.

Before device readiness or navigation, check for an existing terminal marker and
skip the claimed assignment without device activity.

### Acquisition repository

Provide atomic operations to record the terminal marker and skip an unconfirmed
assignment. The operation must preserve leases/fences, reject confirmed visits,
and retain phase-history diagnostics.

## Verification

Automated tests must prove:

1. Explicit banned/deleted UI produces the dedicated classification.
2. The first assignment skips after one attempt with zero confirmed coverage.
3. A sibling device assignment skips without any device method call.
4. Generic blank/timeout failures still require the existing bounded retries.
5. Completion and capacity reporting count terminal targets as skipped, not
   completed visits.

Live verification uses a visible terminal target fixture and confirms that later
device workers do not reopen it. Logs and screenshots must not expose personal
data.
