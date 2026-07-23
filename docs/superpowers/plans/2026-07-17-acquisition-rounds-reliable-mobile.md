# Acquisition Rounds And Reliable Mobile Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build restart-safe target pools, repeated seven-device exposure rounds, shared qualification snapshots, independent quota-controlled outcomes, and visibly verified MYT mobile execution.

**Architecture:** New acquisition tables and services run beside the legacy `tasks` compatibility path until live migration is accepted. A round materializes one assignment per target and device with deterministic per-device ordering. A first-visitor lease publishes one round-scoped metric snapshot, while every device independently persists and verifies its own outcome through a durable phase machine.

**Tech Stack:** Python 3.12, SQLite WAL, standard-library CSV/HTTP/socket/subprocess modules, Appium Python Client, UiAutomator2, ADB, MYT host SDK, PyYAML, pytest, Ruff.

---

## File Map

- Modify `src/tikpoc/importer.py`: stable identity precedence and duplicate lineage for the existing comment CSV.
- Create `src/tikpoc/acquisition_models.py`: target-pool, round, snapshot, outcome, phase, and assignment value types.
- Create `src/tikpoc/acquisition_db.py`: acquisition-only migrations and transactional repository operations.
- Create `src/tikpoc/rounds.py`: deterministic order keys, round materialization, and spacing configuration.
- Create `src/tikpoc/outcome_planner.py`: independent seeded outcome selection and fixed-hour windows.
- Create `src/tikpoc/mobile_worker.py`: durable assignment orchestration, recovery, and completion semantics.
- Modify `src/tikpoc/device.py`: semantic condition waits and verified `repost` behavior.
- Create `src/tikpoc/myt.py`: minimal typed MYT host SDK discovery client.
- Create `src/tikpoc/proxy_relay.py`: source-allowlisted LAN relay to the Mac loopback proxy.
- Create `src/tikpoc/fleet.py`: device/account configuration, health, and one-worker-per-device execution.
- Create `src/tikpoc/capacity.py`: measured latency, slowest-device projection, coverage, and promotion verdict.
- Modify `src/tikpoc/cli.py`: target-pool, round, fleet, retry, and capacity commands.
- Modify `config/devices.example.yaml`: MYT, ADB, Appium, account, relay, and seed fields.
- Create `tests/test_acquisition_db.py`: schema, transaction, lease, coverage, and recovery behavior.
- Create `tests/test_rounds.py`: exact materialization, ordering, restart, and spacing behavior.
- Create `tests/test_outcome_planner.py`: independent draws, quota fallback, and hour rollover.
- Create `tests/test_mobile_worker.py`: state-machine, reconciliation, deferral, and completion behavior.
- Create `tests/test_myt.py`: MYT response parsing and endpoint health.
- Create `tests/test_proxy_relay.py`: source allowlist and byte forwarding.
- Create `tests/test_fleet.py`: mapping validation and process ownership.
- Create `tests/test_capacity.py`: slowest-device capacity and strict gate behavior.
- Modify `tests/test_importer.py`, `tests/test_appium_device.py`, `tests/test_cli.py`, and `tests/fakes.py`.
- Create `docs/mobile-fleet-runbook.md`: two-device calibration, recovery, and seven-device operation.

### Task 1: Stable CSV Identity And Duplicate Lineage

**Files:**
- Modify: `src/tikpoc/importer.py`
- Modify: `tests/test_importer.py`

- [x] **Step 1: Write failing identity-precedence tests**

```python
def test_comment_export_deduplicates_by_sec_uid_before_user_id(tmp_path: Path) -> None:
    path = write_comment_csv(
        tmp_path,
        [
            comment_row(user_id="user-1", sec_uid="sec-shared", handle="first"),
            comment_row(user_id="user-2", sec_uid="sec-shared", handle="second"),
        ],
    )

    result = read_targets(path)

    assert len(result.targets) == 1
    assert result.targets[0].identity_key == "sec:sec-shared"
    assert result.targets[0].source_line_numbers == (2, 3)
    assert result.skipped_duplicates == 1


def test_comment_export_falls_back_to_user_id_when_sec_uid_is_empty(tmp_path: Path) -> None:
    path = write_comment_csv(
        tmp_path,
        [comment_row(user_id="user-1", sec_uid="", handle="buyer")],
    )
    target = read_targets(path).targets[0]
    assert target.identity_key == "uid:user-1"
```

`write_comment_csv` emits the current 15-column Chinese header and writes with
`utf-8-sig`, retaining BOM coverage.

- [x] **Step 2: Run the focused importer tests**

Run: `uv run pytest tests/test_importer.py -q`

Expected: FAIL because `Target` has no `identity_key` or lineage.

- [x] **Step 3: Extend the structured import result**

```python
@dataclass(frozen=True)
class Target:
    target_id: str
    username: str
    profile_url: str
    source_video_id: str
    sec_uid: str
    profile_metrics: ProfileMetrics | None = None
    private_account: bool | None = None
    identity_key: str = ""
    source_line_numbers: tuple[int, ...] = ()


def target_identity_key(*, sec_uid: str, target_id: str, username: str) -> str:
    if normalized := sec_uid.strip():
        return f"sec:{normalized}"
    if normalized := target_id.strip():
        return f"uid:{normalized}"
    if normalized := username.strip().removeprefix("@").lower():
        return f"handle:{normalized}"
    raise ValueError("target identity is empty")
```

Build a dictionary keyed by `identity_key`. On a duplicate, replace the stored
target with `dataclasses.replace(existing, source_line_numbers=existing.source_line_numbers + (line_number,))`.

- [x] **Step 4: Run importer regression**

Run: `uv run pytest tests/test_importer.py -q`

Expected: PASS.

- [x] **Step 5: Commit the identity contract**

```bash
git add src/tikpoc/importer.py tests/test_importer.py
git commit -m "feat: preserve stable target identity lineage"
```

### Task 2: Acquisition Repository And Immutable Target Pools

**Files:**
- Create: `src/tikpoc/acquisition_models.py`
- Create: `src/tikpoc/acquisition_db.py`
- Create: `tests/test_acquisition_db.py`

- [x] **Step 1: Write failing pool idempotency tests**

```python
def test_import_pool_is_idempotent_by_source_checksum(tmp_path: Path) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db")
    repository.migrate()
    targets = (
        Target("u1", "buyer", "https://www.tiktok.com/@buyer", "v1", "s1", identity_key="sec:s1", source_line_numbers=(2, 4)),
    )

    checksum = "a" * 64
    first = repository.import_pool("comments.csv", checksum, targets)
    second = repository.import_pool("comments.csv", checksum, targets)

    assert second.pool_id == first.pool_id
    assert second.unique_targets == 1
    assert second.source_rows == 2
    assert repository.pool_targets(first.pool_id)[0].identity_key == "sec:s1"


def test_existing_pool_is_immutable(tmp_path: Path) -> None:
    repository = migrated_repository(tmp_path)
    checksum = "a" * 64
    repository.import_pool("comments.csv", checksum, (pool_target("s1"),))
    with pytest.raises(ValueError, match="checksum already has different content"):
        repository.import_pool("other.csv", checksum, (pool_target("s2"),))
```

- [x] **Step 2: Run the repository test**

Run: `uv run pytest tests/test_acquisition_db.py -q`

Expected: FAIL because `tikpoc.acquisition_db` and its models do not exist.

- [x] **Step 3: Define focused acquisition value types**

```python
class AssignmentPhase(StrEnum):
    PENDING = "pending"
    PROFILE_OPENING = "profile_opening"
    IDENTITY_CONFIRMED = "identity_confirmed"
    WAITING_SNAPSHOT = "waiting_snapshot"
    VIDEO_OPENING = "video_opening"
    VIDEO_CONFIRMED = "video_confirmed"
    QUOTA_RESERVED = "quota_reserved"
    ACTION_EXECUTING = "action_executing"
    ACTION_RECONCILING = "action_reconciling"
    DEFERRED = "deferred"
    COMPLETED = "completed"


class OutcomeKind(StrEnum):
    LIKE = "like"
    FAVORITE = "favorite"
    REPOST = "repost"
    TRACE = "trace"


@dataclass(frozen=True)
class PoolImport:
    pool_id: str
    unique_targets: int
    source_rows: int
```

Also define `PoolTarget`, `ExposureRound`, `RoundAssignment`,
`ProfileSnapshot`, and `ActionPlan` with explicit identifiers and timestamps.

- [x] **Step 4: Implement acquisition-only migrations**

`AcquisitionRepository.migrate()` creates these initial tables in one WAL-mode
transaction:

```sql
CREATE TABLE IF NOT EXISTS target_pools (
    pool_id TEXT PRIMARY KEY,
    source_name TEXT NOT NULL,
    source_checksum TEXT NOT NULL UNIQUE,
    unique_targets INTEGER NOT NULL,
    source_rows INTEGER NOT NULL,
    created_at_ms INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS pool_targets (
    pool_id TEXT NOT NULL,
    identity_key TEXT NOT NULL,
    target_id TEXT NOT NULL,
    sec_uid TEXT NOT NULL,
    username TEXT NOT NULL,
    profile_url TEXT NOT NULL,
    source_video_id TEXT NOT NULL,
    source_line_numbers_json TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    PRIMARY KEY(pool_id, identity_key),
    FOREIGN KEY(pool_id) REFERENCES target_pools(pool_id)
);
```

Require a 64-character lowercase hexadecimal source SHA-256 and derive `pool_id`
as `pool-` followed by its first 20 characters.
Validate an existing checksum against target count and identities before returning
the existing pool.

- [x] **Step 5: Run repository and importer tests**

Run: `uv run pytest tests/test_acquisition_db.py tests/test_importer.py -q`

Expected: PASS.

- [x] **Step 6: Commit immutable pools**

```bash
git add src/tikpoc/acquisition_models.py src/tikpoc/acquisition_db.py tests/test_acquisition_db.py
git commit -m "feat: persist immutable acquisition target pools"
```

### Task 3: Exposure Rounds And Deterministic Device Ordering

**Files:**
- Create: `src/tikpoc/rounds.py`
- Modify: `src/tikpoc/acquisition_db.py`
- Create: `tests/test_rounds.py`
- Modify: `tests/test_acquisition_db.py`

- [x] **Step 1: Write failing materialization and order tests**

```python
def test_round_materializes_every_target_for_every_device(tmp_path: Path) -> None:
    repository, pool_id = repository_with_targets(tmp_path, count=3)
    round_id = create_exposure_round(
        repository,
        pool_id=pool_id,
        device_seeds={"phone-01": "seed-a", "phone-02": "seed-b"},
        starts_at_ms=1_000,
    )

    assert repository.assignment_count(round_id) == 6
    first_order = repository.device_target_order(round_id, "phone-01")
    second_order = repository.device_target_order(round_id, "phone-02")
    assert set(first_order) == set(second_order)
    assert first_order != second_order


def test_order_is_stable_across_repository_restart(tmp_path: Path) -> None:
    repository, round_id = repository_with_round(tmp_path, target_count=20)
    before = repository.device_target_order(round_id, "phone-01")
    reopened = AcquisitionRepository(repository.path)
    reopened.migrate()
    assert reopened.device_target_order(round_id, "phone-01") == before
```

- [x] **Step 2: Run the new round tests**

Run: `uv run pytest tests/test_rounds.py tests/test_acquisition_db.py -q`

Expected: FAIL because exposure-round tables and APIs are absent.

- [x] **Step 3: Add round and assignment schema**

```sql
CREATE TABLE IF NOT EXISTS exposure_rounds (
    round_id TEXT PRIMARY KEY,
    pool_id TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    starts_at_ms INTEGER NOT NULL,
    min_inter_device_gap_ms INTEGER NOT NULL DEFAULT 900000,
    min_repeat_gap_ms INTEGER NOT NULL DEFAULT 72000000,
    created_at_ms INTEGER NOT NULL,
    FOREIGN KEY(pool_id) REFERENCES target_pools(pool_id)
);
CREATE TABLE IF NOT EXISTS round_assignments (
    assignment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id TEXT NOT NULL,
    identity_key TEXT NOT NULL,
    device_id TEXT NOT NULL,
    order_key TEXT NOT NULL,
    phase TEXT NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at_ms INTEGER NOT NULL DEFAULT 0,
    visit_confirmed_at_ms INTEGER,
    completed_at_ms INTEGER,
    last_error_code TEXT,
    lease_owner TEXT,
    lease_expires_at_ms INTEGER NOT NULL DEFAULT 0,
    UNIQUE(round_id, identity_key, device_id)
);
CREATE INDEX IF NOT EXISTS round_assignment_claim_idx
ON round_assignments(round_id, device_id, phase, next_attempt_at_ms, order_key);
```

- [x] **Step 4: Implement stable order keys and round creation**

```python
def device_order_key(round_id: str, device_seed: str, identity_key: str) -> str:
    payload = "\0".join((round_id, device_seed, identity_key)).encode()
    return hashlib.sha256(payload).hexdigest()
```

`create_exposure_round` inserts the round and every assignment in one
transaction. Reject duplicate device IDs, blank seeds, a round earlier than the
pool's previous repeat-gap boundary, or a second active round for the pool.

- [x] **Step 5: Implement spaced claims and lease recovery**

`claim_next_assignment(round_id, device_id, owner_id, now_ms)` selects the first
eligible order key and excludes targets with an unexpired assignment lease or a
confirmed visit by another device inside `min_inter_device_gap_ms`. It atomically
sets a 120-second assignment lease. `recover_expired_assignment_leases(now_ms)`
returns interrupted work to `pending` or its prior `deferred` schedule.

- [x] **Step 6: Run round and repository tests**

Run: `uv run pytest tests/test_rounds.py tests/test_acquisition_db.py -q`

Expected: PASS.

- [x] **Step 7: Commit exposure rounds**

```bash
git add src/tikpoc/rounds.py src/tikpoc/acquisition_db.py tests/test_rounds.py tests/test_acquisition_db.py
git commit -m "feat: schedule deterministic exposure rounds"
```

### Task 4: First-Visitor Qualification Snapshots

**Files:**
- Modify: `src/tikpoc/acquisition_db.py`
- Modify: `src/tikpoc/acquisition_models.py`
- Modify: `tests/test_acquisition_db.py`
- Create: `tests/test_qualification.py`

- [x] **Step 1: Write failing exclusive-snapshot tests**

```python
def test_only_one_device_owns_snapshot_lease(tmp_path: Path) -> None:
    repository, round_id, identity_key = repository_with_one_target(tmp_path)
    first = repository.claim_snapshot_lease(round_id, identity_key, "phone-01", now_ms=1_000, ttl_ms=30_000)
    second = repository.claim_snapshot_lease(round_id, identity_key, "phone-02", now_ms=1_001, ttl_ms=30_000)
    assert first is True
    assert second is False


def test_completed_snapshot_is_shared_but_action_plan_is_not(tmp_path: Path) -> None:
    repository, round_id, identity_key = repository_with_one_target(tmp_path)
    repository.claim_snapshot_lease(round_id, identity_key, "phone-01", now_ms=1_000, ttl_ms=30_000)
    repository.publish_profile_snapshot(
        round_id,
        identity_key,
        device_id="phone-01",
        observed_username="buyer",
        metrics=ProfileMetrics(following=20, followers=10, posts=5),
        private_account=False,
        observed_at_ms=2_000,
    )
    snapshot = repository.profile_snapshot(round_id, identity_key)
    assert snapshot is not None and snapshot.eligible is True
    assert repository.action_plan(round_id, identity_key, "phone-02") is None
```

- [x] **Step 2: Run the snapshot tests**

Run: `uv run pytest tests/test_qualification.py tests/test_acquisition_db.py -q`

Expected: FAIL because snapshot lease operations are absent.

- [x] **Step 3: Add lease and snapshot tables**

```sql
CREATE TABLE IF NOT EXISTS profile_snapshot_leases (
    round_id TEXT NOT NULL,
    identity_key TEXT NOT NULL,
    owner_device_id TEXT NOT NULL,
    expires_at_ms INTEGER NOT NULL,
    PRIMARY KEY(round_id, identity_key)
);
CREATE TABLE IF NOT EXISTS profile_snapshots (
    round_id TEXT NOT NULL,
    identity_key TEXT NOT NULL,
    observed_by_device_id TEXT NOT NULL,
    observed_username TEXT NOT NULL,
    following_count INTEGER,
    followers_count INTEGER,
    post_count INTEGER,
    private_account INTEGER NOT NULL DEFAULT 0,
    access_state TEXT NOT NULL DEFAULT 'public',
    eligible INTEGER NOT NULL,
    reason TEXT NOT NULL,
    observed_at_ms INTEGER NOT NULL,
    PRIMARY KEY(round_id, identity_key)
);
```

- [x] **Step 4: Implement transactional publication**

Require the publishing device to hold the unexpired lease. Evaluate public
metrics with the existing `evaluate_profile`; map private, suspended, missing,
and inaccessible visible states to explicit ineligible reasons. Insert the
snapshot and delete its lease in one transaction. Reject partial public metrics.

- [x] **Step 5: Test expiry takeover and round isolation**

Add assertions that phone 2 can acquire after expiry, that a late phone 1
publication is rejected, and that round 2 receives no snapshot from round 1.

Run: `uv run pytest tests/test_qualification.py tests/test_acquisition_db.py tests/test_rules.py -q`

Expected: PASS.

- [x] **Step 6: Commit shared qualification**

```bash
git add src/tikpoc/acquisition_db.py src/tikpoc/acquisition_models.py tests/test_acquisition_db.py tests/test_qualification.py
git commit -m "feat: share round qualification snapshots"
```

### Task 5: Independent Outcome Plans And Fixed-Hour Quotas

**Files:**
- Create: `src/tikpoc/outcome_planner.py`
- Modify: `src/tikpoc/acquisition_db.py`
- Create: `tests/test_outcome_planner.py`

- [x] **Step 1: Write failing independent-plan tests**

```python
def test_each_device_persists_an_independent_equal_weight_draw(tmp_path: Path) -> None:
    repository, round_id, identity_key = eligible_target_repository(tmp_path)
    plans = [
        get_or_create_plan(repository, round_id, identity_key, device_id, now_ms=3_600_000)
        for device_id in ("phone-01", "phone-02", "phone-03")
    ]
    assert len({plan.plan_id for plan in plans}) == 3
    assert all(plan.requested_outcome in set(OutcomeKind) for plan in plans)
    assert get_or_create_plan(repository, round_id, identity_key, "phone-01", now_ms=3_600_100) == plans[0]


def test_full_selected_quota_becomes_trace_without_redraw(tmp_path: Path) -> None:
    repository, round_id, identity_key = eligible_target_repository(tmp_path)
    fill_quota(repository, "phone-01", OutcomeKind.FAVORITE, window_start_ms=3_600_000, count=14)
    plan = get_or_create_plan(
        repository,
        round_id,
        identity_key,
        "phone-01",
        now_ms=3_900_000,
        forced_draw=OutcomeKind.FAVORITE,
    )
    assert plan.requested_outcome is OutcomeKind.FAVORITE
    assert plan.effective_outcome is OutcomeKind.TRACE
    assert plan.quota_reason == "favorite_limit_reached"
```

- [x] **Step 2: Run outcome tests**

Run: `uv run pytest tests/test_outcome_planner.py -q`

Expected: FAIL because outcome planning does not exist.

- [x] **Step 3: Implement deterministic independent draws**

```python
OUTCOMES = (
    OutcomeKind.LIKE,
    OutcomeKind.FAVORITE,
    OutcomeKind.REPOST,
    OutcomeKind.TRACE,
)


def plan_seed(round_id: str, identity_key: str, device_id: str) -> str:
    return hashlib.sha256("\0".join((round_id, identity_key, device_id)).encode()).hexdigest()


def draw_outcome(seed: str) -> OutcomeKind:
    return OUTCOMES[random.Random(seed).randrange(len(OUTCOMES))]


def fixed_hour_start_ms(now_ms: int) -> int:
    return now_ms - now_ms % 3_600_000
```

`forced_draw` is a keyword-only test seam; production callers omit it.

- [x] **Step 4: Add action-plan and quota schema**

```sql
CREATE TABLE IF NOT EXISTS device_action_plans (
    plan_id INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id TEXT NOT NULL,
    identity_key TEXT NOT NULL,
    device_id TEXT NOT NULL,
    seed TEXT NOT NULL,
    requested_outcome TEXT NOT NULL,
    effective_outcome TEXT NOT NULL,
    quota_window_start_ms INTEGER,
    quota_reason TEXT,
    video_key TEXT,
    state TEXT NOT NULL DEFAULT 'planned',
    created_at_ms INTEGER NOT NULL,
    UNIQUE(round_id, identity_key, device_id)
);
CREATE TABLE IF NOT EXISTS acquisition_quota_windows (
    device_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    window_start_ms INTEGER NOT NULL,
    reserved_count INTEGER NOT NULL DEFAULT 0,
    confirmed_count INTEGER NOT NULL DEFAULT 0,
    uncertain_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(device_id, outcome, window_start_ms)
);
```

Use limits `like=100`, `favorite=14`, `repost=25`. Insert the immutable plan and
reserve quota in one `BEGIN IMMEDIATE` transaction. An ineligible snapshot always
produces an effective trace without consuming a draw quota.

- [x] **Step 5: Test quota rollover, crash reuse, and draw distribution**

Check 4,000 distinct deterministic identities and require every outcome between
22 and 28 percent. Check that a plan created at `3_599_999` uses hour zero and a
new plan at `3_600_000` uses the next window. Check that reopening the repository
returns the original plan and does not reserve twice.

Run: `uv run pytest tests/test_outcome_planner.py -q`

Expected: PASS.

- [x] **Step 6: Commit action planning**

```bash
git add src/tikpoc/outcome_planner.py src/tikpoc/acquisition_db.py tests/test_outcome_planner.py
git commit -m "feat: plan independent quota controlled outcomes"
```

### Task 6: Durable Mobile Assignment State Machine

**Files:**
- Create: `src/tikpoc/mobile_worker.py`
- Modify: `src/tikpoc/acquisition_db.py`
- Modify: `src/tikpoc/acquisition_models.py`
- Modify: `tests/fakes.py`
- Create: `tests/test_mobile_worker.py`

- [x] **Step 1: Write failing no-early-advance tests**

```python
def test_worker_does_not_complete_until_visible_action_is_confirmed(tmp_path: Path) -> None:
    repository, assignment = runnable_assignment(tmp_path, forced_outcome=OutcomeKind.LIKE)
    device = ScriptedVerifiedDevice(action_results=[ActionResult.UNCERTAIN, ActionResult.CONFIRMED])
    worker = MobileAssignmentWorker(repository, device, device_id="phone-01", owner_id="worker-1", clock=step_clock())

    worker.run_assignment(assignment)

    stored = repository.assignment(assignment.assignment_id)
    assert stored.phase is AssignmentPhase.COMPLETED
    assert device.opened_profiles == [assignment.username]
    assert device.action_calls == [OutcomeKind.LIKE]
    assert device.reconcile_calls == [OutcomeKind.LIKE]


def test_slow_action_is_deferred_without_false_completion(tmp_path: Path) -> None:
    repository, assignment = runnable_assignment(tmp_path, forced_outcome=OutcomeKind.REPOST)
    device = ScriptedVerifiedDevice(action_results=[ActionResult.UNCERTAIN] * 4)
    worker = MobileAssignmentWorker(repository, device, device_id="phone-01", owner_id="worker-1", clock=clock_over_90_seconds())

    worker.run_assignment(assignment)

    stored = repository.assignment(assignment.assignment_id)
    assert stored.phase is AssignmentPhase.DEFERRED
    assert stored.completed_at_ms is None
    assert repository.round_completion(assignment.round_id).completed == 0
```

- [x] **Step 2: Run the mobile worker tests**

Run: `uv run pytest tests/test_mobile_worker.py -q`

Expected: FAIL because the worker and verified device contract do not exist.

- [x] **Step 3: Define the verified device boundary**

```python
class ActionResult(StrEnum):
    CONFIRMED = "confirmed"
    NOT_APPLIED = "not_applied"
    UNCERTAIN = "uncertain"


class VerifiedTikTokDevice(Protocol):
    def ensure_ready(self) -> None: ...
    def open_target(self, target: PoolTarget) -> None: ...
    def confirm_profile_identity(self, target: PoolTarget) -> None: ...
    def read_profile_observation(self) -> ProfileObservation: ...
    def list_video_keys(self) -> tuple[str, ...]: ...
    def open_and_confirm_video(self, video_key: str) -> None: ...
    def execute_outcome(self, outcome: OutcomeKind) -> ActionResult: ...
    def reconcile_outcome(self, outcome: OutcomeKind) -> ActionResult: ...
    def capture_diagnostics(self) -> DeviceDiagnostics: ...
    def recover(self, phase: AssignmentPhase) -> None: ...
```

- [x] **Step 4: Add validated transitions and history**

Create `assignment_phase_history` and `action_attempts`. Implement
`transition_assignment(id, expected_phase, next_phase, now_ms, details)` with an
explicit transition map. A mismatched expected phase raises
`ConcurrentAssignmentUpdate`. Record visit confirmation separately at
`IDENTITY_CONFIRMED`; record completion only after the required trace or action
is confirmed.

- [x] **Step 5: Implement orchestration and recovery budget**

The worker executes the persisted phase sequence, publishes or waits for a
snapshot, obtains one immutable plan, stores one random video key, and calls the
device. On uncertain action state, reconcile before any additional click. Allow
three recoveries and 90 seconds from the first action attempt. On exhaustion,
store diagnostics, set `next_attempt_at_ms = now_ms + 300_000`, transition to
`DEFERRED`, and release the assignment lease.

- [x] **Step 6: Add restart and ineligible-trace tests**

Restart a worker from every persisted phase and assert it resumes without a
second draw or duplicate quota reservation. Assert an ineligible profile
completes after identity confirmation without opening a video. Assert eligible
trace opens and confirms one video without pressing an action control.

Run: `uv run pytest tests/test_mobile_worker.py tests/test_acquisition_db.py tests/test_outcome_planner.py -q`

Expected: PASS.

- [x] **Step 7: Commit the durable worker**

```bash
git add src/tikpoc/mobile_worker.py src/tikpoc/acquisition_db.py src/tikpoc/acquisition_models.py tests/fakes.py tests/test_mobile_worker.py
git commit -m "feat: execute durable verified mobile assignments"
```

### Task 7: Appium Semantic Action Verification

**Files:**
- Modify: `src/tikpoc/device.py`
- Modify: `tests/test_appium_device.py`
- Create: `tests/fixtures/appium_profile_public.xml`
- Create: `tests/fixtures/appium_video.xml`
- Create: `tests/fixtures/appium_share_repost.xml`

- [x] **Step 1: Write failing lag and reconciliation tests**

```python
def test_like_waits_for_selected_state_instead_of_sleeping() -> None:
    driver = ScriptedDriver(page_sources=[VIDEO_UNLIKED_XML, VIDEO_UNLIKED_XML, VIDEO_LIKED_XML])
    device = AppiumTikTokDevice(driver, poll_interval=0, action_timeout=5, clock=step_clock())
    assert device.execute_outcome(OutcomeKind.LIKE) is ActionResult.CONFIRMED
    assert driver.clicked_labels == ["Like"]


def test_uncertain_like_reconciliation_does_not_toggle_again() -> None:
    driver = ScriptedDriver(page_sources=[VIDEO_LIKED_XML])
    device = AppiumTikTokDevice(driver, poll_interval=0)
    assert device.reconcile_outcome(OutcomeKind.LIKE) is ActionResult.CONFIRMED
    assert driver.clicked_labels == []


def test_repost_requires_share_control_repost_control_and_completed_state() -> None:
    driver = ScriptedDriver(page_sources=[VIDEO_XML, SHARE_REPOST_XML, REPOSTED_XML])
    device = AppiumTikTokDevice(driver, poll_interval=0)
    assert device.execute_outcome(OutcomeKind.REPOST) is ActionResult.CONFIRMED
    assert driver.clicked_labels == ["Share", "Repost"]
```

- [x] **Step 2: Run Appium device tests**

Run: `uv run pytest tests/test_appium_device.py -q`

Expected: FAIL because the current implementation uses fixed sleeps, screenshot
comparison for favorite, and the legacy `share` name.

- [x] **Step 3: Add condition-based wait helpers**

```python
def _wait_until(self, predicate: Callable[[], bool], description: str) -> None:
    deadline = self.clock() + self.action_timeout
    while self.clock() < deadline:
        if predicate():
            return
        self.sleeper(self.poll_interval)
    raise ActionStateTimeout(description)
```

Inject `clock` and `sleeper`. Parse Appium page source with structured XML and
semantic labels/resource IDs. Do not use native `uiautomator dump` for the
animated TikTok feed.

- [x] **Step 4: Implement explicit state probes**

Implement `_like_state`, `_favorite_state`, and `_repost_state` returning active,
inactive, or unknown. Use calibrated English and Chinese labels plus stable
resource IDs. Favorite success requires an active semantic state or visible
saved confirmation; pixel inequality alone is not accepted. Repost success
requires Reposted/Remove repost/You reposted or a calibrated equivalent.

- [x] **Step 5: Implement execute then reconcile behavior**

`execute_outcome` records pre-state, clicks once only when inactive, waits for
active state, and returns uncertain on timeout. `reconcile_outcome` only reads
state. Retain `perform_action("share")` as a legacy adapter that delegates to
`execute_outcome(OutcomeKind.REPOST)` until legacy callers are migrated.

- [x] **Step 6: Run device, parser, legacy worker, and new worker tests**

Run: `uv run pytest tests/test_appium_device.py tests/test_profile_parser.py tests/test_worker.py tests/test_mobile_worker.py -q`

Expected: PASS.

- [x] **Step 7: Commit visible verification**

```bash
git add src/tikpoc/device.py tests/test_appium_device.py tests/fixtures/appium_profile_public.xml tests/fixtures/appium_video.xml tests/fixtures/appium_share_repost.xml
git commit -m "fix: verify mobile interactions before advancing"
```

### Task 8: MYT Discovery, Allowlisted Proxy Relay, And Fleet Ownership

**Files:**
- Create: `src/tikpoc/myt.py`
- Create: `src/tikpoc/proxy_relay.py`
- Create: `src/tikpoc/fleet.py`
- Modify: `config/devices.example.yaml`
- Create: `tests/test_myt.py`
- Create: `tests/test_proxy_relay.py`
- Create: `tests/test_fleet.py`

- [x] **Step 1: Write failing MYT response tests**

```python
def test_myt_lists_running_slots_with_mapped_adb_ports() -> None:
    transport = FakeJsonTransport({"code": 0, "data": [{
        "id": "container-1", "name": "T0001", "status": "running", "indexNum": 1,
        "portBindings": {"5555/tcp": [{"HostPort": "30000"}]},
    }]})
    client = MytClient("192.168.28.114", transport=transport)
    slots = client.list_android()
    assert slots[0].adb_endpoint == "192.168.28.114:30000"
    assert slots[0].slot_index == 1
```

- [x] **Step 2: Write failing relay allowlist tests**

```python
def test_relay_rejects_a_source_outside_allowlist() -> None:
    policy = RelayPolicy(allowed_sources=frozenset({"192.168.28.114"}))
    assert policy.permits("192.168.28.114") is True
    assert policy.permits("192.168.28.200") is False


def test_relay_forwards_bytes_to_loopback_upstream() -> None:
    upstream = EchoServer("127.0.0.1", 0)
    relay = ProxyRelay("127.0.0.1", 0, "127.0.0.1", upstream.port, allowed_sources={"127.0.0.1"})
    with upstream, relay:
        payload = b"CONNECT example.test:443 HTTP/1.1\r\n\r\n"
        assert tcp_exchange("127.0.0.1", relay.port, payload) == payload
```

- [x] **Step 3: Run MYT, relay, and fleet tests**

Run: `uv run pytest tests/test_myt.py tests/test_proxy_relay.py tests/test_fleet.py -q`

Expected: FAIL because the modules do not exist.

- [x] **Step 4: Implement the MYT client with standard HTTP APIs**

Define `JsonTransport.request(method, url, json_body=None)` and a production
`UrllibJsonTransport`. Implement `/info` and `/android`, unwrap `code/data`, and
parse name, status, slot index, ADB host port, web port, image, and raw health.
Raise `MytSdkError` with HTTP status or SDK code; never include credentials.

- [x] **Step 5: Implement the TCP relay**

Use `socketserver.ThreadingTCPServer`. Bind only the configured Mac LAN address,
reject clients whose source IP is outside `allowed_sources`, connect upstream to
`127.0.0.1:7897`, and copy both directions with bounded buffers and idle timeout.
Expose a context-managed handle and health snapshot. Do not change Clash's
`allow-lan` setting.

- [x] **Step 6: Implement typed fleet configuration and ownership**

```python
@dataclass(frozen=True)
class FleetDevice:
    device_id: str
    account_id: str
    myt_slot: int
    adb_endpoint: str
    appium_url: str
    order_seed: str


@dataclass(frozen=True)
class FleetConfig:
    myt_host: str
    myt_sdk_port: int
    relay_bind_host: str
    relay_upstream_port: int
    devices: tuple[FleetDevice, ...]
```

Validate unique device, account, slot, ADB endpoint, and seed values. Acquire one
SQLite worker lease per device/account before creating its Appium session.
Unexpected child exit marks that device unhealthy and leaves assignments
recoverable; it does not terminate healthy workers.

- [x] **Step 7: Run focused tests and commit infrastructure**

Run: `uv run pytest tests/test_myt.py tests/test_proxy_relay.py tests/test_fleet.py -q`

Expected: PASS.

```bash
git add src/tikpoc/myt.py src/tikpoc/proxy_relay.py src/tikpoc/fleet.py config/devices.example.yaml tests/test_myt.py tests/test_proxy_relay.py tests/test_fleet.py
git commit -m "feat: manage MYT fleet and local proxy relay"
```

### Task 9: CLI Operations, Capacity Report, And Coverage Gate

**Files:**
- Create: `src/tikpoc/capacity.py`
- Modify: `src/tikpoc/cli.py`
- Modify: `src/tikpoc/acquisition_db.py`
- Create: `tests/test_capacity.py`
- Modify: `tests/test_cli.py`

- [x] **Step 1: Write failing strict capacity tests**

```python
def test_capacity_uses_slowest_device_and_completed_assignments() -> None:
    rows = synthetic_completed_timings(
        {"phone-01": [6_000] * 100, "phone-02": [6_400] * 100},
    )
    report = evaluate_capacity(rows, expected_devices=2, target_count=10_000, effective_hours=20)
    assert report.slowest_device_id == "phone-02"
    assert report.projected_unique_per_day == 11_250


def test_uncertain_or_missing_coverage_fails_promotion() -> None:
    report = evaluate_capacity(
        synthetic_completed_timings(seven_fast_devices()),
        expected_devices=7,
        target_count=10_000,
        effective_hours=20,
        uncertain_count=1,
        fully_covered_targets=9_999,
    )
    assert report.passed is False
    assert "uncertain assignments" in report.reasons
    assert "7/7 coverage incomplete" in report.reasons
```

- [x] **Step 2: Run capacity and CLI tests**

Run: `uv run pytest tests/test_capacity.py tests/test_cli.py -q`

Expected: FAIL because the acquisition capacity module and commands are absent.

- [x] **Step 3: Implement capacity value types and math**

```python
@dataclass(frozen=True)
class DeviceCapacity:
    confirmed: int
    mean_ms: float
    p90_ms: float
    confirmed_per_hour: float
    projected_per_effective_day: int
    passed: bool


@dataclass(frozen=True)
class CapacityReport:
    measured_seconds: float
    slowest_device_id: str
    projected_unique_per_day: int
    fully_covered_targets: int
    uncertain_count: int
    devices: dict[str, DeviceCapacity]
    reasons: tuple[str, ...]
    passed: bool
```

Use assignment completion durations only. Require every expected device, mean
below 6,500 ms, p90 below 8,640 ms, zero identity mismatch, zero false success,
zero quota overrun, zero pending deferred work, and exact target 7/7 coverage.

- [x] **Step 4: Add acquisition CLI commands**

Add:

```text
tikpoc pool-import --db DB --csv PATH
tikpoc round-create --db DB --pool POOL --devices CONFIG --starts-at ISO8601
tikpoc fleet-run --db DB --round ROUND --devices CONFIG
tikpoc assignment-retry --db DB --assignment ID
tikpoc capacity --db DB --round ROUND --expected-devices 7 --target-count 10000 --effective-hours 20 --json
```

Validate paths and IDs before mutations. JSON output has stable keys matching
`CapacityReport`; text output labels measured and projected values separately.

- [x] **Step 5: Run capacity, CLI, and repository tests**

Run: `uv run pytest tests/test_capacity.py tests/test_cli.py tests/test_acquisition_db.py -q`

Expected: PASS.

- [x] **Step 6: Commit operations and capacity gate**

```bash
git add src/tikpoc/capacity.py src/tikpoc/cli.py src/tikpoc/acquisition_db.py tests/test_capacity.py tests/test_cli.py
git commit -m "feat: operate and gate acquisition capacity"
```

### Task 10: Regression, Two-Device Calibration, And Runbook

**Files:**
- Create: `docs/mobile-fleet-runbook.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: Run full automated verification**

Run:

```bash
uv run pytest -q
uv tool run ruff check src tests
uv tool run ruff format --check src tests
node --test chrome-event-bridge/*.test.js
bash android-event-bridge/build.sh
git diff --check
```

Expected: every command exits 0.

- [ ] **Step 2: Calibrate slot health without account actions**

For MYT host `192.168.28.114`, verify slot 1 and slot 2 discovery, ADB
connectivity, Android version, resolution, TikTok package/version, proxy relay,
Appium session creation, `waitForIdleTimeout=0`, profile identity parsing, and
screenshot capture. Record slot 2 setup as a prerequisite until TikTok is
installed and the user completes login.

- [ ] **Step 3: Calibrate each visible outcome on controlled targets**

Force one plan at a time for trace, like, favorite, and repost. For every plan,
record pre-state, one click path, post-state, duration, and screenshot. Inject a
slow UI response and verify reconciliation succeeds without a second toggle.
Reset controlled target state between cases.

- [ ] **Step 4: Run the current CSV through two devices**

Import `/Users/chenyuqi/Desktop/tik/tiktok_comments_7440036951958244609_2026-04-15T08-03-55-614Z.csv`, require 326 targets and 652 assignments, and finish
with exact 2/2 completion. Verify one snapshot per target, different device
orders, independent plans, no quota overrun, no false completion, and an empty
deferred queue.

- [ ] **Step 5: Write the operational record**

Document exact commands, configuration paths, known selectors, screenshots,
recovery categories, quota inspection, two-device results, observed mean/p90,
and the distinction between this functional gate and the later production
capacity gate. Update `AGENTS.md` with the last verified commit and next task.

- [ ] **Step 6: Commit the accepted mobile checkpoint**

```bash
git add docs/mobile-fleet-runbook.md AGENTS.md
git commit -m "docs: record two device mobile acceptance"
```
