# Windowed Coverage And Throughput Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver at least 125 confirmed profile visits/hour/device with fast terminal errors and shared 100-target coverage windows before six-device production resumes.

**Architecture:** SQLite remains authoritative for assignment state, window barriers, coverage, and idempotency. The server only exposes assignments from the earliest nonterminal 100-target window; the autonomous APK performs one forward pass and reports every error or uncertain interaction as terminal without reopening the profile or video. Phase timing and stuck-task queries provide the live acceptance evidence.

**Tech Stack:** Python 3.14, SQLite, pytest, Java 8 Android accessibility executor, shell Android build, Alibaba ECS and VMOS live gates.

---

### Task 1: Make Fast-Path Errors Terminal

**Files:**
- Modify: `src/tikpoc/acquisition_db.py`
- Modify: `tests/test_mobile_api.py`

- [ ] Add `test_mobile_forward_error_is_terminal_and_not_reclaimed` that registers a device, pulls an assignment, uploads a deferred `profile_opening` result, and asserts the assignment is `skipped`, retains the error code, and does not appear in the next pull.
- [ ] Add `test_mobile_error_after_identity_preserves_confirmed_visit` that first uploads `profile_observed`, then uploads a deferred `video_opening` result and asserts `visit_confirmed_at_ms` remains populated while the assignment becomes terminal.
- [ ] Run `uv run pytest tests/test_mobile_api.py -q` and require both new assertions to fail because `_apply_mobile_profile_opening_result` and unhandled continuation failures remain retryable.
- [ ] Add one repository helper:

```python
def terminate_mobile_assignment(
    self,
    assignment_id: int,
    owner_id: str,
    *,
    now_ms: int,
    error_code: str,
) -> RoundAssignment:
    assignment = self.assignment(assignment_id)
    if assignment.visit_confirmed_at_ms is None:
        return self.skip_unreachable_assignment(
            assignment_id,
            owner_id,
            now_ms=now_ms,
            error_code=error_code,
            original_error_code=error_code,
            failure_stage="fast_path",
            diagnostics=DeviceDiagnostics(),
        )
    return self.complete_assignment(
        assignment_id,
        owner_id,
        assignment.phase,
        now_ms=now_ms,
        terminal_error_code=error_code,
        completion_details={"reason": "fast_path_error"},
    )
```

- [ ] Route deferred/skipped mobile results from `profile_opening`, `identity_confirmed`, and `video_opening` through this helper; retain the existing action-plan transitions for action results.
- [ ] Run `uv run pytest tests/test_mobile_api.py tests/test_acquisition_db.py -q` and require pass.
- [ ] Commit with `git commit -m "feat: terminate mobile targets on first error"`.

### Task 2: Remove Automatic Action Reconciliation

**Files:**
- Modify: `android-touch-executor/src/com/tikpoc/touch/AutonomousTaskExecutor.java`
- Modify: `android-touch-executor/test/com/tikpoc/touch/AutonomousTaskExecutorTest.java`
- Modify: `src/tikpoc/acquisition_db.py`
- Modify: `tests/test_mobile_api.py`

- [ ] Replace the existing reconciliation-follow-up test with a failing test asserting an unverified action returns `state=uncertain`, `phase=action_executing`, retains `plan_id`, and becomes terminal after one upload.
- [ ] Add a Java test asserting a task received in historical `action_reconciling` reports one terminal deferred result without clicking or reopening after the first bounded observation.
- [ ] Run `bash android-touch-executor/build.sh` and `uv run pytest tests/test_mobile_api.py -q`; observe the old reconciliation task and server follow-up assertions fail.
- [ ] Change the APK unverified-action result to:

```java
return actionResult(
        task, target, "action_executing", "action_unverified", "uncertain");
```

and delete the path that requests a second `observeAction` execution for new work.
- [ ] In `record_mobile_result`, handle an uncertain `action_executing` result by recording the plan uncertain and completing the assignment with terminal error `action_uncertain_terminal`; do not transition to `ACTION_RECONCILING`.
- [ ] Keep compatibility for historical `ACTION_RECONCILING` assignments by terminating them on their next result.
- [ ] Run `bash android-touch-executor/build.sh` and `uv run pytest tests/test_mobile_api.py tests/test_device_api.py -q` and require pass.
- [ ] Commit with `git commit -m "perf: make uncertain interactions terminal"`.

### Task 3: Cap Current-Surface Action Observation At 1.5 Seconds

**Files:**
- Modify: `android-touch-executor/src/com/tikpoc/touch/TouchCommandDispatcher.java`
- Modify: `android-touch-executor/test/com/tikpoc/touch/TouchCommandDispatcherTest.java`

- [ ] Add a fake clock/source test proving `apply_action` makes at most three 500ms post-click observations and performs exactly one click when state remains unavailable.
- [ ] Run `bash android-touch-executor/build.sh` and observe failure because the current loop permits ten observations.
- [ ] Replace the action observation loop with:

```java
for (int attempt = 0; attempt < 3; attempt++) {
    after = snapshots.awaitAfter(after.eventSequence, 500L);
    // Existing selected-state and visible-counter checks remain unchanged.
}
```

- [ ] Keep the visible selected descendant, counter-increment, and repost confirmation evidence; return uncertain after the third observation.
- [ ] Run `bash android-touch-executor/build.sh` and require every Java test pass.
- [ ] Commit with `git commit -m "perf: bound interaction verification latency"`.

### Task 4: Add Shared 100-Target Window Barriers

**Files:**
- Modify: `src/tikpoc/acquisition_db.py`
- Modify: `src/tikpoc/rounds.py`
- Modify: `tests/test_acquisition_db.py`
- Modify: `tests/test_rounds.py`

- [ ] Add round tests proving `device_order_key` sorts first by a stable 100-target pool window and then by the independent per-device hash inside the window.
- [ ] Add a repository test with 201 targets and two devices proving neither device can claim window 1 while either device has a nonterminal assignment in window 0.
- [ ] Add a test proving terminal `completed`, `skipped`, and terminal-error assignments all release the window barrier.
- [ ] Run `uv run pytest tests/test_rounds.py tests/test_acquisition_db.py -q` and observe current global hash ordering violates the window assertions.
- [ ] Introduce pure helpers:

```python
WINDOW_SIZE = 100

def coverage_window(index: int, *, size: int = WINDOW_SIZE) -> int:
    if index < 0 or size <= 0:
        raise ValueError("invalid coverage window")
    return index // size

def windowed_order_key(
    round_id: str,
    device_seed: str,
    identity_key: str,
    target_index: int,
) -> str:
    window = coverage_window(target_index)
    shuffled = device_order_key(round_id, device_seed, identity_key)
    return f"{window:08d}:{shuffled}"
```

- [ ] Build round order keys from the stable pool-target index and `windowed_order_key`.
- [ ] Restrict `claim_scheduled_assignment` to the minimum window containing any nonterminal assignment for the round, while retaining device-specific shuffled ordering inside that window.
- [ ] Run `uv run pytest tests/test_rounds.py tests/test_acquisition_db.py tests/test_priority_coverage.py tests/test_fleet_runtime.py -q` and require pass.
- [ ] Commit with `git commit -m "feat: coordinate devices through coverage windows"`.

### Task 5: Add Throughput And Stuck-Task Acceptance Metrics

**Files:**
- Modify: `src/tikpoc/acquisition_db.py`
- Modify: `src/tikpoc/api_models.py`
- Modify: `src/tikpoc/acquisition_service.py`
- Modify: `tests/test_acquisition_service.py`

- [ ] Add a failing service test with timestamped visits asserting per-device 15-minute and 60-minute confirmed visit rates, mean/p50/p90 duration, and coverage distribution are returned.
- [ ] Add a failing test asserting an assignment is reported stuck when nonterminal beyond twice its configured phase budget or `attempt_count > 1`.
- [ ] Run `uv run pytest tests/test_acquisition_service.py -q` and observe missing fields.
- [ ] Add immutable API fields:

```python
confirmed_visits_15m: int
confirmed_visits_60m: int
confirmed_rate_15m: float
confirmed_rate_60m: float
p50_ms: int
stuck_assignments: int
```

- [ ] Compute these values using `visit_confirmed_at_ms`, phase history, and assignment attempts; do not count claims or receipts as visits.
- [ ] Add window coverage counts for 1/N through N/N using distinct confirmed device ids.
- [ ] Run `uv run pytest tests/test_acquisition_service.py tests/test_api.py -q` and require pass.
- [ ] Commit with `git commit -m "feat: report live mobile throughput gates"`.

### Task 6: Regression, Deployment, And Live Promotion Gates

**Files:**
- Modify: `docs/superpowers/specs/2026-07-28-windowed-coverage-throughput-design.md` only if live evidence requires a clarification.
- Use ignored local server and VMOS configuration; do not commit secrets, SQLite files, logs, screenshots, or CSV data.

- [ ] Run `uv run pytest -q`, `uv tool run ruff check src tests`, `uv tool run ruff format --check src tests`, `bash android-touch-executor/build.sh`, and `git diff --check`.
- [ ] Build the APK, deploy it to one VMOS device, fully restart the accessibility service, and verify TikTok foreground plus current helper health.
- [ ] Create a fresh 20-target Deeplink fast-path canary. Require exact identity evidence, zero duplicate visits/actions, no assignment attempt above one, and no nonterminal assignment beyond twice its phase budget.
- [ ] Create a fresh representative 100-target canary. Require at least 125 confirmed visits/hour, interaction plan retention, terminal errors on first occurrence, and no automatic action reconciliation receipt.
- [ ] Run an uninterrupted 30-minute gate with enough targets. Require at least 125 confirmed visits/hour/device and stable mean/p90 without stuck assignments.
- [ ] Deploy the verified APK and server changes to all six VMOS devices.
- [ ] Resume from the saved production checkpoint using a shared 100-target window. Require each device at least 125 confirmed visits/hour and verify the 6/6 coverage count rises at each completed window.
- [ ] Keep the fleet running only after every gate passes; otherwise pause, preserve evidence, and return to the failing task with a new red-green cycle.
- [ ] Push all verified commits to `origin/feat/web-lead-conversion` and update the plan checkpoint with measured results.
