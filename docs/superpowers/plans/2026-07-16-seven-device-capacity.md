# Seven-Device TikTok Capacity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make seven paired mobile accounts complete confirmed profile touches for at least 10,000 shared targets per day while preserving rule-based interaction quotas and exact 7/7 coverage accounting.

**Architecture:** Imported metrics determine eligibility centrally. A fast ADB/MYT-compatible device backend opens prescreened targets through TikTok deep links, validates the visible profile route from UI XML, performs one preplanned outcome, and records phase timings. A fleet runner starts one account-scoped worker per device, while a capacity command enforces average, p90, identity, and coverage gates.

**Tech Stack:** Python 3.12, SQLite, Android Debug Bridge, TikTok deep links, Android UI XML, pytest, existing worker/rule/interaction modules.

---

## File Map

- Create `src/tikpoc/adb_fast_device.py`: fast deep-link, XML, tap, and action backend.
- Create `src/tikpoc/fleet.py`: seven-device configuration and worker launcher.
- Create `src/tikpoc/capacity.py`: duration percentiles, throughput projection, and promotion verdict.
- Modify `src/tikpoc/worker.py`: pass task identity to capable backends and record phase timings.
- Modify `src/tikpoc/db.py`: durable task timing and coverage queries.
- Modify `src/tikpoc/runner.py`: construct Appium or fast ADB devices.
- Modify `src/tikpoc/cli.py`: add backend, fleet, and capacity commands.
- Modify `config/devices.example.yaml`: include backend and ADB/RPA connection fields.
- Modify `docs/interaction-runbook.md`: document 70,000-visit capacity operation.
- Create `tests/fixtures/tiktok_profile.xml`: confirmed public profile route fixture.
- Create `tests/fixtures/tiktok_video.xml`: opened video and action controls fixture.
- Create `tests/test_adb_fast_device.py`: command and XML behavior coverage.
- Create `tests/test_capacity.py`: capacity gate coverage.
- Create `tests/test_fleet.py`: seven-worker launch coverage.
- Modify `tests/fakes.py`: add target-aware device and deterministic clock helpers.
- Modify `tests/test_worker.py`: target-aware opening and timing coverage.
- Modify `tests/test_db.py`: timing and coverage persistence coverage.
- Modify `tests/test_cli.py`: new command parsing coverage.

### Task 1: Task Phase Timings And Confirmed Visit Accounting

**Files:**
- Modify: `src/tikpoc/db.py`
- Modify: `src/tikpoc/worker.py:10-90`
- Modify: `tests/test_db.py`
- Modify: `tests/test_worker.py`

- [ ] **Step 1: Write failing timing tests**

```python
def test_worker_records_confirmed_visit_duration(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite")
    db.migrate()
    task_id = db.insert_task(
        "batch-01",
        "user-01",
        "buyer",
        device_id="phone-01",
        profile_metrics=ProfileMetrics(20, 10, 5),
        sec_uid="sec-01",
    )
    clock = iter([0.0, 0.4, 1.0, 1.8, 2.2, 2.6]).__next__

    Worker(db, TargetAwareFakeDevice(), clock=clock, device_id="phone-01").run_one()

    timing = db.task_timing(task_id)
    assert timing["visit_confirmed"] == 1
    assert timing["total_ms"] == 2600
    assert timing["profile_open_ms"] == 400


def test_rule_skip_after_profile_open_counts_as_confirmed_visit(tmp_path: Path) -> None:
    db = seeded_rule_skip_database(tmp_path)
    Worker(db, TargetAwareFakeDevice(), clock=step_clock(), device_id="phone-01").run_one()
    assert db.batch_coverage("batch-01", expected_devices=1)["fully_covered"] == 1
```

Add `TargetAwareFakeDevice` to `tests/fakes.py`; its `open_target(task)` stores
the received task and delegates to `open_profile(task.username)`. Add a
`StepClock` callable that returns a supplied sequence, and build the rule-skip
database inline with one prescreened task whose following count is below its
follower count.

- [ ] **Step 2: Run focused tests and verify missing timing support**

Run: `uv run pytest tests/test_db.py tests/test_worker.py -q`

Expected: FAIL because `Worker` lacks `clock` and `Database` lacks timing methods.

- [ ] **Step 3: Add the timing table and methods**

Create `task_timings` keyed by task ID with device, batch, target, start/finish milliseconds, total, profile-open, profile-ready, post-open, action, outcome, and `visit_confirmed`. Implement `start_task_timing`, `finish_task_timing`, `task_timing`, and `batch_device_timings`.

- [ ] **Step 4: Instrument `Worker`**

Inject `clock: Callable[[], float] = time.monotonic`. Record phase boundaries around ready, profile open, profile validation, post open, action, and return. Set `visit_confirmed` immediately after `wait_profile_ready` or a successful metric read. Finish the timing record in `finally`, including retry and failure outcomes.

- [ ] **Step 5: Pass full target identity to fast backends**

Use a capability method without breaking the current protocol:

```python
target_opener = getattr(self.device, "open_target", None)
if callable(target_opener):
    target_opener(task)
else:
    self.device.open_profile(task.username)
```

- [ ] **Step 6: Run worker and database tests**

Run: `uv run pytest tests/test_db.py tests/test_worker.py -q`

Expected: PASS.

- [ ] **Step 7: Commit timing instrumentation**

```bash
git add src/tikpoc/db.py src/tikpoc/worker.py tests/test_db.py tests/test_worker.py
git commit -m "feat: record confirmed touch timings"
```

### Task 2: Fast ADB Profile Entry And Route Validation

**Files:**
- Create: `src/tikpoc/adb_fast_device.py`
- Create: `tests/fixtures/tiktok_profile.xml`
- Create: `tests/test_adb_fast_device.py`

- [ ] **Step 1: Write failing deep-link tests**

```python
def test_open_target_prefers_sec_uid_deeplink() -> None:
    runner = FakeAdbRunner(outputs=[ok_start(), "UI hierarchy dumped", PROFILE_XML])
    device = AdbFastTikTokDevice("adb", "127.0.0.1:5555", runner=runner)
    task = Task(1, "batch", "user-1", "buyer", TaskState.RUNNING, 1, None, "phone-01", sec_uid="sec-1")

    device.open_target(task)
    device.wait_profile_ready("buyer")

    assert "snssdk1233://user/profile/sec-1" in " ".join(runner.commands[0])
    assert device.last_route == "profile"


def test_identity_mismatch_raises_terminal_profile_error() -> None:
    runner = FakeAdbRunner(outputs=[ok_start(), "UI hierarchy dumped", PROFILE_XML.replace("@buyer", "@other")])
    device = AdbFastTikTokDevice("adb", "serial", runner=runner)
    device.open_profile("buyer")
    with pytest.raises(ProfileIdentityMismatch):
        device.wait_profile_ready("buyer")
```

- [ ] **Step 2: Run the new tests and verify import failure**

Run: `uv run pytest tests/test_adb_fast_device.py -q`

Expected: FAIL because `tikpoc.adb_fast_device` does not exist.

- [ ] **Step 3: Implement the command seam**

```python
class AdbRunner(Protocol):
    def run(self, args: list[str], timeout: float) -> str: ...


class SubprocessAdbRunner:
    def run(self, args: list[str], timeout: float) -> str:
        completed = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        if completed.returncode != 0:
            raise AdbCommandError((completed.stderr or completed.stdout).strip())
        return completed.stdout
```

- [ ] **Step 4: Implement deep-link entry**

Try `snssdk1233://user/profile/{sec_uid}`, then `snssdk1233://user/profile/{target_id}`, then `https://www.tiktok.com/@{username}`. Run `adb -s SERIAL shell am start -W -a android.intent.action.VIEW -d URI com.zhiliaoapp.musically`. Reuse the current warm TikTok process.

- [ ] **Step 5: Implement one-dump route validation**

Run `uiautomator dump` followed by `exec-out cat`, parse with `xml.etree.ElementTree`, normalize visible `text` and `content-desc`, confirm a profile route and exact normalized username token, and raise typed errors for blocker, route miss, and identity mismatch.

- [ ] **Step 6: Run backend tests and lint**

Run: `uv run pytest tests/test_adb_fast_device.py -q && uv run ruff check src/tikpoc/adb_fast_device.py tests/test_adb_fast_device.py`

Expected: PASS and Ruff clean.

- [ ] **Step 7: Commit fast entry**

```bash
git add src/tikpoc/adb_fast_device.py tests/fixtures/tiktok_profile.xml tests/test_adb_fast_device.py
git commit -m "feat: open TikTok profiles through fast deep links"
```

### Task 3: Fast Video And Exclusive Interaction Actions

**Files:**
- Modify: `src/tikpoc/adb_fast_device.py`
- Create: `tests/fixtures/tiktok_video.xml`
- Modify: `tests/test_adb_fast_device.py`

- [ ] **Step 1: Write failing semantic action tests**

```python
@pytest.mark.parametrize("action,label", [("like", "Like"), ("favorite", "Add to Favorites")])
def test_action_taps_matching_visible_control(action: str, label: str) -> None:
    runner = FakeAdbRunner(outputs=[VIDEO_XML])
    device = AdbFastTikTokDevice("adb", "serial", runner=runner)
    device.perform_action(action)
    assert runner.last_tap == center_of_node(VIDEO_XML, label)


def test_share_action_opens_sheet_and_clicks_repost() -> None:
    runner = FakeAdbRunner(outputs=[VIDEO_XML, SHARE_SHEET_XML, REPOST_CONFIRMED_XML])
    device = AdbFastTikTokDevice("adb", "serial", runner=runner)
    device.perform_action("share")
    assert runner.tapped_labels == ["Share", "Repost"]
```

- [ ] **Step 2: Run tests and verify missing behavior**

Run: `uv run pytest tests/test_adb_fast_device.py -q`

Expected: FAIL because visible post and action methods are incomplete.

- [ ] **Step 3: Implement bounds-based semantic taps**

Parse `[left,top][right,bottom]` bounds only from nodes that match exact multilingual labels or stable resource IDs. `list_visible_posts` returns stable bound tokens from the current profile dump. `open_post` taps one token and confirms the video route.

- [ ] **Step 4: Implement exclusive actions**

Map `like`, `favorite`, and `share` to exact visible controls. For share, require a second exact Repost/转发 control and a post-action state change. Return after one action because `InteractionPolicy` already chooses exactly one of Like, Favorite, Share, or trace-only.

- [ ] **Step 5: Run device and interaction tests**

Run: `uv run pytest tests/test_adb_fast_device.py tests/test_interactions.py tests/test_worker.py -q`

Expected: PASS.

- [ ] **Step 6: Commit fast interactions**

```bash
git add src/tikpoc/adb_fast_device.py tests/fixtures/tiktok_video.xml tests/test_adb_fast_device.py
git commit -m "feat: add fast exclusive TikTok interactions"
```

### Task 4: Backend Selection And Seven-Device Fleet Runner

**Files:**
- Create: `src/tikpoc/fleet.py`
- Modify: `src/tikpoc/runner.py`
- Modify: `src/tikpoc/cli.py`
- Modify: `config/devices.example.yaml`
- Create: `tests/test_fleet.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing fleet configuration tests**

```python
def test_fleet_requires_unique_device_and_account_ids(tmp_path: Path) -> None:
    path = write_devices(tmp_path, duplicate_device=True)
    with pytest.raises(ValueError, match="duplicate device id"):
        FleetConfig.from_path(path)


def test_fleet_launches_one_worker_per_configured_device(tmp_path: Path) -> None:
    config = seven_device_config(tmp_path)
    launched = []
    run_fleet(config, database_path=tmp_path / "db.sqlite", launcher=launched.append)
    assert [item.device_id for item in launched] == [f"phone-{i:02d}" for i in range(1, 8)]
```

- [ ] **Step 2: Run fleet and CLI tests and verify missing command**

Run: `uv run pytest tests/test_fleet.py tests/test_cli.py -q`

Expected: FAIL because `FleetConfig` and `run-fleet` do not exist.

- [ ] **Step 3: Implement typed fleet configuration**

```python
@dataclass(frozen=True)
class FleetDevice:
    device_id: str
    account_id: str
    udid: str
    backend: str
    adb_path: str = "adb"
    appium_url: str = "http://127.0.0.1:4723"


@dataclass(frozen=True)
class FleetConfig:
    devices: tuple[FleetDevice, ...]
```

Validate unique device, account, and UDID values. Accept `appium` and `adb-fast` backends.

- [ ] **Step 4: Add device construction and CLI flags**

Add `--backend`, `--adb-path`, and `run-fleet --devices PATH`. Build `AppiumTikTokDevice` for calibration or `AdbFastTikTokDevice` for capacity. Preserve all existing interaction probability and quota arguments.

- [ ] **Step 5: Implement process isolation**

Launch one `multiprocessing.Process` per device, pass the exact device ID to `run_queue`, propagate SIGINT, and return a nonzero status when any child exits unexpectedly. Each worker uses the shared SQLite database and existing account-scoped claims.

- [ ] **Step 6: Run fleet, CLI, and runner tests**

Run: `uv run pytest tests/test_fleet.py tests/test_cli.py tests/test_runner.py -q`

Expected: PASS.

- [ ] **Step 7: Commit fleet execution**

```bash
git add src/tikpoc/fleet.py src/tikpoc/runner.py src/tikpoc/cli.py config/devices.example.yaml tests/test_fleet.py tests/test_cli.py
git commit -m "feat: run seven device workers as a fleet"
```

### Task 5: Capacity Projection And Promotion Gate

**Files:**
- Create: `src/tikpoc/capacity.py`
- Modify: `src/tikpoc/db.py`
- Modify: `src/tikpoc/cli.py`
- Create: `tests/test_capacity.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing capacity verdict tests**

```python
def test_capacity_passes_only_when_every_device_meets_latency_and_coverage() -> None:
    rows = sample_timings(device_count=7, per_device=1000, average_ms=6200, p90_ms=8300)
    report = evaluate_capacity(rows, target_count=10_000, expected_devices=7)
    assert report.passed is True
    assert report.projected_unique_per_day >= 10_000


def test_one_slow_device_fails_the_fleet_gate() -> None:
    rows = sample_timings(device_count=7, per_device=1000, average_ms=6200, p90_ms=8300)
    rows.extend(sample_device_timings("phone-07", average_ms=7000, p90_ms=9400))
    report = evaluate_capacity(rows, target_count=10_000, expected_devices=7)
    assert report.passed is False
    assert report.devices["phone-07"].p90_ms > 8640
```

- [ ] **Step 2: Run capacity tests and verify import failure**

Run: `uv run pytest tests/test_capacity.py -q`

Expected: FAIL because `tikpoc.capacity` does not exist.

- [ ] **Step 3: Implement exact percentile and projection math**

```python
@dataclass(frozen=True)
class DeviceCapacity:
    completed: int
    average_ms: float
    p90_ms: float
    projected_per_day: int
    passed: bool


@dataclass(frozen=True)
class CapacityReport:
    target_count: int
    expected_devices: int
    projected_unique_per_day: int
    devices: dict[str, DeviceCapacity]
    passed: bool
```

Use confirmed visits only. Require average below 6500 ms, p90 below 8640 ms, seven present devices, zero identity mismatches, and complete target-device assignments. Fleet projection is the minimum device projection.

- [ ] **Step 4: Add `tikpoc capacity`**

Accept `--db`, `--batch-id`, `--expected-devices`, `--target-count`, and `--json`. Print per-device average, p90, projected daily visits, coverage, and a final PASS/FAIL verdict. Exit 0 only on PASS.

- [ ] **Step 5: Run capacity and CLI tests**

Run: `uv run pytest tests/test_capacity.py tests/test_cli.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the promotion gate**

```bash
git add src/tikpoc/capacity.py src/tikpoc/db.py src/tikpoc/cli.py tests/test_capacity.py tests/test_cli.py
git commit -m "feat: enforce ten thousand target capacity gate"
```

### Task 6: Full Regression And Seven-Device Live Benchmark

**Files:**
- Modify: `docs/interaction-runbook.md`

- [ ] **Step 1: Document capacity operation**

Add exact import, fleet-run, dashboard, and capacity commands. Document the 70,000-visit calculation, fresh-data identity requirement, 6.5-second average gate, 8.64-second p90 gate, and rule quotas of 100 likes, 14 favorites, and 25 reposts per account-hour.

- [ ] **Step 2: Run automated verification**

Run:

```bash
uv run pytest -q
uv run ruff check .
node --test chrome-event-bridge/core.test.js chrome-event-bridge/dm-core.test.js
```

Expected: all commands exit 0.

- [ ] **Step 3: Run a 100-target single-device smoke**

Use fresh targets with metrics and `secUid`. Confirm zero identity mismatches, exact action selection, confirmed profile checkpoints, and a warm-path p90 below 8.64 seconds before adding devices.

- [ ] **Step 4: Run a 1,000-target seven-device benchmark**

Import every target for `phone-01` through `phone-07`, start `run-fleet`, and execute `tikpoc capacity`. Require 7/7 assignment integrity and all device latency gates.

- [ ] **Step 5: Run the 10,000-target production batch**

Import a fresh 10,000-target workbook, confirm 70,000 assignments, start all seven workers, and monitor coverage plus browser lead health. Count the goal only when the capacity command reports PASS and every target has seven confirmed visits.

- [ ] **Step 6: Commit the capacity runbook**

```bash
git add docs/interaction-runbook.md
git commit -m "docs: add seven device capacity operations"
```
