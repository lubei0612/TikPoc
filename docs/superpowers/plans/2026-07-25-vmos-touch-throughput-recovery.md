# VMOS Touch Throughput Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure and remove the dominant redundant Appium work on VMOS while preserving exact identity, qualification, interaction, verification, quota, retry, and coverage behavior.

**Architecture:** A transparent driver proxy records cumulative Appium RPC counts and wall time without changing driver results. The mobile worker snapshots those counters around its existing route, identity, metrics, video, and action stages and persists deltas beside stage timing; the resulting isolated VMOS baseline selects the single highest-value fast-path change. Existing semantic caches and slow-path fallbacks remain authoritative.

**Tech Stack:** Python 3.12, Appium Python client, Selenium, SQLite, pytest, Ruff, `uv`.

---

## File Structure

- Create `src/tikpoc/device_performance.py`: transparent driver measurement proxy and immutable counter snapshots.
- Modify `src/tikpoc/acquisition_models.py`: add the persisted per-stage command metrics value object.
- Modify `src/tikpoc/acquisition_db.py`: create, write, and read stage command metrics without changing assignment state.
- Modify `src/tikpoc/mobile_worker.py`: snapshot counters at the current stage boundaries.
- Modify `src/tikpoc/fleet_runtime.py`: wrap each real Appium driver once and pass performance snapshots through the fenced adapter.
- Create `tests/test_device_performance.py`: proxy behavior and counter-delta tests.
- Modify `tests/test_acquisition_db.py`, `tests/test_mobile_worker.py`, and `tests/test_fleet_runtime.py`: persistence and integration coverage.
- Modify `src/tikpoc/device.py` and `tests/test_appium_device.py` only for the one evidence-selected redundant operation.
- Update `docs/superpowers/specs/2026-07-25-vmos-touch-throughput-recovery-design.md` with measured before/after evidence.

### Task 1: Transparent Appium command measurement

**Files:**
- Create: `src/tikpoc/device_performance.py`
- Test: `tests/test_device_performance.py`

- [ ] **Step 1: Write failing proxy tests**

```python
def test_measured_driver_preserves_results_and_records_commands() -> None:
    raw = FakeDriver(page_source="<hierarchy/>")
    driver = MeasuredAppiumDriver(raw, clock=SteppingClock())

    assert driver.page_source == "<hierarchy/>"
    assert driver.find_elements("xpath", "//*") == ["node"]
    assert driver.execute_script("mobile: deepLink", {"url": "TARGET"}) == "ok"

    snapshot = driver.performance_snapshot()
    assert snapshot.command_count == 3
    assert snapshot.page_source_reads == 1
    assert snapshot.element_queries == 1
    assert snapshot.execute_script_calls == 1
    assert snapshot.command_duration_ms == 300


def test_command_snapshot_subtraction_returns_nonnegative_delta() -> None:
    before = DevicePerformanceSnapshot(2, 200, 1, 1, 0)
    after = DevicePerformanceSnapshot(5, 650, 2, 2, 1)
    assert after - before == DevicePerformanceSnapshot(3, 450, 1, 1, 1)
```

- [ ] **Step 2: Run the tests and confirm RED**

Run: `uv run pytest tests/test_device_performance.py -q`

Expected: collection fails because `tikpoc.device_performance` does not exist.

- [ ] **Step 3: Implement the transparent proxy**

```python
@dataclass(frozen=True)
class DevicePerformanceSnapshot:
    command_count: int = 0
    command_duration_ms: int = 0
    page_source_reads: int = 0
    element_queries: int = 0
    execute_script_calls: int = 0

    def __sub__(self, previous: "DevicePerformanceSnapshot") -> "DevicePerformanceSnapshot":
        values = tuple(max(0, current - old) for current, old in zip(astuple(self), astuple(previous), strict=True))
        return type(self)(*values)


class MeasuredAppiumDriver:
    def __init__(self, driver: object, *, clock: Callable[[], float] = time.perf_counter) -> None:
        self._driver = driver
        self._clock = clock
        self._snapshot = DevicePerformanceSnapshot()

    @property
    def page_source(self):
        return self._measure("page_source", lambda: self._driver.page_source)

    def find_elements(self, *args, **kwargs):
        return self._measure("find_elements", lambda: self._driver.find_elements(*args, **kwargs))

    def execute_script(self, *args, **kwargs):
        return self._measure("execute_script", lambda: self._driver.execute_script(*args, **kwargs))

    def performance_snapshot(self) -> DevicePerformanceSnapshot:
        return self._snapshot

    def __getattr__(self, name: str):
        attribute = getattr(self._driver, name)
        if not callable(attribute):
            return attribute
        return lambda *args, **kwargs: self._measure("command", lambda: attribute(*args, **kwargs))
```

The private `_measure` method must update counters in `finally`, so failed Appium calls are measured and the original result or exception is preserved unchanged.

- [ ] **Step 4: Run focused verification**

Run: `uv run pytest tests/test_device_performance.py -q`

Expected: all proxy tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/tikpoc/device_performance.py tests/test_device_performance.py
git commit -m "feat: measure Appium command latency"
```

### Task 2: Persist command deltas at existing worker stages

**Files:**
- Modify: `src/tikpoc/acquisition_models.py`
- Modify: `src/tikpoc/acquisition_db.py`
- Modify: `src/tikpoc/mobile_worker.py`
- Modify: `src/tikpoc/fleet_runtime.py`
- Modify: `tests/test_acquisition_db.py`
- Modify: `tests/test_mobile_worker.py`
- Modify: `tests/test_fleet_runtime.py`

- [ ] **Step 1: Write failing repository and worker tests**

```python
def test_repository_round_trips_stage_command_metrics(repository, assignment) -> None:
    stored = repository.record_assignment_command_metrics(
        assignment.assignment_id,
        AssignmentStage.IDENTITY,
        command_count=3,
        command_duration_ms=620,
        page_source_reads=2,
        element_queries=1,
        execute_script_calls=0,
        recorded_at_ms=1_000,
    )
    assert repository.assignment_command_metrics(assignment.assignment_id) == (stored,)


def test_worker_records_performance_delta_for_route(repository, assignment) -> None:
    device = PerformanceDevice([snapshot(0), snapshot(3, 600, 1, 1, 1)])
    worker = build_worker(repository, device)
    worker.run_assignment(assignment)
    route = repository.assignment_command_metrics(assignment.assignment_id)[0]
    assert route.stage is AssignmentStage.ROUTE
    assert route.command_count == 3
```

Also add a fleet-runtime test asserting that `run_device_worker` wraps the raw driver exactly once and that `FencedVerifiedDevice.performance_snapshot()` returns the raw device snapshot through the worker fence.

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `uv run pytest tests/test_acquisition_db.py tests/test_mobile_worker.py tests/test_fleet_runtime.py -q`

Expected: failures report the missing command-metrics model, repository methods, and device performance boundary.

- [ ] **Step 3: Add additive SQLite storage**

Create `assignment_command_metrics` with primary key `(assignment_id, stage)` and nonnegative integer columns for command count, command duration, page-source reads, element queries, execute-script calls, and recorded time. Implement fenced upsert/read methods mirroring `record_assignment_stage_timing`; do not modify assignment phases or completion accounting.

- [ ] **Step 4: Add stage snapshots without changing stage behavior**

Extend `VerifiedTikTokDevice` and `FencedVerifiedDevice` with:

```python
def performance_snapshot(self) -> DevicePerformanceSnapshot: ...
```

Change each existing stage start to capture both wall time and a counter snapshot, and change `_record_stage` to persist `current_snapshot - started_snapshot` immediately after the existing stage timing. A device without the method returns the zero snapshot so synthetic tests and non-Appium adapters preserve their behavior.

- [ ] **Step 5: Wrap real fleet drivers once**

In `run_device_worker`, construct `MeasuredAppiumDriver(driver)` before `device_factory`, but keep `driver.quit()` on the original driver in `finally`. This avoids double wrapping and preserves cleanup.

- [ ] **Step 6: Run focused verification**

Run: `uv run pytest tests/test_device_performance.py tests/test_acquisition_db.py tests/test_mobile_worker.py tests/test_fleet_runtime.py -q`

Expected: all focused tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/tikpoc/acquisition_models.py src/tikpoc/acquisition_db.py src/tikpoc/mobile_worker.py src/tikpoc/fleet_runtime.py tests/test_acquisition_db.py tests/test_mobile_worker.py tests/test_fleet_runtime.py
git commit -m "feat: persist mobile stage command metrics"
```

### Task 3: Measure VMOS and remove the dominant redundant operation

**Files:**
- Modify: `src/tikpoc/device.py`
- Modify: `tests/test_appium_device.py`
- Modify: `docs/superpowers/specs/2026-07-25-vmos-touch-throughput-recovery-design.md`

- [ ] **Step 1: Run an isolated unchanged 20-target baseline**

Use `127.0.0.1:57203`, the existing Appium endpoint, `var/vmos-touch-speed-20.csv`, and a new database `var/vmos-touch-command-baseline.db`. Stop after 20 terminal assignments or 15 minutes. Export per-stage wall time, command time, page-source reads, element queries, confirmed visits, completions, deferrals, uncertain results, and error codes.

- [ ] **Step 2: Select exactly one implementation branch from measured evidence**

Apply the first matching branch only:

1. If identity page-source reads contribute the most command wall time, add a `ProfileSurfaceSnapshot` parser result and pass the already-read XML from identity into observation parsing; the fallback reads fresh XML only when the cached source is incomplete or mismatched.
2. If video element queries contribute the most command wall time, retain the verified visible post element selected by `list_video_keys()` through `open_and_confirm_video()` and poll a single combined visible-video-control selector after its click.
3. If action element queries contribute the most command wall time, keep the current settled-control and post-click verification semantics but reuse the last uniquely visible pre-click control instead of issuing an equivalent lookup before the click.

Do not alter global Appium HTTP timeout, action quotas, outcome selection, retry count, or terminal classification.

- [ ] **Step 3: Write the failing behavioral test for the selected branch**

The test must assert both the visible business result and the exact reduced command/query count. For example, the video branch test is:

```python
def test_cached_visible_post_opens_with_one_post_query_and_one_control_poll() -> None:
    driver = CachedPostDriver()
    device = AppiumTikTokDevice(driver, poll_interval=0, action_timeout=0)
    keys = device.list_video_keys()
    device.open_and_confirm_video(keys[0])
    assert driver.post_queries == 1
    assert driver.video_control_queries == 1
    assert driver.clicked_posts == [0]
```

- [ ] **Step 4: Run the selected test and confirm RED**

Run: `uv run pytest tests/test_appium_device.py -q`

Expected: the command/query count exceeds the asserted count while identity or action correctness remains visible.

- [ ] **Step 5: Implement only the selected fast path**

Keep the existing semantic slow path for incomplete, hidden, stale, ambiguous, or mismatched state. Never synthesize metrics, click an unverified element, or convert a due action to trace.

- [ ] **Step 6: Run device and worker verification**

Run: `uv run pytest tests/test_appium_device.py tests/test_rules.py tests/test_mobile_worker.py tests/test_fleet_runtime.py -q`

Expected: all tests pass, including exact identity, zero-post fallback, cached post reuse, action verification, and single reconciliation.

- [ ] **Step 7: Run the same isolated 20-target after-test**

Use `var/vmos-touch-command-after.db` and the identical CSV/device/account configuration. Retain the change only when the selected stage's command wall contribution falls, confirmed visits and terminal completions do not fall, and identity/action/uncertain/deferred evidence does not regress.

- [ ] **Step 8: Record evidence and commit**

```bash
git add src/tikpoc/device.py tests/test_appium_device.py docs/superpowers/specs/2026-07-25-vmos-touch-throughput-recovery-design.md
git commit -m "perf: remove redundant VMOS touch query"
```

### Task 4: Capacity gates and regression

**Files:**
- Modify: `docs/superpowers/specs/2026-07-25-vmos-touch-throughput-recovery-design.md`

- [ ] **Step 1: Run complete static and automated verification**

```bash
uv run pytest -q
uv tool run ruff check src tests
uv tool run ruff format --check src tests
node --test chrome-event-bridge/*.test.js
bash android-event-bridge/build.sh
git diff --check
```

Expected: every command exits zero.

- [ ] **Step 2: Run the 100-target VMOS gate**

Measure from first successful claim to the last terminal assignment. Require at least 400 confirmed visits/hour and clean exact-identity, action-evidence, quota, duplicate-attempt, and coverage audits before advancing.

- [ ] **Step 3: Run the 30-minute promotion gate**

Require at least 500 confirmed visits/hour and a projected 20-hour capacity of at least 10,000 for this device. Report measured throughput separately from projection; preferred acceptance is at least 550/hour with mean below 6.5 seconds and p90 below 8.64 seconds.

- [ ] **Step 4: Record result and rollback any failed candidate**

Append the database names, wall windows, completed/confirmed/deferred/uncertain counts, stage percentiles, command counts, measured hourly rate, and projected 20-hour capacity to the design. Revert the performance commit if any correctness invariant or regression gate fails.

- [ ] **Step 5: Commit the accepted evidence**

```bash
git add docs/superpowers/specs/2026-07-25-vmos-touch-throughput-recovery-design.md
git commit -m "docs: record VMOS touch capacity evidence"
```
