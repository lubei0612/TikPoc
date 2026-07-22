# Live-Interest Priority Batches Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a machine-facing CLI that queues live-room users ahead of a running large round, completes each FIFO priority batch across every participating account, and then resumes the large round from its durable checkpoint.

**Architecture:** Parse collector JSONL or the current English follower workbook into normalized targets, create a separate priority round linked to the active ordinary round, and let the repository scheduler choose the oldest incomplete priority round before the ordinary round. Reuse the existing assignment state machine and mobile worker; add a global batch barrier and cross-round visit propagation so priority execution never weakens business verification or creates duplicate touches.

**Tech Stack:** Python 3.14, SQLite WAL, JSONL, openpyxl, argparse, pytest, Ruff, Git.

---

### Task 1: Machine Collector Input Adapter

**Files:**
- Create: `src/tikpoc/priority_importer.py`
- Test: `tests/test_priority_importer.py`

- [ ] **Step 1: Write failing JSONL and workbook tests**

Add tests requiring normalized lowercase usernames, identity precedence
`sec_uid -> real user_id -> handle`, duplicate source-line preservation, invalid
row counts, and rejection of collector-local `dom-*` IDs as platform IDs:

```python
def test_priority_jsonl_normalizes_and_deduplicates_live_users(tmp_path: Path):
    source = tmp_path / "live.jsonl"
    source.write_text(
        "\n".join(
            (
                '{"username":"@Buyer.One","user_id":"dom-0-buyer.one","source_live_id":"live-1"}',
                '{"username":"buyer.one","sec_uid":"sec-1","source_live_id":"live-1"}',
                '{"username":"BUYER.ONE","sec_uid":"sec-1","source_live_id":"live-1"}',
            )
        ),
        encoding="utf-8",
    )

    result = read_priority_targets(source, source_live_id="live-1")

    assert len(result.targets) == 1
    assert result.targets[0].identity_key == "sec:sec-1"
    assert result.targets[0].source_line_numbers == (1, 2, 3)
    assert result.skipped_duplicates == 2
```

```python
def test_priority_importer_accepts_current_english_follower_headers(tmp_path: Path):
    workbook = build_follower_workbook(
        tmp_path / "followers.xlsx",
        follower_uid="dom-0-sample",
        follower_handle="Sample",
    )

    result = read_priority_targets(workbook, source_live_id="live-2")

    assert result.targets[0].identity_key == "handle:sample"
    assert result.targets[0].profile_url == "https://www.tiktok.com/@sample"
```

- [ ] **Step 2: Run the tests and observe RED**

Run:

```bash
uv run pytest tests/test_priority_importer.py -q
```

Expected: collection fails because `tikpoc.priority_importer` does not exist.

- [ ] **Step 3: Implement the focused adapter**

Add the immutable result type to `priority_importer.py` and reuse the existing
`importer.Target` type:

```python
@dataclass(frozen=True)
class PriorityImportResult:
    targets: tuple[Target, ...]
    source_rows: int
    skipped_duplicates: int
    skipped_invalid: int
```

Implement `read_priority_targets(path, *, source_live_id)` in the new module.
Require a username, strip `@`, lowercase it, synthesize the profile URL when
missing, ignore `dom-*` as a real user ID, and use the named English workbook
headers `follower_handle`, `follower_uid`, and `follower_sec_uid`. Preserve
one-based source line numbers and never infer profile metrics from the zeroed DOM
export columns.

- [ ] **Step 4: Verify and commit Task 1**

```bash
uv run pytest tests/test_priority_importer.py -q
uv tool run ruff check src/tikpoc/priority_importer.py tests/test_priority_importer.py
uv tool run ruff format --check src/tikpoc/priority_importer.py tests/test_priority_importer.py
git diff --check
git add src/tikpoc/priority_importer.py tests/test_priority_importer.py
git commit -m "feat: parse live-interest priority targets"
```

### Task 2: Durable Priority Batch Queue

**Files:**
- Modify: `src/tikpoc/acquisition_db.py`
- Modify: `src/tikpoc/acquisition_models.py`
- Test: `tests/test_priority_batches.py`

- [ ] **Step 1: Write failing persistence and idempotency tests**

Require a priority batch linked to one active ordinary round, a fixed device
snapshot copied from that round, FIFO queue sequence, immutable input checksum,
and idempotent replay:

```python
def test_create_priority_batch_snapshots_round_devices_and_is_idempotent(tmp_path):
    repository, ordinary_round = seeded_ordinary_round(tmp_path, devices=("d1", "d2"))
    pool = repository.import_pool("live.jsonl", "a" * 64, (target("buyer"),))

    first = repository.create_priority_batch(
        batch_id="priority-1",
        parent_round_id=ordinary_round,
        pool_id=pool.pool_id,
        source_live_id="live-1",
        source_checksum="a" * 64,
        device_seeds={"d1": "p1", "d2": "p2"},
    )
    second = repository.create_priority_batch(
        batch_id="priority-1",
        parent_round_id=ordinary_round,
        pool_id=pool.pool_id,
        source_live_id="live-1",
        source_checksum="a" * 64,
        device_seeds={"d1": "p1", "d2": "p2"},
    )

    assert first == second
    assert repository.priority_batch_device_ids("priority-1") == ("d1", "d2")
    assert repository.priority_queue(ordinary_round)[0].batch_id == "priority-1"
```

Also require a different payload under the same batch ID to raise `ValueError`
without mutating existing rows.

- [ ] **Step 2: Run RED**

```bash
uv run pytest tests/test_priority_batches.py -q
```

Expected: `create_priority_batch` and queue models are missing.

- [ ] **Step 3: Add schema and repository operations**

Create `priority_batches` with:

```sql
batch_id TEXT PRIMARY KEY,
parent_round_id TEXT NOT NULL,
priority_round_id TEXT NOT NULL UNIQUE,
source_live_id TEXT NOT NULL,
source_checksum TEXT NOT NULL,
queue_sequence INTEGER NOT NULL UNIQUE,
state TEXT NOT NULL CHECK(state IN ('queued','running','barrier','completed')),
created_at_ms INTEGER NOT NULL,
completed_at_ms INTEGER
```

Reuse `target_pools`, `exposure_rounds`, `round_device_seeds`, and
`round_assignments` for the priority round. Generate per-device order keys with
the existing deterministic round helper and the supplied distinct seeds. Reject
a parent round that is missing, terminal, or has no devices.

- [ ] **Step 4: Verify and commit Task 2**

```bash
uv run pytest tests/test_priority_batches.py tests/test_rounds.py -q
uv tool run ruff check src/tikpoc/acquisition_db.py src/tikpoc/acquisition_models.py tests/test_priority_batches.py
uv tool run ruff format --check src/tikpoc/acquisition_db.py src/tikpoc/acquisition_models.py tests/test_priority_batches.py
git diff --check
git add src/tikpoc/acquisition_db.py src/tikpoc/acquisition_models.py tests/test_priority_batches.py
git commit -m "feat: persist fifo priority batches"
```

### Task 3: FIFO Scheduler And All-Device Barrier

**Files:**
- Modify: `src/tikpoc/acquisition_db.py`
- Test: `tests/test_priority_scheduler.py`

- [ ] **Step 1: Write failing scheduler tests**

Add a local `seeded_priority_queue(tmp_path)` helper that returns the repository,
ordinary round ID, and the two FIFO priority-round IDs. Cover these exact cases:

```python
def test_scheduler_never_claims_second_batch_before_first_barrier(tmp_path):
    repository, ordinary, priority_one, priority_two = seeded_priority_queue(tmp_path)

    first = repository.claim_scheduled_assignment(
        ordinary, "d1", "worker-1", now_ms=1_000
    )
    assert first is not None
    assert first.round_id == priority_one

    complete_confirmed(repository, first, "worker-1", now_ms=1_100)
    waiting = repository.claim_scheduled_assignment(
        ordinary, "d1", "worker-1", now_ms=1_200
    )
    assert waiting is None
    assert repository.priority_batch(priority_two).state == "queued"

    finish_priority_round(repository, priority_one, now_ms=1_300)
    second = repository.claim_scheduled_assignment(
        ordinary, "d1", "worker-1", now_ms=1_400
    )
    assert second is not None
    assert second.round_id == priority_two
```

Also add complete tests named
`test_scheduler_finishes_current_lease_then_prefers_oldest_priority_batch`,
`test_fast_device_waits_while_another_device_has_priority_work`, and
`test_scheduler_resumes_ordinary_round_after_all_priority_assignments_terminal`.
The first creates an already-leased ordinary assignment before adding the
priority batch, asserts the lease owner and expiry are unchanged, finishes it,
and asserts its next claim belongs to priority batch 1. The barrier test finishes
all d1 work, leaves one d2 assignment pending, and asserts d1 receives `None`.
The resume test finishes both priority rounds and asserts the next d1 claim has
the ordinary round ID.

- [ ] **Step 2: Run RED**

```bash
uv run pytest tests/test_priority_scheduler.py -q
```

Expected: `claim_scheduled_assignment` is missing.

- [ ] **Step 3: Implement transactional scheduling**

Extract the existing claim SQL into a private connection-scoped helper, then add
`claim_scheduled_assignment(self, parent_round_id, device_id, owner_id, *,
now_ms, worker_account_id=None, worker_fence_token=None) -> RoundAssignment |
None`.

Inside one `BEGIN IMMEDIATE`, select the smallest incomplete `queue_sequence`.
If it exists, claim only from its priority round. When the calling device has no
remaining assignment but another device does, set the batch to `barrier` and
return `None`. When all assignments are terminal, mark the batch completed,
advance to the next FIFO batch in the same transaction, and only select the
ordinary round when no priority batch remains.

- [ ] **Step 4: Verify and commit Task 3**

```bash
uv run pytest tests/test_priority_scheduler.py tests/test_priority_batches.py tests/test_rounds.py -q
uv tool run ruff check src/tikpoc/acquisition_db.py tests/test_priority_scheduler.py
uv tool run ruff format --check src/tikpoc/acquisition_db.py tests/test_priority_scheduler.py
git diff --check
git add src/tikpoc/acquisition_db.py tests/test_priority_scheduler.py
git commit -m "feat: schedule fifo priority work"
```

### Task 4: Cross-Batch Coverage Without Duplicate Touches

**Files:**
- Modify: `src/tikpoc/acquisition_db.py`
- Test: `tests/test_priority_coverage.py`

- [x] **Step 1: Write failing coverage propagation tests**

```python
def test_priority_completion_satisfies_only_matching_parent_device(tmp_path):
    repository, ordinary, priority = seeded_matching_identity_rounds(tmp_path)
    assignment = repository.claim_scheduled_assignment(
        ordinary, "d1", "worker-1", now_ms=1_000
    )
    complete_confirmed(repository, assignment, "worker-1", now_ms=1_100)

    parent_d1 = repository.assignment(ordinary, assignment.identity_key, "d1")
    parent_d2 = repository.assignment(ordinary, assignment.identity_key, "d2")
    assert parent_d1.phase == AssignmentPhase.COMPLETED
    assert parent_d1.visit_confirmed_at_ms == 1_100
    assert parent_d2.phase == AssignmentPhase.PENDING
    assert parent_d2.visit_confirmed_at_ms is None
```

Also add complete tests named
`test_existing_ordinary_visit_satisfies_matching_priority_assignment` and
`test_skipped_priority_target_does_not_create_confirmed_ordinary_coverage`.
Assert timestamps and phase history details. Require an audit detail such as
`{"reason":"satisfied_by_priority","source_assignment_id":123}`. The skipped
case must finish a priority assignment with terminal `skipped`, then assert both
the parent phase and visit timestamp remain unchanged.

- [x] **Step 2: Run RED**

```bash
uv run pytest tests/test_priority_coverage.py -q
```

Expected: duplicate identities remain independent and the assertions fail.

- [x] **Step 3: Implement account-scoped propagation**

At priority batch creation, find confirmed visits for the same identity and
device in the parent round and mark only those priority assignments completed
with copied visit evidence. When a priority assignment reaches completed with a
confirmed visit, atomically mark the matching pending parent assignment
completed, copy the confirmed timestamp, and write phase history referencing the
priority assignment. Never propagate `skipped`, missing visits, or uncertain
action state.

- [x] **Step 4: Verify and commit Task 4**

```bash
uv run pytest tests/test_priority_coverage.py tests/test_mobile_worker.py -q
uv tool run ruff check src/tikpoc/acquisition_db.py tests/test_priority_coverage.py
uv tool run ruff format --check src/tikpoc/acquisition_db.py tests/test_priority_coverage.py
git diff --check
git add src/tikpoc/acquisition_db.py tests/test_priority_coverage.py
git commit -m "feat: deduplicate priority coverage"
```

### Task 5: Worker Integration And CLI Commands

**Files:**
- Modify: `src/tikpoc/fleet_runtime.py`
- Modify: `src/tikpoc/cli.py`
- Create: `src/tikpoc/priority_service.py`
- Test: `tests/test_fleet_runtime.py`
- Test: `tests/test_priority_cli.py`

- [x] **Step 1: Write failing worker and CLI tests**

Require `run_device_worker` to call `claim_scheduled_assignment` using the
ordinary round ID, and add CLI acceptance tests for:

```text
tikpoc priority-import --db DB --devices DEVICES --file INPUT --source-live LIVE_ID
tikpoc priority-status --db DB
```

The import command prints one JSON object containing `batch_id`,
`parent_round_id`, `unique_targets`, `skipped_duplicates`,
`skipped_invalid`, and `device_count`. The status command prints ordered
batches with per-device pending/completed/skipped counts and the current ordinary
checkpoint. Replaying the import prints the same batch ID.

- [x] **Step 2: Run RED**

```bash
uv run pytest tests/test_priority_cli.py tests/test_fleet_runtime.py -q
```

Expected: argparse rejects the new commands and the runtime still calls
`claim_next_assignment`.

- [x] **Step 3: Implement the service, CLI, and runtime switch**

`PriorityBatchService.import_batch()` must:

1. resolve and validate the input path;
2. read targets through `read_priority_targets`;
3. locate exactly one active ordinary round;
4. import an immutable target pool by SHA-256;
5. derive distinct per-device seeds from batch ID and device ID;
6. create or replay the durable priority batch;
7. return only redacted identifiers and counts.

Change only the worker claim call; do not alter `MobileAssignmentWorker`, action
probabilities, quotas, retry handling, or verification behavior.

- [x] **Step 4: Verify and commit Task 5**

```bash
uv run pytest tests/test_priority_cli.py tests/test_fleet_runtime.py tests/test_mobile_worker.py -q
uv tool run ruff check src/tikpoc/priority_service.py src/tikpoc/cli.py src/tikpoc/fleet_runtime.py tests/test_priority_cli.py tests/test_fleet_runtime.py
uv tool run ruff format --check src/tikpoc/priority_service.py src/tikpoc/cli.py src/tikpoc/fleet_runtime.py tests/test_priority_cli.py tests/test_fleet_runtime.py
git diff --check
git add src/tikpoc/priority_service.py src/tikpoc/cli.py src/tikpoc/fleet_runtime.py tests/test_priority_cli.py tests/test_fleet_runtime.py
git commit -m "feat: expose live priority batch cli"
```

### Task 6: Recovery Acceptance, AI Contract, And Full Regression

**Files:**
- Create: `docs/priority-live-batch-cli.md`
- Modify: `docs/tikpoc-business-logic.md`
- Test: `tests/test_priority_recovery.py`

- [x] **Step 1: Write the end-to-end recovery test**

Create a three-device ordinary round, claim one ordinary assignment, submit two
priority batches, finish the already-claimed ordinary assignment, partially
complete priority batch 1, expire one worker lease, recreate the repository, and
continue. Assert this order:

```text
current ordinary assignment
priority batch 1 on all devices
priority batch 2 on all devices
remaining ordinary assignments
```

Also assert zero duplicate confirmed visits per identity/device and preserved
ordinary attempt counts.

- [x] **Step 2: Run RED, then implement only missing recovery glue**

```bash
uv run pytest tests/test_priority_recovery.py -q
```

Expected: the test identifies any missing stale-lease or queue-state transition.
Add the smallest repository transition needed; do not add another scheduler.

- [x] **Step 3: Document the external AI contract**

Document the JSONL schema, example commands, idempotent replay, FIFO behavior,
status output, exit codes, and the rule that a collector writes the file fully
before invoking the CLI. Add the priority scheduling paragraph to the business
logic document without changing eligibility or interaction rules.

- [x] **Step 4: Run all verification and independent reviews**

```bash
uv run pytest -q
uv tool run ruff check src tests
uv tool run ruff format --check src/tikpoc/priority_importer.py src/tikpoc/priority_service.py src/tikpoc/acquisition_models.py src/tikpoc/acquisition_db.py src/tikpoc/fleet_runtime.py src/tikpoc/cli.py tests/test_priority_importer.py tests/test_priority_batches.py tests/test_priority_scheduler.py tests/test_priority_coverage.py tests/test_priority_cli.py tests/test_priority_recovery.py
git diff --check
```

Run an independent specification review against
`docs/superpowers/specs/2026-07-22-priority-live-batch-design.md`, fix every
Critical/Important finding with a red-green cycle, repeat the specification
review, then run and repeat an independent code-quality review.

- [x] **Step 5: Commit and push**

```bash
git add docs/priority-live-batch-cli.md docs/tikpoc-business-logic.md tests/test_priority_recovery.py src/tikpoc/acquisition_db.py
git commit -m "docs: verify live priority batch recovery"
git push origin feat/web-lead-conversion
```

The production database remains paused until an isolated database passes the
end-to-end test and a short six-device canary proves FIFO switching, all-device
coverage, and ordinary-round resume from the prior checkpoint.
