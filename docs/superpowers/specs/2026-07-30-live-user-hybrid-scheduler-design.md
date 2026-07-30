# Live User Hybrid Scheduler Design

## Goal

Integrate the existing browser/followers live-user collector, durable
`live_interrupt` profile-touch lane, VMOS brand-comment lane, and read-only Home
browsing into one autonomous HTTPS worker. A newly submitted live audience must
be processed while its interest is fresh, without losing comment pacing,
background checkpoints, exact identity verification, or per-account evidence.

## Product Rules

- Work priority is `live_interrupt`, then a due reviewed brand comment, then
  read-only Home-feed browsing.
- An APK finishes the currently executing UI operation before changing work
  kinds. It never revokes an in-flight lease.
- Every device/account captured as running when a live batch is accepted visits
  every valid target in that batch once, using its own durable shuffled order.
- A live target is a profile-touch target, not a comment target. The global
  one-video/one-brand-account rule continues to govern first-level brand
  comments only.
- A live profile with at least one visibly confirmed post draws only like,
  favorite, or repost; following/follower ratios do not gate this decision. A
  zero-post profile is trace-only. The immutable, quota-constrained outcome and
  the profile visit count only from visible-state evidence.
- Missing, suspended, or visibly unavailable targets terminate immediately for
  that device. Ordinary route or observation errors record one explicit result
  and release the worker for the next target; uncertain actions receive the
  existing single read-only reconciliation.
- When all snapshotted participants reach terminal results, workers return to
  their original comment schedule and Home-browsing state. Comment due times do
  not move merely because a live batch ran.

## Architecture

### Collector adapter

The collector emits atomic UTF-8 JSONL. Each row contains `username` and may
contain `sec_uid`, `uid`, `source_video_id`, and `collected_at_ms`. The submission
also carries a nonempty `source_live_id`. The existing CLI remains the stable
machine contract and an authenticated HTTP endpoint accepts the same normalized
payload for browser-driven operation.

The server validates usernames, deduplicates stable identities within the
submission, hashes the canonical content for idempotency, and never exposes
browser cookies or session state. Replaying the same source and content returns
the original batch and participant snapshot.

### Server work arbiter

`/api/mobile/pull` gains a hybrid task selector. For a provisioned acquisition
round it first asks the existing priority scheduler for an assignment. If no
eligible live assignment exists for that device, it asks the existing comment
session service for a due plan. If neither exists, it returns an empty task list
and the APK performs bounded read-only Home browsing.

The selector delegates persistence to the existing acquisition and comment
repositories. It does not create a second queue or copy leases. Each returned
envelope has an immutable `task_kind` (`profile_touch` or `brand_comment`) so the
APK dispatches to the existing executor. Results return through the existing
idempotent mobile result endpoint.

Live batches remain higher priority than comments until that device has no
claimable live work. Devices outside the immutable participant snapshot may
continue due comments. A participant waiting at the live barrier does not
publish a comment ahead of unfinished live work; it performs read-only Home
browsing until the barrier advances or the next live batch becomes claimable.

### Android worker

Provisioning uses a hybrid worker mode with its ordinary parent round ID. The
runner makes one pull request at a time, keeps at most one durable local task,
and dispatches by `task_kind`. Existing profile-touch and comment executors,
local outbox, session epoch, challenge handling, and startup recovery remain the
only UI execution paths.

The APK is independent of persistent ADB. ADB remains limited to install,
upgrade, provisioning, and diagnostics. Mac shutdown or network changes do not
interrupt an already provisioned device as long as the device can reach the
HTTPS service.

## Browser Validation Flow

1. Open a product-relevant TikTok live room in the user's authenticated browser.
2. Collect approximately ten currently visible audience identities through the
   existing followers collector surface.
3. Atomically submit one source-labeled live batch.
4. Confirm the server snapshots the six currently running VMOS accounts.
5. Observe exact username matches and visible visit/action evidence on each
   device.
6. Confirm `N/N` terminal accounting, no duplicate same-device visit, no brand
   comments on the target profiles, and automatic return to comment pacing.

Browser discovery is a human-visible acquisition surface. The system does not
read cookies, local storage, credentials, or hidden browser databases.

## Failure Handling

- Invalid or empty submissions are rejected atomically.
- A source/content replay is idempotent.
- No running acquisition round or no running devices rejects a new live batch.
- A device outside the participant snapshot receives no live assignment.
- Transport loss leaves the server lease and APK outbox durable for retry.
- A verification challenge records the existing block/recovery evidence and
  pauses comment/touch gestures on that device.
- If a browser session is signed out, collection stops before creating a batch;
  previously accepted batches remain unaffected.

## Acceptance Gates

1. Unit tests prove hybrid priority ordering, participant exclusion, barrier
   waiting, due-comment fallback, empty-queue browsing, and idempotent results.
2. Android tests prove `task_kind` dispatch, single-task local durability,
   profile-touch/comment coexistence, and restart recovery.
3. Existing priority, comment, mobile API, and full regression suites pass.
4. A synthetic two-device canary proves live preemption and exact checkpoint
   return without ADB during execution.
5. A browser-sourced ten-target canary proves exact identity, visible evidence,
   per-device coverage, and comment-schedule resumption on the six-device fleet.
6. Deployment retains a database backup and an immediate rollback release.

## Out Of Scope

- Automatic follow-back and direct-message replies.
- Commenting on live-user profiles or coordinating multiple brand comments on
  one video.
- Solving verification challenges.
- Replacing the followers collector's discovery algorithm.
