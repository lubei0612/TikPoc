# Live Interrupt Priority Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a durable live-interrupt lane that preempts preloaded Strategy B waves, snapshots only running devices, and resumes the exact background checkpoint after the interrupt.

**Architecture:** Persist an immutable `batch_class` on every priority batch and order scheduled claims by class then FIFO sequence. Keep full-fleet background waves unchanged, while the import service derives a nonempty live participant subset from operator controls and replays the original participant snapshot idempotently.

**Tech Stack:** Python 3.13, SQLite, pytest, Ruff, existing TikPoc CLI and acquisition repository.

---

## File Map

- `src/tikpoc/acquisition_models.py`: declare the immutable batch-class type and expose it on `PriorityBatch`.
- `src/tikpoc/acquisition_db.py`: migrate/persist batch class, validate participant subsets, and schedule live interrupts before background waves.
- `src/tikpoc/priority_service.py`: snapshot running devices and preserve participants on idempotent replay.
- `src/tikpoc/cli.py`: include `batch_class` in import JSON through the summary model.
- `tests/test_priority_batches.py`: persistence, migration, and participant validation.
- `tests/test_priority_scheduler.py`: preemption, live FIFO, barrier, and background resume.
- `tests/test_priority_service.py`: active-device snapshot and replay after controls change.
- `tests/test_priority_cli.py`: machine-output contract.
- `docs/priority-live-batch-cli.md`, `README.md`, `docs/tikpoc-business-logic.md`: operator and collector contract.

### Task 1: Persist Batch Class and Participant Rules

**Files:**
- Modify: `src/tikpoc/acquisition_models.py`
- Modify: `src/tikpoc/acquisition_db.py`
- Modify: `tests/test_priority_batches.py`

- [ ] **Step 1: Write failing persistence tests**

Add tests that create `batch_class="live_interrupt"` with a one-device subset of a two-device parent and assert the returned row exposes that class and participant; add a background-subset test expecting `ValueError`; add a migration test that opens a legacy schema and asserts the new column is present with `live_interrupt`.

- [ ] **Step 2: Verify RED**

Run:

```bash
uv run pytest tests/test_priority_batches.py -q
```

Expected: failures because `PriorityBatch` and `create_priority_batch` do not accept or expose `batch_class` and the schema lacks the column.

- [ ] **Step 3: Implement minimal persistence**

Add:

```python
class PriorityBatchClass(str, Enum):
    BACKGROUND = "background"
    LIVE_INTERRUPT = "live_interrupt"
```

Add `batch_class: PriorityBatchClass` to `PriorityBatch`. Create/migrate `priority_batches.batch_class TEXT NOT NULL DEFAULT 'live_interrupt'` with a two-value check. Extend `create_priority_batch(..., batch_class=PriorityBatchClass.LIVE_INTERRUPT)` so background seeds equal the parent set and live seeds are a nonempty subset. Include `batch_class` in idempotency comparison, digest payload, INSERT, and row mapping.

- [ ] **Step 4: Verify GREEN and lint**

```bash
uv run pytest tests/test_priority_batches.py -q
uv tool run ruff check src/tikpoc/acquisition_models.py src/tikpoc/acquisition_db.py tests/test_priority_batches.py
uv tool run ruff format --check src/tikpoc/acquisition_models.py src/tikpoc/acquisition_db.py tests/test_priority_batches.py
```

- [ ] **Step 5: Commit**

```bash
git add src/tikpoc/acquisition_models.py src/tikpoc/acquisition_db.py tests/test_priority_batches.py
git commit -m "feat: persist priority batch classes"
```

### Task 2: Schedule Live Interrupts Before Background Waves

**Files:**
- Modify: `src/tikpoc/acquisition_db.py`
- Modify: `tests/test_priority_scheduler.py`

- [ ] **Step 1: Write failing scheduler tests**

Add tests proving: an unfinished background batch yields to a later live batch after the current lease; two live batches remain FIFO; a device outside the live participant snapshot receives no live assignment; completing the live participant barrier returns the next claim to the original unfinished background batch; existing strict background barrier behavior remains unchanged.

- [ ] **Step 2: Verify RED**

```bash
uv run pytest tests/test_priority_scheduler.py -q
```

Expected: the first post-import claim still selects the older background batch.

- [ ] **Step 3: Implement minimal class-aware selection**

Change the incomplete-batch query to:

```sql
ORDER BY
  CASE batch_class WHEN 'live_interrupt' THEN 0 ELSE 1 END,
  queue_sequence
```

When the calling device is not in a selected live round, return no live claim rather than touching background work. Preserve completion, barrier, lease, and ordinary-round behavior.

- [ ] **Step 4: Verify GREEN and regression**

```bash
uv run pytest tests/test_priority_scheduler.py tests/test_priority_batches.py tests/test_rounds.py -q
uv tool run ruff check src/tikpoc/acquisition_db.py tests/test_priority_scheduler.py
uv tool run ruff format --check src/tikpoc/acquisition_db.py tests/test_priority_scheduler.py
```

- [ ] **Step 5: Commit**

```bash
git add src/tikpoc/acquisition_db.py tests/test_priority_scheduler.py
git commit -m "feat: preempt background waves with live batches"
```

### Task 3: Snapshot Active Devices and Preserve Replay Participants

**Files:**
- Modify: `src/tikpoc/priority_service.py`
- Modify: `src/tikpoc/acquisition_db.py`
- Modify: `tests/test_priority_service.py`

- [ ] **Step 1: Write failing service tests**

Create a two-device parent, persist `d1=running` and `d2=paused`, import one live file, and assert only `d1` participates. Change controls and replay the same import; assert the same batch ID and original device count. Add a test rejecting an import when every parent device is paused.

- [ ] **Step 2: Verify RED**

```bash
uv run pytest tests/test_priority_service.py -q
```

Expected: current service either requires every configured device or snapshots both.

- [ ] **Step 3: Implement active snapshot and replay lookup**

Add a repository query returning parent devices whose control is absent or `running`. In `import_batch`, first validate full fleet config against the parent, then return an existing `(parent, source_live_id, checksum)` batch before recomputing participants. For new imports, reject an empty running set, generate seeds only for running devices, and create `batch_class=LIVE_INTERRUPT`.

- [ ] **Step 4: Verify GREEN and lint**

```bash
uv run pytest tests/test_priority_service.py tests/test_priority_batches.py tests/test_priority_scheduler.py -q
uv tool run ruff check src/tikpoc/priority_service.py src/tikpoc/acquisition_db.py tests/test_priority_service.py
uv tool run ruff format --check src/tikpoc/priority_service.py src/tikpoc/acquisition_db.py tests/test_priority_service.py
```

- [ ] **Step 5: Commit**

```bash
git add src/tikpoc/priority_service.py src/tikpoc/acquisition_db.py tests/test_priority_service.py
git commit -m "feat: snapshot active live batch participants"
```

### Task 4: Expose the Machine Contract and Document Operations

**Files:**
- Modify: `src/tikpoc/priority_service.py`
- Modify: `tests/test_priority_cli.py`
- Modify: `docs/priority-live-batch-cli.md`
- Modify: `docs/tikpoc-business-logic.md`
- Modify: `README.md`

- [ ] **Step 1: Write failing CLI contract test**

Assert successful `priority-import` JSON contains `"batch_class":"live_interrupt"` and the snapshotted `device_count`, while `priority-status` exposes each batch class.

- [ ] **Step 2: Verify RED**

```bash
uv run pytest tests/test_priority_cli.py -q
```

- [ ] **Step 3: Implement output and docs**

Add `batch_class` to `PriorityImportSummary` and status rows. Document live preemption, running-device snapshots, replay idempotency, participant barriers, and background checkpoint resume. Keep the existing input and exit-code contract unchanged.

- [ ] **Step 4: Verify focused and full regression**

```bash
uv run pytest tests/test_priority_cli.py tests/test_priority_service.py tests/test_priority_scheduler.py tests/test_priority_batches.py -q
uv run pytest -q
uv tool run ruff check src tests
uv tool run ruff format --check src tests
git diff --check
```

- [ ] **Step 5: Commit**

```bash
git add src/tikpoc/priority_service.py tests/test_priority_cli.py README.md docs/priority-live-batch-cli.md docs/tikpoc-business-logic.md
git commit -m "docs: publish live interrupt cli contract"
```

### Task 5: Production Migration and Live Canary

**Files:**
- Runtime only: `/Users/Shared/TikPoc/tikpoc.db`
- Runtime only: `/Users/Shared/TikPoc/checkpoints/`

- [ ] **Step 1: Stop at a durable boundary and back up**

Pause running devices, stop the fleet PTY after active leases settle, and copy the database to a timestamped checkpoint. Record current background batch, per-device phases, controls, and source checksums.

- [ ] **Step 2: Migrate and classify existing Strategy B waves**

Run repository migration, then in one transaction set `batch_class='background'` only for the existing `source_live_id LIKE 'strategy-b-%'` rows. Verify counts and preserve queue sequences.

- [ ] **Step 3: Run a synthetic collector canary**

With devices 4 and 6 paused, atomically publish a small synthetic JSONL through `priority-import`. Verify its class is live, participants are exactly devices 1, 2, 3, and 5, it becomes the scheduled batch before background sequence 4, and repeated import returns the same batch ID.

- [ ] **Step 4: Resume and verify visible execution**

Resume devices 1, 2, 3, and 5. Verify confirmed visits and action outcomes for the canary, completion across all four participants, and return to the unchanged Strategy B sequence-4 checkpoint. Remove no durable evidence.

- [ ] **Step 5: Final status and GitHub update**

Run fresh status, focused/full verification, push the feature branch, and report the exact collector command plus current runtime checkpoint.
