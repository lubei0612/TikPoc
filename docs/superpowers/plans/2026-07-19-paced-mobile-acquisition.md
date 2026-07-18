# Paced Mobile Acquisition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pace rolling-hour interaction quotas, accept one-post profiles, and reduce verified TikTok navigation latency.

**Architecture:** SQLite owns durable token state and exact rolling reservations. The planner selects only due actions with headroom; the device route uses native stable IDs with staged visible readiness and bounded recovery.

**Tech Stack:** Python 3.12, SQLite WAL, Appium/UiAutomator2, ADB, pytest, Ruff.

---

### Task 1: One-Post Eligibility

**Files:**
- Modify: `src/tikpoc/rules.py`
- Modify: `tests/test_rules.py`

- [ ] Add a failing test asserting `ProfileMetrics(11, 10, 1)` is eligible and `posts=0` returns `insufficient_posts`.
- [ ] Run `uv run pytest tests/test_rules.py -q` and observe the one-post case fail.
- [ ] Change the post predicate from `posts <= 3` to `posts < 1` without changing the following/follower rule.
- [ ] Run focused tests and commit `feat: accept profiles with one post`.

### Task 2: Durable Rolling Pacing State

**Files:**
- Modify: `src/tikpoc/acquisition_db.py`
- Modify: `src/tikpoc/acquisition_models.py`
- Create: `tests/test_rolling_quota.py`

- [ ] Add failing migration and repository tests for one token bucket per `(device_id, outcome)`, deterministic fractional initialization, capacity-one refill, atomic consume, and timestamps that never move backward.
- [ ] Add a migration-safe `action_pacing_state(device_id, outcome, tokens, updated_at_ms)` table and typed pacing snapshot.
- [ ] Add a rolling usage query over non-trace plans created in `(now_ms - 3_600_000, now_ms]`, including planned/executing/uncertain/confirmed states.
- [ ] Add one transaction that checks rolling headroom, refills and consumes the token, and creates the immutable action plan.
- [ ] Run `uv run pytest tests/test_rolling_quota.py tests/test_acquisition_db.py -q` and commit `feat: persist rolling action pacing`.

### Task 3: Dynamic Weighted Outcome Planning

**Files:**
- Modify: `src/tikpoc/outcome_planner.py`
- Modify: `tests/test_outcome_planner.py`

- [ ] Replace equal four-way draws with tests for due-action candidates, deterministic weighted selection, trace when no token is due, quota headroom exclusion, and immutable retry reuse.
- [ ] Implement candidate weights from full token amount plus deterministic seed jitter; consume only the selected action token.
- [ ] Simulate 3,600 seconds of dense eligible traffic and assert action timestamps span the hour, counts reach 100/14/25, and no rolling interval exceeds a limit.
- [ ] Run focused planner/DB tests and commit `feat: pace dynamic mobile outcomes`.

### Task 4: Native Route And Readiness Timing

**Files:**
- Modify: `src/tikpoc/device.py`
- Modify: `src/tikpoc/fleet_runtime.py`
- Modify: `src/tikpoc/acquisition_models.py`
- Modify: `tests/test_appium_device.py`
- Modify: `tests/test_fleet_runtime.py`

- [ ] Add failing tests for ADB stable-ID route dispatch, previous-profile rejection, metric readiness, Inbox baseline retry, one app restart, and terminal staged errors.
- [ ] Introduce an injectable route runner using `adb -s SERIAL shell am start -W -a android.intent.action.VIEW -d URI PACKAGE` and retain Appium only for visible verification.
- [ ] Read current resource IDs directly, use bounded XML fallback, and require identity plus metric/post readiness before returning.
- [ ] Persist phase timings for route, identity, metrics, video, and action readiness.
- [ ] Run device/fleet/mobile-worker tests and commit `perf: verify fast stable profile routes`.

### Task 5: Rolling Quota API And Console Data

**Files:**
- Modify: `src/tikpoc/acquisition_service.py`
- Modify: `src/tikpoc/api.py`
- Modify: `tests/test_acquisition_api.py`

- [ ] Add failing API tests for rolling used/limit, reserved, uncertain, token readiness, next due time, and candidate weight.
- [ ] Build these fields from one read transaction without exposing credentials or profile data.
- [ ] Preserve existing API enum values and return no fixed-hour reset claim.
- [ ] Run focused API tests and commit `feat: expose rolling quota pacing`.

### Task 6: Regression And Controlled Performance Gate

**Files:**
- Modify: `docs/mobile-fleet-runbook.md`
- Modify: `AGENTS.md`

- [ ] Run Python, Ruff, Chrome Node, Android build, and `git diff --check`.
- [ ] Run controlled trace/like/favorite/repost calibration and verify visible post-action state without duplicate clicks.
- [ ] Run a fresh 500-target slot-1 round, record measured mean/P90 separately from projection, and require mean `<6.5s`, P90 `<8.64s`, exact coverage, and zero uncertain work.
- [ ] Record results and commit `docs: record paced mobile acceptance`.

