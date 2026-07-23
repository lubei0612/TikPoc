# Mobile Retry Starvation Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove mobile Inbox recovery, stop deferred retry starvation, restore the same six-device round, and verify stable business-preserving throughput before beginning the broader code audit.

**Architecture:** Keep the existing durable round, plans, quota evidence, and worker boundaries. Change only the Appium stable-route recovery and repository claim/defer policy: restart directly into the target route, select pending work before deferred work, and place fully reconciled uncertain or confirmed-visit-unreachable work into manual retry hold.

**Tech Stack:** Python 3.12, SQLite, Appium UiAutomator2, ADB, pytest, Ruff, Git/GitHub.

---

### Task 1: Remove Inbox From Mobile Profile Recovery

**Files:**
- Modify: `src/tikpoc/device.py`
- Modify: `tests/test_appium_device.py`

- [ ] **Step 1: Replace baseline-oriented test fixtures with a restart-to-route fixture**

Create a fake driver whose first stable route leaves the preceding profile visible,
whose `terminate_app()` marks the process restarted, and whose next stable route
loads the renamed target. Assert that `activate_app()` is not called during this
recovery.

- [ ] **Step 2: Write failing tests for Inbox-free recovery**

```python
def test_stable_route_recovery_restarts_directly_into_target_without_inbox():
    driver = RestartDirectStableRouteDriver()
    device = AppiumTikTokDevice(driver, metric_read_attempts=1, poll_interval=0)

    device.open_target(_stable_target())
    device.confirm_profile_identity(_stable_target())

    assert driver.terminate_calls == 1
    assert driver.activate_calls == 0
    assert "tiktok://inbox" not in [item[1]["url"] for item in driver.scripts]
    assert driver.scripts[-1][1]["url"] == "snssdk1233://user/profile/123"
```

Also assert that a restarted stable route which still exposes the preceding
username is rejected and falls back to the exact username URL.

- [ ] **Step 3: Run the tests and observe the expected failure**

Run:

```bash
uv run pytest tests/test_appium_device.py -k 'stable_route or inbox' -q
```

Expected: the new assertions fail because `confirm_profile_identity()` still
routes through `tiktok://inbox` and calls `activate_app()`.

- [ ] **Step 4: Implement direct restart routing**

Add a private helper that invalidates cached profile state, terminates TikTok,
and invokes `_open_route(uri)` without activating the previously selected tab.
Replace both Inbox baseline branches with one direct restart to the stable URI.
Require the restarted profile username to differ from the pre-route username;
otherwise continue to the existing exact-username fallback.

- [ ] **Step 5: Run focused verification**

```bash
uv run pytest tests/test_appium_device.py -q
uv tool run ruff check src/tikpoc/device.py tests/test_appium_device.py
uv tool run ruff format --check src/tikpoc/device.py tests/test_appium_device.py
git diff --check
```

- [ ] **Step 6: Commit Task 1**

```bash
git add src/tikpoc/device.py tests/test_appium_device.py
git commit -m "fix: remove inbox from mobile route recovery"
```

### Task 2: Prevent Deferred Work From Starving The Target Pool

**Files:**
- Modify: `src/tikpoc/acquisition_db.py`
- Modify: `src/tikpoc/mobile_worker.py`
- Modify: `tests/test_acquisition_db.py`
- Modify: `tests/test_mobile_worker.py`

- [ ] **Step 1: Write a failing repository claim-order test**

Create two assignments on one device, defer the lower-order assignment so it is
due, leave the other pending, then claim again:

```python
claimed = repository.claim_next_assignment(
    "round-01", "phone-01", "worker-2", now_ms=2_000
)
assert claimed is not None
assert claimed.identity_key == pending_identity
```

- [ ] **Step 2: Run the claim-order test and confirm RED**

```bash
uv run pytest tests/test_acquisition_db.py -k 'pending_before_deferred' -q
```

Expected: the due deferred assignment is selected first.

- [ ] **Step 3: Change automatic claim ordering**

Change the SQL phase ordering in `claim_next_assignment()` so `pending` sorts
before `deferred`. Do not change phase eligibility, inter-device gaps, leases,
operator controls, or order keys within each phase.

- [ ] **Step 4: Write failing tests for manual retry hold**

Extend the persistent-uncertain worker test to assert:

```python
assert stored.phase is AssignmentPhase.DEFERRED
assert stored.next_attempt_at_ms == MANUAL_RETRY_AT_MS
assert repository.claim_next_assignment(
    assignment.round_id, "phone-01", "worker-2", now_ms=10**15
) is None
```

Add a test where an assignment already has `visit_confirmed_at_ms`, reopening
raises `ProfileUnreachable`, and the assignment remains deferred/manual rather
than skipped or scheduled five minutes later.

- [ ] **Step 5: Run the worker tests and confirm RED**

```bash
uv run pytest tests/test_mobile_worker.py -k 'uncertain or confirmed_visit' -q
```

Expected: current deferral uses `now_ms + retry_delay_ms`.

- [ ] **Step 6: Implement manual retry hold**

Define a shared SQLite-safe manual retry timestamp constant. Allow
`defer_assignment()` to receive a manual-hold flag and persist that timestamp.
Use manual hold after one uncertain reconciliation and when `ProfileUnreachable`
occurs on an assignment with an existing confirmed visit. Preserve the existing
operator retry path, which sets `next_attempt_at_ms = 0`.

- [ ] **Step 7: Run focused verification**

```bash
uv run pytest tests/test_acquisition_db.py tests/test_mobile_worker.py -q
uv tool run ruff check src/tikpoc/acquisition_db.py src/tikpoc/mobile_worker.py tests/test_acquisition_db.py tests/test_mobile_worker.py
uv tool run ruff format --check src/tikpoc/acquisition_db.py src/tikpoc/mobile_worker.py tests/test_acquisition_db.py tests/test_mobile_worker.py
git diff --check
```

- [ ] **Step 8: Commit Task 2**

```bash
git add src/tikpoc/acquisition_db.py src/tikpoc/mobile_worker.py tests/test_acquisition_db.py tests/test_mobile_worker.py
git commit -m "fix: prevent deferred retry starvation"
```

### Task 3: Full Regression And Same-Round Deployment

**Files:**
- Modify: `docs/mobile-fleet-runbook.md` only for the new recovery policy
- Modify: `docs/superpowers/specs/2026-07-20-six-device-throughput-recovery-design.md`

- [ ] **Step 1: Run complete automated verification**

```bash
uv run pytest -q
uv tool run ruff check src tests
uv tool run ruff format --check src tests
node --test chrome-event-bridge/*.test.js
bash android-event-bridge/build.sh
git diff --check
```

Record unrelated failures separately and verify the touched focused suites pass.

- [ ] **Step 2: Update operational documentation**

Document that mobile acquisition never opens Inbox, pending work precedes
deferred repair, and one durable uncertain reconciliation moves to manual hold.

- [ ] **Step 3: Commit documentation and push GitHub**

```bash
git add docs/mobile-fleet-runbook.md docs/superpowers/specs/2026-07-20-six-device-throughput-recovery-design.md
git commit -m "docs: record retry starvation recovery"
git push origin feat/web-lead-conversion
```

- [ ] **Step 4: Verify the paused checkpoint before deployment**

Confirm the CSV checksum, round ID, paused state, zero active assignment leases,
zero active worker leases, and the completed/skipped/deferred/pending counts.

- [ ] **Step 5: Start the updated six-device fleet and resume the same round**

Start `fleet-run` against `/Users/Shared/TikPoc/tikpoc.db` and
`round-ae3616f70853b1901e95`, then issue the durable round start command. Do not
create a new round or re-import the CSV.

- [ ] **Step 6: Run live acceptance**

For at least ten stable minutes verify six healthy workers, six healthy proxy
probes, no Inbox routes, no duplicate action attempts, no identity mismatch
completion, confirmed actions for eligible interaction completions, and measured
per-device throughput. Report slot 4 separately.

### Task 4: Whole-Project Coupling And Business-Risk Audit

**Files:**
- Create: `docs/reviews/2026-07-21-project-coupling-business-risk-review.md`
- Modify only files needed for Critical or Important fixes, each through a
  separate red-green task and commit.

- [ ] **Step 1: Map runtime ownership and database boundaries**

Audit fleet supervisor/worker fencing, device adapter responsibilities,
repository transaction boundaries, mobile/browser separation, multiple SQLite
processes, launchd services, and configuration loading.

- [ ] **Step 2: Inspect business-critical coupling**

Review eligibility-to-snapshot-to-plan flow, quota reservation, uncertain action
handling, assignment coverage, browser idempotency, AI reply immutability,
follow-back leases, funnel events, and Supabase synchronization boundaries.

- [ ] **Step 3: Run static and dependency-oriented checks**

```bash
uv tool run ruff check src tests
uv run pytest -q
rg -n "tiktok://inbox|terminate_app|activate_app|next_attempt_at_ms|BEGIN IMMEDIATE|sqlite3.connect" src/tikpoc
git status --short --branch
```

Inspect large/high-coupling modules manually and record evidence, severity,
business impact, and the smallest recommended boundary change.

- [ ] **Step 4: Fix only Critical and Important findings**

For every accepted finding: add one failing behavioral test, observe RED,
implement the smallest fix, run focused and full verification, and commit the
finding separately. Leave small-value cleanup documented rather than delaying
production work.

- [ ] **Step 5: Publish the audit and keep devices running**

Commit and push the review document. Continue monitoring throughput, deferred
growth, uncertain counts, proxy health, and device-specific tails while the
same round remains active.
