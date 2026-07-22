# Live-Interest Priority Batch Design

## Goal

Allow a second collector/AI process to submit users found in a relevant live
room while a large acquisition round is already running. The current target
finishes its atomic workflow, the submitted live-interest batch runs before the
ordinary round, and all participating accounts touch the batch once before the
ordinary round resumes from its durable checkpoint.

This changes scheduling only. Profile identity checks, eligibility rules,
interaction selection, quotas, visible action verification, retry limits,
coverage accounting, and account/device isolation remain unchanged.

## Source File Observations

The two desktop workbooks named `followers-alexanderdagreat94.xlsx` and
`followers-alexanderdagreat94 (1).xlsx` are byte-level equivalent in their
worksheet data: 300 rows, 300 unique `follower_handle` values, no `sec_uid`
values, and zeroed follower/following/video metrics. Their `follower_uid`
values use a collector-local `dom-<ordinal>-<handle>` form and are not treated as
platform-stable IDs. The English workbook headers require a dedicated adapter;
the existing Chinese-header workbook adapter is not reused by position.

The machine-facing collector contract therefore accepts a real username and an
optional platform user ID or sec UID. Collector-local IDs remain source
lineage only.

## Scheduling Model

The active ordinary round remains durable and keeps its current assignment
leases, phases, and order keys. A submitted priority batch creates its own
immutable target pool and per-device assignment set linked to the ordinary
round. A scheduler chooses work in this order:

1. finish the currently leased assignment;
2. run the oldest incomplete priority batch;
3. wait at a batch barrier until every participating device has reached a
   terminal state for that priority batch;
4. run the next priority batch, if any;
5. resume the ordinary round from its existing assignment checkpoint.

Priority batches are FIFO. A newer batch never preempts the currently running
priority batch. A device that finishes early waits at the barrier rather than
returning to ordinary work, preserving the requirement that the complete batch
is touched by all participating accounts before the large round resumes.

The participating device/account set is snapshotted from the active ordinary
round when the priority batch is submitted. A device that temporarily loses its
lease resumes its own unfinished assignments after recovery. A device added
later is not silently inserted into an already-created batch.

## Identity And Deduplication

Within a priority submission, targets are deduplicated using `sec_uid`, then a
real platform user ID, then normalized username. The collector-local `dom-*`
identifier is never preferred over a real username.

If an account already has a confirmed visit for the identity in the current
ordinary round, the priority assignment for that account is recorded as
satisfied without a second visit. If the ordinary assignment is still pending,
a confirmed priority visit satisfies that account's ordinary assignment as well.
This prevents duplicate touches while retaining all-account coverage.

## CLI Contract

The first implementation exposes explicit commands rather than a web-only
endpoint:

```text
tikpoc priority-import --db DB --devices DEVICES --file INPUT --source-live LIVE_ID
tikpoc priority-status --db DB
```

The collector input is JSONL for deterministic machine exchange. Each row must
include `username` and may include `user_id`, `sec_uid`, `profile_url`,
`source_live_id`, and `collected_at`. The importer also accepts the existing
follower workbook shape through a named header adapter, preserving source row
lineage. Import returns a batch ID, unique count, duplicate count, and the
ordinary round it is queued behind.

Status reports queued/running/barrier/completed state, per-device counts,
deduplication decisions, source live-room IDs, and the ordinary-round
checkpoint. It never prints credentials, cookies, or private browser state.

## Failure And Recovery

- A process stop or Mac disconnect leaves leases and phases durable; workers
  recover stale leases and continue the same priority batch.
- An inaccessible target follows the existing bounded terminal-skip policy and
  does not count as a confirmed visit.
- An uncertain action remains held for reconciliation and is never immediately
  clicked again.
- If a priority batch cannot finish because a device is offline, status exposes
  the blocking device and the ordinary round remains behind the barrier.
- Importing the same source checksum and batch key is idempotent.

## Acceptance Criteria

1. A running ordinary round records its assignment checkpoint before switching.
2. A priority batch is processed by every participating account exactly once
   per identity unless that account already has a confirmed visit in the same
   campaign.
3. Two priority batches execute FIFO, with no ordinary assignment claimed
   between their barriers.
4. After the final barrier, ordinary work resumes at the prior durable
   checkpoint without resetting order or attempt counts.
5. Restart and stale-lease recovery preserve the same queue order and coverage.
6. Existing mobile worker and coverage tests remain green, and new tests cover
   import normalization, idempotency, FIFO barriers, cross-batch deduplication,
   and crash recovery.

