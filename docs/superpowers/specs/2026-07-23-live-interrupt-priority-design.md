# Live Interrupt Priority Design

## Goal

A collector AI can submit a freshly collected TikTok target file while Strategy B is running. The submitted batch preempts preloaded Strategy B background waves after each worker finishes its currently leased target, runs once across the devices that were operationally active at submission time, and then returns to the exact durable Strategy B checkpoint.

## Problem

The existing `priority_batches` queue is also being used to store preloaded Strategy B waves. Both background waves and live submissions share one FIFO sequence, so a newly submitted live batch is appended behind every preloaded wave. The global six-device barrier also includes paused devices, which means a successful import can remain queued indefinitely instead of interrupting current work.

## Batch Classes

`priority_batches` gains an immutable `batch_class`:

- `background`: preloaded Strategy B waves. They retain FIFO order, while the barrier is evaluated against the devices currently controlled as `running` so temporarily paused devices do not halt the active cohort.
- `live_interrupt`: collector submissions. They are selected before every background batch, while remaining FIFO among themselves.

Existing databases migrate conservatively with `live_interrupt` as the schema default because that preserves the behavior of batches created by earlier releases. The current Strategy B database is backed up and its `strategy-b-*` batches are explicitly reclassified as `background` before live acceptance.

## Participant Snapshot

A `live_interrupt` snapshots only parent-round devices whose operator control state is `running` at import time. Missing control rows mean `running`; `paused` and `stopped` devices are excluded. The snapshot is persisted through `round_device_seeds` and never changes if controls later change.

A background batch must still use the complete parent device set. A live batch must use a nonempty subset of that set. Every snapshotted device receives one assignment for every imported target, with its normal durable shuffle seed, identity checks, eligibility rules, quota rules, visible-state verification, and retry behavior.

Repeated import of the same parent round, `source-live`, and file SHA-256 returns the original batch and its original participant count even if device controls changed after the first submission.

## Scheduling

When a worker asks for scheduled work:

1. Its existing assignment lease is never revoked; the current target reaches a recorded terminal/deferred boundary first.
2. The scheduler selects the oldest incomplete `live_interrupt` batch.
3. Only snapshotted devices can claim that batch. Other devices receive no work from it.
4. Fast participants wait at that live batch's participant barrier.
5. After all participant assignments are terminal, the scheduler selects the next live interrupt.
6. When no live interrupt remains, scheduling returns to the oldest incomplete background wave and its existing checkpoint.

A live batch does not mark background assignments completed and does not change their order keys, phases, attempts, or leases. Confirmed-visit propagation continues to suppress genuine same-device duplicate visits; skipped, deferred, uncertain, or unconfirmed results do not propagate.

For background waves, a paused device's assignments remain pending and auditable. When every currently running device reaches a terminal phase for the oldest wave, those running devices may advance together to the next background wave. When a paused device resumes, it becomes part of the running barrier again, claims its earliest missed wave first, and the already-ahead devices wait until the resumed cohort catches up. No paused assignment is marked completed or skipped merely to advance the active cohort.

## CLI Contract

`tikpoc priority-import` creates `live_interrupt` batches by default. It continues accepting the full fleet YAML, but the service derives participants from persisted operator control state rather than requiring all configured devices to participate. Configured device IDs must still exactly match the active ordinary parent round, preventing accidental submission to the wrong fleet.

Successful JSON adds:

```json
{"batch_class":"live_interrupt","device_count":4}
```

`priority-status` exposes `batch_class` and the immutable device rows for each batch. The collector writes a temporary file, atomically renames it, invokes the CLI, accepts success only for exit code 0 plus valid JSON, and may safely replay the same command.

## Failure Handling

- No running parent device: reject the import without creating a pool round or batch.
- Multiple/no active ordinary parent rounds: retain existing rejection/replay behavior.
- Input mutation, malformed rows, or identity conflict: retain atomic rejection.
- Process restart: leases expire normally; the live batch remains the first scheduled class.
- Pausing a participant after submission: its assignment remains durable and the live barrier waits for it; operators can resume it without recreating the batch.

## Acceptance

1. With background batch 1 unfinished and background batch 2 queued, a new live batch is claimed next after a current lease finishes.
2. Two live batches execute FIFO before background work resumes.
3. A live import with devices 4 and 6 paused snapshots only devices 1, 2, 3, and 5.
4. Replaying the import after control changes returns the same batch and original four participants.
5. After the live barrier completes, the next claim returns to the exact unfinished background batch.
6. Existing strict background barrier tests remain green.
7. Pausing one background participant lets the remaining running cohort advance after its own barrier; resuming that participant makes it claim the earliest missed wave without losing assignments.
8. Current production DB is backed up, existing Strategy B batches are reclassified, a synthetic live batch is imported, processed by active devices, and the original background checkpoint remains unchanged except for valid confirmed-visit propagation.
