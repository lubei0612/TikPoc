# TikTok Single-Device POC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove that one Android ARM64 emulator can reliably process a CSV of authorized TikTok test profiles, read profile metrics, evaluate the profile rule, open a random qualifying post, recover from failures, and resume after an hourly app restart.

**Architecture:** A Python CLI imports immutable batches into SQLite and runs one worker against an Appium UiAutomator2 adapter. Pure parsing and rule modules are isolated from TikTok selectors; the device adapter exposes semantic operations so a future ADB cloud phone can replace the emulator without changing the worker.

**Tech Stack:** Python 3.12, pytest, SQLite, PyYAML, Appium Python Client, Appium 2, UiAutomator2, Android Studio Emulator.

---

## File Map

- `pyproject.toml`: package metadata, runtime dependencies, pytest configuration, CLI entry point.
- `src/tikpoc/config.py`: validated YAML configuration.
- `src/tikpoc/models.py`: shared immutable domain records and enums.
- `src/tikpoc/counts.py`: locale-tolerant visible-count parsing.
- `src/tikpoc/rules.py`: pure profile eligibility decision.
- `src/tikpoc/importer.py`: structured CSV validation and normalization.
- `src/tikpoc/db.py`: SQLite schema, migrations, task claims, checkpoints, and results.
- `src/tikpoc/device.py`: semantic device protocol and Appium implementation.
- `src/tikpoc/worker.py`: single-task state machine and recovery policy.
- `src/tikpoc/cli.py`: `init-db`, `validate`, `import`, `run`, and `status` commands.
- `config/settings.example.yaml`: nonsecret emulator and runtime configuration.
- `tests/`: unit and component tests using fake devices.
- `scripts/check_android_env.sh`: deterministic ADB/Appium environment check.
- `docs/emulator-setup.md`: manual Android Studio, AVD, TikTok login, and Inspector checklist.

## Task 1: Bootstrap The Tested Python Package

**Files:**
- Create: `pyproject.toml`
- Create: `src/tikpoc/__init__.py`
- Create: `src/tikpoc/models.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Write the failing model test**

```python
from tikpoc.models import ProfileMetrics


def test_profile_metrics_rejects_negative_values() -> None:
    try:
        ProfileMetrics(following=-1, followers=2, posts=4)
    except ValueError as error:
        assert str(error) == "profile metrics must be nonnegative"
    else:
        raise AssertionError("negative metrics were accepted")
```

- [ ] **Step 2: Add package configuration and verify the test fails**

```toml
[build-system]
requires = ["setuptools>=75"]
build-backend = "setuptools.build_meta"

[project]
name = "tikpoc"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["Appium-Python-Client>=5,<6", "PyYAML>=6,<7"]

[project.optional-dependencies]
test = ["pytest>=8,<9"]

[project.scripts]
tikpoc = "tikpoc.cli:main"

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

Run: `python3 -m pytest tests/test_models.py -v`

Expected: FAIL because `tikpoc.models` does not exist.

- [ ] **Step 3: Implement the minimal domain model**

```python
from dataclasses import dataclass
from enum import StrEnum


class TaskState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    RETRY_WAIT = "retry_wait"
    FAILED = "failed"


@dataclass(frozen=True)
class ProfileMetrics:
    following: int
    followers: int
    posts: int

    def __post_init__(self) -> None:
        if min(self.following, self.followers, self.posts) < 0:
            raise ValueError("profile metrics must be nonnegative")
```

- [ ] **Step 4: Run the test suite**

Run: `python3 -m pytest -v`

Expected: PASS with one test.

- [ ] **Step 5: Commit the bootstrap**

```bash
git add pyproject.toml src/tikpoc tests/test_models.py
git commit -m "chore: bootstrap single-device poc"
```

## Task 2: Implement Count Parsing And Profile Rules

**Files:**
- Create: `src/tikpoc/counts.py`
- Create: `src/tikpoc/rules.py`
- Create: `tests/test_counts.py`
- Create: `tests/test_rules.py`

- [ ] **Step 1: Write failing boundary tests**

```python
import pytest

from tikpoc.counts import parse_visible_count
from tikpoc.models import ProfileMetrics
from tikpoc.rules import evaluate_profile


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1,234", 1234), ("1.2K", 1200), ("2M", 2_000_000), ("0", 0)],
)
def test_parse_visible_count(raw: str, expected: int) -> None:
    assert parse_visible_count(raw) == expected


def test_rule_requires_both_strict_inequalities() -> None:
    assert evaluate_profile(ProfileMetrics(11, 10, 4)).eligible is True
    assert evaluate_profile(ProfileMetrics(10, 10, 4)).eligible is False
    assert evaluate_profile(ProfileMetrics(11, 10, 3)).eligible is False
```

- [ ] **Step 2: Run tests and confirm missing modules**

Run: `python3 -m pytest tests/test_counts.py tests/test_rules.py -v`

Expected: FAIL during import.

- [ ] **Step 3: Implement deterministic parsing and decision reasons**

```python
# src/tikpoc/counts.py
from decimal import Decimal, InvalidOperation


_MULTIPLIERS = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}


def parse_visible_count(raw: str) -> int:
    value = raw.strip().upper().replace(",", "")
    suffix = value[-1] if value and value[-1] in _MULTIPLIERS else ""
    number = value[:-1] if suffix else value
    try:
        parsed = Decimal(number) * _MULTIPLIERS[suffix]
    except InvalidOperation as error:
        raise ValueError(f"unreadable count: {raw!r}") from error
    if parsed < 0 or parsed != parsed.to_integral_value():
        raise ValueError(f"unreadable count: {raw!r}")
    return int(parsed)
```

```python
# src/tikpoc/rules.py
from dataclasses import dataclass

from .models import ProfileMetrics


@dataclass(frozen=True)
class RuleDecision:
    eligible: bool
    reasons: tuple[str, ...]


def evaluate_profile(metrics: ProfileMetrics) -> RuleDecision:
    reasons: list[str] = []
    if metrics.following <= metrics.followers:
        reasons.append("following_not_greater_than_followers")
    if metrics.posts <= 3:
        reasons.append("post_count_not_greater_than_three")
    return RuleDecision(eligible=not reasons, reasons=tuple(reasons))
```

- [ ] **Step 4: Run focused and full tests**

Run: `python3 -m pytest tests/test_counts.py tests/test_rules.py -v && python3 -m pytest -v`

Expected: all tests PASS.

- [ ] **Step 5: Commit parsing and rules**

```bash
git add src/tikpoc/counts.py src/tikpoc/rules.py tests/test_counts.py tests/test_rules.py
git commit -m "feat: add profile metric rules"
```

## Task 3: Add CSV Import And Durable Task Storage

**Files:**
- Create: `src/tikpoc/importer.py`
- Create: `src/tikpoc/db.py`
- Create: `tests/test_importer.py`
- Create: `tests/test_db.py`

- [ ] **Step 1: Write failing import and recovery tests**

```python
from pathlib import Path

from tikpoc.db import Database
from tikpoc.importer import read_targets


def test_import_normalizes_and_deduplicates_username(tmp_path: Path) -> None:
    source = tmp_path / "targets.csv"
    source.write_text(
        "target_id,username,profile_url,enabled,notes\n"
        "1,@Sample,,true,a\n"
        "2,sample,,true,b\n",
        encoding="utf-8",
    )
    result = read_targets(source)
    assert [target.username for target in result.targets] == ["sample"]
    assert result.skipped_duplicates == 1


def test_stale_running_task_returns_to_retry_wait(tmp_path: Path) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    database.insert_task("batch", "1", "sample")
    task = database.claim_next()
    assert task is not None
    database.recover_stale_tasks()
    assert database.task_state(task.id) == "retry_wait"
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `python3 -m pytest tests/test_importer.py tests/test_db.py -v`

Expected: FAIL during import.

- [ ] **Step 3: Implement CSV records and SQLite transitions**

Implement `Target`, `ImportResult`, and `Task` frozen dataclasses. Use `csv.DictReader`, lowercase usernames, strip a leading `@`, reject missing or duplicate `target_id`, and preserve the first normalized username. Implement SQLite schema version 1 with `tasks(id, batch_id, target_id, username, state, attempts, checkpoint, error_code, created_at, updated_at)`. Use `BEGIN IMMEDIATE` in `claim_next`, and restrict state changes to the transitions in the design.

The concrete repository methods must be:

```python
class Database:
    def __init__(self, path: Path) -> None: ...
    def migrate(self) -> None: ...
    def insert_task(self, batch_id: str, target_id: str, username: str) -> int: ...
    def claim_next(self) -> Task | None: ...
    def checkpoint(self, task_id: int, value: str) -> None: ...
    def finish(self, task_id: int, state: TaskState, error_code: str | None = None) -> None: ...
    def recover_stale_tasks(self) -> int: ...
    def task_state(self, task_id: int) -> str: ...
```

- [ ] **Step 4: Run database tests twice to expose persistent-state errors**

Run: `python3 -m pytest tests/test_importer.py tests/test_db.py -v && python3 -m pytest tests/test_db.py -v`

Expected: both runs PASS.

- [ ] **Step 5: Commit durable task input**

```bash
git add src/tikpoc/importer.py src/tikpoc/db.py tests/test_importer.py tests/test_db.py
git commit -m "feat: add csv tasks and sqlite recovery"
```

## Task 4: Define The Device Boundary And Worker State Machine

**Files:**
- Create: `src/tikpoc/device.py`
- Create: `src/tikpoc/worker.py`
- Create: `tests/fakes.py`
- Create: `tests/test_worker.py`

- [ ] **Step 1: Write a failing end-to-end worker test with a fake device**

```python
from pathlib import Path

from tikpoc.db import Database
from tikpoc.models import ProfileMetrics
from tikpoc.worker import Worker
from tests.fakes import FakeDevice


def test_worker_opens_one_random_post_for_eligible_profile(tmp_path: Path) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    database.insert_task("batch", "1", "sample")
    device = FakeDevice(metrics=ProfileMetrics(20, 10, 5), posts=("a", "b", "c"))
    Worker(database, device, random_seed=7).run_one()
    assert database.task_state(1) == "completed"
    assert device.opened_profiles == ["sample"]
    assert device.opened_posts[0] in {"a", "b", "c"}
```

- [ ] **Step 2: Run the worker test and verify import failure**

Run: `python3 -m pytest tests/test_worker.py -v`

Expected: FAIL because the device protocol and worker do not exist.

- [ ] **Step 3: Implement semantic device operations and one-task execution**

```python
from typing import Protocol

from .models import ProfileMetrics


class Device(Protocol):
    def ensure_ready(self) -> None: ...
    def open_profile(self, username: str) -> None: ...
    def read_profile_metrics(self) -> ProfileMetrics: ...
    def list_visible_posts(self) -> tuple[str, ...]: ...
    def open_post(self, post_id: str) -> None: ...
    def return_to_baseline(self) -> None: ...
    def restart_app(self) -> None: ...
```

Implement `Worker.run_one()` to claim one task, open the profile, persist `profile_opened`, evaluate the rule, mark ineligible profiles `skipped`, choose a post with `random.Random(random_seed).choice`, persist `post_opened:<id>`, return to baseline, and mark the task completed. Exceptions enter `retry_wait` until the configured attempt limit and then become failed.

- [ ] **Step 4: Add and run failure-injection tests**

Add tests where `open_profile` raises once, where posts are empty, and where metric reading always fails. Assert later tasks remain claimable and no completed task is claimed twice.

Run: `python3 -m pytest tests/test_worker.py -v && python3 -m pytest -v`

Expected: all tests PASS.

- [ ] **Step 5: Commit the device-independent worker**

```bash
git add src/tikpoc/device.py src/tikpoc/worker.py tests/fakes.py tests/test_worker.py
git commit -m "feat: add single-device worker state machine"
```

## Task 5: Add Appium Adapter And Environment Verification

**Files:**
- Modify: `src/tikpoc/device.py`
- Create: `src/tikpoc/config.py`
- Create: `config/settings.example.yaml`
- Create: `scripts/check_android_env.sh`
- Create: `tests/test_config.py`
- Create: `docs/emulator-setup.md`

- [ ] **Step 1: Write configuration validation tests**

```python
from pathlib import Path

import pytest

from tikpoc.config import load_settings


def test_settings_require_appium_url_and_udid(tmp_path: Path) -> None:
    config = tmp_path / "settings.yaml"
    config.write_text("device:\n  udid: ''\n", encoding="utf-8")
    with pytest.raises(ValueError, match="device.udid"):
        load_settings(config)
```

- [ ] **Step 2: Run the test and verify failure**

Run: `python3 -m pytest tests/test_config.py -v`

Expected: FAIL because `tikpoc.config` does not exist.

- [ ] **Step 3: Implement validated settings and Appium session creation**

Use `yaml.safe_load` and frozen dataclasses. Require `device.udid`, `device.appium_url`, and `device.app_package`; validate positive timeouts and retry counts. Implement `AppiumTikTokDevice` with `UiAutomator2Options` and capabilities `platformName=Android`, `automationName=UiAutomator2`, `udid`, `appPackage`, `noReset=true`, and `newCommandTimeout=180`.

Keep TikTok selectors in a versioned `SelectorSet` dataclass. Do not use coordinates unless a selector failure artifact proves no semantic selector is available.

- [ ] **Step 4: Add deterministic environment checks**

```bash
#!/usr/bin/env bash
set -euo pipefail
command -v adb >/dev/null
command -v node >/dev/null
command -v appium >/dev/null
adb get-state | grep -qx device
appium driver doctor uiautomator2
printf 'Android/Appium environment is ready.\n'
```

Document exact AVD settings, Google Play login, manual TikTok installation/login, `adb shell pm list packages`, Appium Inspector connection, package/activity discovery, and capture of sanitized profile/post page sources.

- [ ] **Step 5: Run automated checks available without a live emulator**

Run: `python3 -m pytest -v && bash -n scripts/check_android_env.sh`

Expected: Python tests PASS and shell syntax check exits 0.

- [ ] **Step 6: Commit the Appium boundary**

```bash
git add src/tikpoc/device.py src/tikpoc/config.py config scripts tests/test_config.py docs/emulator-setup.md
git commit -m "feat: add appium emulator adapter"
```

## Task 6: Add CLI, Hourly Restart, And POC Evidence

**Files:**
- Create: `src/tikpoc/cli.py`
- Modify: `src/tikpoc/worker.py`
- Create: `tests/test_cli.py`
- Create: `tests/test_restart.py`
- Create: `docs/poc-runbook.md`

- [ ] **Step 1: Write failing CLI and restart tests**

```python
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tikpoc.worker import RestartClock


def test_restart_clock_becomes_due_after_one_hour() -> None:
    start = datetime(2026, 7, 11, 0, 0, tzinfo=timezone.utc)
    clock = RestartClock(started_at=start, interval=timedelta(hours=1))
    assert clock.is_due(start + timedelta(minutes=59)) is False
    assert clock.is_due(start + timedelta(hours=1)) is True
```

- [ ] **Step 2: Run focused tests and verify failure**

Run: `python3 -m pytest tests/test_cli.py tests/test_restart.py -v`

Expected: FAIL because CLI and restart clock do not exist.

- [ ] **Step 3: Implement CLI commands and restart checkpoint**

Use `argparse` subcommands:

```text
tikpoc init-db --db data/tasks.db
tikpoc validate data/targets.csv
tikpoc import data/targets.csv --db data/tasks.db
tikpoc run --db data/tasks.db --config config/settings.yaml
tikpoc status --db data/tasks.db
```

Before a scheduled restart, stop claiming tasks, finish the current semantic operation, persist the current checkpoint, call `restart_app`, call `ensure_ready`, reset the restart clock, and continue. A restart failure must release the task into `retry_wait` and must not terminate the worker loop.

- [ ] **Step 4: Run all automated verification**

Run: `python3 -m pytest -v`

Expected: all tests PASS.

- [ ] **Step 5: Run the live single-device smoke test**

Run:

```bash
bash scripts/check_android_env.sh
tikpoc init-db --db data/tasks.db
tikpoc validate data/targets.csv
tikpoc import data/targets.csv --db data/tasks.db
tikpoc run --db data/tasks.db --config config/settings.yaml
```

Expected: the emulator is detected, an authorized test profile is opened, raw and normalized metrics are persisted, an eligible profile opens one visible post, and the task reaches `completed` without performing engagement actions.

- [ ] **Step 6: Run recovery and endurance acceptance**

During a controlled test, force-stop TikTok once and stop/restart the worker once. Then run for four hours against authorized test data. Record task counts, failure categories, restart events, duplicate count, and maximum recovery time in `docs/poc-results.md`.

Expected: no duplicate completed tasks, no permanent stall, hourly app restart recorded, and incomplete work resumes from SQLite.

- [ ] **Step 7: Commit the verified POC**

```bash
git add src/tikpoc/cli.py src/tikpoc/worker.py tests/test_cli.py tests/test_restart.py docs/poc-runbook.md docs/poc-results.md
git commit -m "feat: complete single-device tiktok poc"
```

## POC Decision Gate

Proceed beyond one device only when all of the following are evidenced:

- TikTok installs and remains logged in on the ARM64 AVD.
- Appium retrieves stable profile and post page trees.
- Required counts parse correctly on at least 20 authorized test profiles.
- One hundred queued tasks finish without duplication or permanent blocking.
- A forced app failure and a forced worker restart both recover from SQLite.
- The four-hour run completes with bounded memory and an hourly app restart.

Automated public engagement volume and multi-account coordination are not part of this POC. Any subsequent acquisition system should use TikTok's approved business, advertising, lead-generation, or explicitly authorized integration surfaces.
