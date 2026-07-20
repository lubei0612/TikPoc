# Terminal Unavailable Targets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record an explicitly banned, deleted, nonexistent, or visibly unavailable TikTok target once and skip all later assignments for that round/identity without opening the device.

**Architecture:** The Appium adapter raises a dedicated exception only from explicit terminal UI text. The repository persists a round-level terminal profile snapshot and provides a fenced one-attempt skip operation. The mobile worker checks that marker before device activity and preserves the existing three-attempt retry behavior for generic loading failures.

**Tech Stack:** Python 3.14, Appium/Selenium adapter, SQLite, pytest, Ruff.

---

## File Structure

- Modify `src/tikpoc/acquisition_models.py`: add the durable terminal access state.
- Modify `src/tikpoc/device.py`: classify explicit terminal profile pages.
- Modify `src/tikpoc/acquisition_db.py`: atomically publish the terminal marker and skip unconfirmed assignments.
- Modify `src/tikpoc/mobile_worker.py`: immediate current skip and marker-based preflight skip.
- Modify `tests/test_appium_device.py`: terminal UI classification tests.
- Modify `tests/test_acquisition_db.py`: persistence, fencing, and coverage tests.
- Modify `tests/test_mobile_worker.py`: first-observer and sibling-device behavior tests.
- Modify `docs/mobile-fleet-runbook.md`: record runtime behavior and verification commands.

### Task 1: Explicit Terminal UI Classification

**Files:**
- Modify: `src/tikpoc/acquisition_models.py`
- Modify: `src/tikpoc/device.py`
- Test: `tests/test_appium_device.py`

- [ ] **Step 1: Write failing adapter tests**

Add a driver fixture whose page source contains `Account banned` and `is no longer available`, then assert:

```python
from tikpoc.device import ProfilePermanentlyUnavailable


def test_explicit_banned_profile_is_terminal() -> None:
    device = AppiumTikTokDevice(
        TerminalProfileDriver("Account banned\nThis account is no longer available"),
        metric_read_attempts=1,
        poll_interval=0,
    )

    with pytest.raises(ProfilePermanentlyUnavailable, match="banned"):
        device.confirm_profile_identity(_target())


def test_blank_profile_is_not_terminal() -> None:
    device = AppiumTikTokDevice(
        TerminalProfileDriver(""), metric_read_attempts=1, poll_interval=0
    )

    with pytest.raises(ValueError) as captured:
        device.confirm_profile_identity(_target())
    assert not isinstance(captured.value, ProfilePermanentlyUnavailable)
```

- [ ] **Step 2: Verify RED**

Run:

```bash
uv run pytest tests/test_appium_device.py -k 'terminal or banned_profile or blank_profile' -q
```

Expected: collection or assertion failure because `ProfilePermanentlyUnavailable` and terminal-page classification do not exist.

- [ ] **Step 3: Implement the minimal classifier**

Add:

```python
class ProfilePermanentlyUnavailable(ValueError):
    pass


TERMINAL_PROFILE_MARKERS = (
    "account banned",
    "no longer available",
    "account doesn't exist",
    "couldn't find this account",
)


def _terminal_profile_marker(source: str) -> str:
    lowered = source.casefold()
    return next((marker for marker in TERMINAL_PROFILE_MARKERS if marker in lowered), "")
```

Call the helper in stable-route identity polling and `_wait_profile_surface()` before generic parsing. Raise `ProfilePermanentlyUnavailable(marker)` only when a marker is present. Add `PERMANENTLY_UNAVAILABLE = "permanently_unavailable"` to `ProfileAccessState`.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
uv run pytest tests/test_appium_device.py -k 'terminal or banned_profile or blank_profile' -q
uv tool run ruff check src/tikpoc/device.py src/tikpoc/acquisition_models.py tests/test_appium_device.py
```

Expected: focused tests and Ruff pass.

- [ ] **Step 5: Commit**

```bash
git add src/tikpoc/acquisition_models.py src/tikpoc/device.py tests/test_appium_device.py
git commit -m "feat: classify terminal unavailable profiles"
```

### Task 2: Durable Terminal Marker And One-Attempt Skip

**Files:**
- Modify: `src/tikpoc/acquisition_db.py`
- Test: `tests/test_acquisition_db.py`

- [ ] **Step 1: Write failing repository tests**

Add tests that claim the first device assignment and call the wished-for API:

```python
snapshot, skipped = repository.record_permanently_unavailable(
    claimed.assignment_id,
    "worker-01",
    observed_username="target",
    observed_by_device_id="phone-01",
    now_ms=1_100,
    diagnostics=DeviceDiagnostics(ui_summary="explicit unavailable marker"),
)

assert snapshot.access_state is ProfileAccessState.PERMANENTLY_UNAVAILABLE
assert snapshot.eligible is False
assert snapshot.reason == "permanently_unavailable"
assert skipped.phase is AssignmentPhase.SKIPPED
assert skipped.attempt_count == 1
assert skipped.visit_confirmed_at_ms is None
assert skipped.last_error_code == "profile_permanently_unavailable"
```

Also assert the operation rejects an assignment with a confirmed visit and that round coverage remains zero.

- [ ] **Step 2: Verify RED**

Run:

```bash
uv run pytest tests/test_acquisition_db.py -k permanently_unavailable -q
```

Expected: failure because `record_permanently_unavailable` does not exist.

- [ ] **Step 3: Implement one atomic repository operation**

Implement:

```python
def record_permanently_unavailable(
    self,
    assignment_id: int,
    owner_id: str,
    *,
    observed_username: str,
    observed_by_device_id: str,
    now_ms: int,
    diagnostics: DeviceDiagnostics,
    worker_account_id: str | None = None,
    worker_fence_token: int | None = None,
) -> tuple[ProfileSnapshot, RoundAssignment]:
    ...
```

Within one `BEGIN IMMEDIATE` transaction:

1. Validate the active owner, device fence, `profile_opening` phase, and absent confirmed visit.
2. Insert or replace the round/identity snapshot with null metrics, `private_account=0`, `access_state='permanently_unavailable'`, `eligible=0`, and `reason='permanently_unavailable'`.
3. Set the current assignment to `skipped`, release the lease, and set `completed_at_ms` plus `last_error_code='profile_permanently_unavailable'` without requiring three attempts.
4. Insert phase history containing attempt count, error code, screenshot path, and UI summary.

Add a separate fenced method for a claimed sibling assignment:

```python
def skip_marked_permanently_unavailable(
    self,
    assignment_id: int,
    owner_id: str,
    *,
    now_ms: int,
    worker_account_id: str | None = None,
    worker_fence_token: int | None = None,
) -> RoundAssignment:
    ...
```

It must verify the matching terminal snapshot exists before skipping.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
uv run pytest tests/test_acquisition_db.py -k permanently_unavailable -q
uv tool run ruff check src/tikpoc/acquisition_db.py tests/test_acquisition_db.py
```

Expected: focused tests and Ruff pass.

- [ ] **Step 5: Commit**

```bash
git add src/tikpoc/acquisition_db.py tests/test_acquisition_db.py
git commit -m "feat: persist terminal unavailable targets"
```

### Task 3: Worker Immediate And Cross-Device Skip

**Files:**
- Modify: `src/tikpoc/mobile_worker.py`
- Test: `tests/test_mobile_worker.py`

- [ ] **Step 1: Write failing worker tests**

Add one test whose device raises the dedicated exception on the first identity confirmation. Assert the assignment skips at attempt one with no confirmed visit. Then claim the same identity for a second device and assert:

```python
second_worker.run_assignment(second_claim)

assert repository.assignment(second_claim.assignment_id).phase is AssignmentPhase.SKIPPED
assert second_device.opened_profiles == []
assert second_device.diagnostic_calls == 0
```

Keep the existing `test_profile_opening_value_error_skips_only_after_three_claims` unchanged to prove generic failures still receive bounded retries.

- [ ] **Step 2: Verify RED**

Run:

```bash
uv run pytest tests/test_mobile_worker.py -k 'permanently_unavailable or profile_opening_value_error' -q
```

Expected: terminal tests fail because the worker defers the dedicated error and does not preflight the marker.

- [ ] **Step 3: Implement minimal worker branching**

At the start of `_run_claimed`, before `ensure_ready()`, load the profile snapshot. If its access state is `PERMANENTLY_UNAVAILABLE`, call `skip_marked_permanently_unavailable()` and return.

Catch `ProfilePermanentlyUnavailable` separately around profile confirmation and call:

```python
self.repository.record_permanently_unavailable(
    assignment.assignment_id,
    self.owner_id,
    observed_username=assignment.username,
    observed_by_device_id=self.device_id,
    now_ms=self.clock_ms(),
    diagnostics=self._capture_diagnostics(),
    **self._assignment_fence_kwargs(),
)
```

Do not change `MAX_PROFILE_OPEN_ATTEMPTS` or the `ProfileUnreachable` branch.

- [ ] **Step 4: Verify GREEN and applicable regression**

Run:

```bash
uv run pytest tests/test_mobile_worker.py -k 'permanently_unavailable or profile_opening_value_error' -q
uv run pytest tests/test_appium_device.py tests/test_acquisition_db.py tests/test_mobile_worker.py -q
uv tool run ruff check src/tikpoc/acquisition_models.py src/tikpoc/device.py src/tikpoc/acquisition_db.py src/tikpoc/mobile_worker.py tests/test_appium_device.py tests/test_acquisition_db.py tests/test_mobile_worker.py
uv tool run ruff format --check src/tikpoc/acquisition_models.py src/tikpoc/device.py src/tikpoc/acquisition_db.py src/tikpoc/mobile_worker.py tests/test_appium_device.py tests/test_acquisition_db.py tests/test_mobile_worker.py
git diff --check
```

Expected: all focused tests, lint, formatting, and diff checks pass.

- [ ] **Step 5: Commit**

```bash
git add src/tikpoc/mobile_worker.py tests/test_mobile_worker.py
git commit -m "feat: skip recorded unavailable targets across devices"
```

### Task 4: Full Regression And Live Fleet Verification

**Files:**
- Modify: `docs/mobile-fleet-runbook.md`

- [ ] **Step 1: Run the complete regression**

```bash
uv run pytest -q
uv tool run ruff check src tests
uv tool run ruff format --check src tests
git diff --check
```

Expected: all repository-owned tests pass; any pre-existing unrelated dirty-test failure is recorded separately and not hidden.

- [ ] **Step 2: Deploy through a controlled fleet restart**

Pause the round, wait for all active assignment leases to release, stop the current fleet process, start the same round and database from the updated worktree, then resume the round. Preserve the durable checkpoint and CSV source.

- [ ] **Step 3: Verify visible terminal behavior**

Use one known explicit terminal target fixture. Confirm the first assignment records `profile_permanently_unavailable`, later device assignments skip without visible navigation, and confirmed coverage remains zero for that identity.

- [ ] **Step 4: Run a six-device observation window**

Observe at least 15 minutes and report per-device measured hourly completion rate, profile-opening error count, action-uncertain count, route/identity/video/action timing, proxy HTTP latency, and visible UI health. Compare slots 2, 3, and 6 against slots 1, 4, and 5 before making another optimization.

- [ ] **Step 5: Update the runbook and commit**

Document the explicit terminal-marker boundary, the difference from generic unreachable retries, the live evidence, and rollback steps.

```bash
git add docs/mobile-fleet-runbook.md
git commit -m "docs: record terminal target fleet behavior"
```

- [ ] **Step 6: Push reviewed commits**

```bash
git status --short --branch
git push origin feat/web-lead-conversion
```

Expected: the branch is pushed without adding unrelated local modifications.
