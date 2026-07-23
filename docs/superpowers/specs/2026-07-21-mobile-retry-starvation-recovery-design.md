# Mobile Retry Starvation Recovery Design

## Goal

Restore the paused six-device acquisition round to stable forward progress without
opening TikTok Inbox as a mobile recovery baseline, without allowing deferred
assignments to starve untouched targets, and without weakening identity,
interaction, uncertainty, quota, or coverage accounting.

## Observed Production Failure

The round reached 29,940 completed assignments before the audit pause. All six
ADB endpoints and all six HTTP proxies were healthy, Appium was ready, and no
TikTok crash or ANR explained the visible behavior. Android exit history instead
showed user-requested force stops issued by the worker.

The mobile adapter explicitly opened `tiktok://inbox` when a stable profile route
did not visibly replace the preceding profile. If that baseline did not clear,
the adapter restarted TikTok, opened Inbox again, and retried the profile route.
Four devices were observed on Inbox together. Home, black, and splash screens
were transient states from the same restart path.

The assignment selector also placed every due `deferred` row before every
`pending` row. At the pause there were 136 deferred rows and about 140 confirmed
visits without assignment completion. Several had 40-59 claims. An uncertain
plan received only one reconciliation inside a claim, but the assignment became
eligible again five minutes later, so repeated claims recreated an unlimited
reconciliation loop. During the final five-minute window all six devices
completed zero assignments.

## Recovery Design

### Inbox-Free Profile Recovery

Mobile acquisition must never use Inbox as a baseline or recovery route. The
browser lead plane remains unchanged.

After the initial stable-ID route fails to show a changed, complete profile
surface, terminate TikTok once and dispatch the same stable-ID route directly.
Do not activate the previous application tab between termination and route
dispatch. Accept the restarted route only when a visible profile surface exists
and its username differs from the profile visible before routing. If the stable
route still fails, try the exact username/profile URL once. Existing identity
mismatch handling remains terminal for the claim and is never accepted as a
visit.

### Pending Work Before Deferred Recovery

For normal automatic claims, untouched `pending` assignments precede due
`deferred` assignments. This keeps the full target-pool pass moving and prevents
a small poison backlog from consuming all devices. No deferred assignment is
deleted, completed, or counted as coverage. Operator-triggered retry continues
to make the assignment retry-ready; deferred reconciliation is handled after the
main pass or during a deliberate repair window.

### One Durable Uncertain Reconciliation

An uncertain action receives one reconciliation after the original action
attempt. If that reconciliation is still ambiguous, the assignment remains
deferred with its immutable action plan and quota reservation, but its automatic
retry time is set to manual-only. A later operator retry may explicitly make it
claimable again. The worker must not click the action control again.

A profile that already has a durable confirmed visit but cannot be reopened for
its unfinished plan is also deferred for manual repair rather than automatically
reclaimed every five minutes. It is not skipped and is not counted as assignment
completion.

## Business Invariants

- Eligibility remains `following > followers AND video_count >= 1`.
- A due like, favorite, or repost is not converted to trace for speed.
- Visible identity, video-open, and post-action verification remain mandatory.
- Confirmed visits remain coverage; skipped and deferred rows do not become
  completed coverage.
- Existing action plans, selected videos, quota reservations, and action evidence
  remain immutable across recovery.
- One device's failure does not suppress the target on another device.
- The change adds no mobile Activity, Inbox, DM, or follow-back behavior.

## Live Acceptance

After focused and full automated verification, resume the same durable round and
run a six-device canary. Acceptance requires:

- six healthy ADB devices, proxies, Appium sessions, and worker leases;
- no explicit `tiktok://inbox` route from the mobile acquisition process;
- no repeated automatic claims of manual-review uncertain assignments;
- new pending assignments continue completing on every device;
- no duplicate action attempt, identity mismatch completion, or quota overrun;
- interaction-eligible completions retain confirmed action evidence;
- measured throughput recovers above 400 completed assignments per device-hour
  on a stable rolling window, with slot 4 reported separately.

After the canary passes, keep the round running and perform the broader coupling
and business-risk review without interrupting the device workers.
