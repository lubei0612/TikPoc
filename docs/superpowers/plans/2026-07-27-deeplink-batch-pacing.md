# Deeplink B-Strategy And Lightweight Pacing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Deeplink B-strategy interaction eligibility depend only on having a visible post and add deterministic lightweight per-device session pacing.

**Architecture:** Python owns immutable round navigation, eligibility, batching, coverage, and durable checkpoints. A pure Android pacing planner derives bounded delays and 40–80 target segment boundaries from the device identity; the autonomous runner applies delays and invokes a read-only home browse without changing target or action decisions.

**Tech Stack:** Python 3.14, SQLite, pytest, Java 8 Android accessibility service, shell build tests.

---

### Task 1: Unify Deeplink Eligibility On Visible Posts

**Files:**
- Modify: `src/tikpoc/rules.py`
- Modify: `tests/test_rules.py`
- Modify: `AGENTS.md`

- [ ] Add a failing test asserting that `evaluate_profile(ProfileMetrics(1, 999, 1))` is eligible and that zero posts remain ineligible.
- [ ] Run `uv run pytest tests/test_rules.py -q` and observe the follower-ratio assertion fail.
- [ ] Change `evaluate_profile` to require only `metrics.posts >= 1`, remove the ratio rejection, and set `POLICY_VERSION = "posts-gte-1-v2"`.
- [ ] Update the mobile touch-plane eligibility line in `AGENTS.md` to `video_count >= 1` and state that follower metrics are observational only.
- [ ] Run `uv run pytest tests/test_rules.py tests/test_worker.py tests/test_device_api.py -q`.
- [ ] Commit only these files with `git commit -m "feat: interact with every profile containing posts"`.

### Task 2: Add A Pure Deterministic Session Pacing Planner

**Files:**
- Create: `android-touch-executor/src/com/tikpoc/touch/SessionPacingPlanner.java`
- Create: `android-touch-executor/test/com/tikpoc/touch/SessionPacingPlannerTest.java`
- Modify: `android-touch-executor/build.sh`

- [ ] Add tests proving the same device and progress return the same plan, ordinary delay is within 200–1,200 ms, segment size is within 40–80, different devices produce differing schedules, and a segment boundary is due only at a positive multiple of the segment size.
- [ ] Run `bash android-touch-executor/build.sh` and observe compilation fail because `SessionPacingPlanner` is absent.
- [ ] Implement an immutable `Plan` with `delayMs`, `segmentSize`, and `homeBrowseDue`, deriving values from a SHA-256 digest of `deviceId + ':' + completedTargets`.
- [ ] Register the new source and test in `build.sh`.
- [ ] Run `bash android-touch-executor/build.sh` and require `SessionPacingPlannerTest PASS`.
- [ ] Commit with `git commit -m "feat: plan deterministic mobile session pacing"`.

### Task 3: Apply Pacing And Read-Only Home Browsing

**Files:**
- Modify: `android-touch-executor/src/com/tikpoc/touch/AutonomousTaskRunner.java`
- Modify: `android-touch-executor/src/com/tikpoc/touch/AutonomousTaskExecutor.java`
- Modify: `android-touch-executor/src/com/tikpoc/touch/AccessibilityUiAdapter.java`
- Modify: `android-touch-executor/src/com/tikpoc/touch/TouchCommandDispatcher.java`
- Modify: `android-touch-executor/src/com/tikpoc/touch/TikPocAccessibilityService.java`
- Modify: `android-touch-executor/test/com/tikpoc/touch/AutonomousTaskRunnerTest.java`
- Modify: `android-touch-executor/test/com/tikpoc/touch/TouchCommandDispatcherTest.java`

- [ ] Add runner tests with an injected sleeper and pacing planner proving one bounded delay per executed target and one home-browse call at a segment boundary.
- [ ] Add dispatcher tests proving `browse_home` activates only the TikTok home control, performs one bounded read-only swipe, and returns visible-state evidence without any target interaction.
- [ ] Run the two focused Java tests through `bash android-touch-executor/build.sh` and observe the missing interfaces fail compilation.
- [ ] Add injected `Sleeper`, completed-target counter, and pacing plan application after a result is durably queued.
- [ ] Extend `Ui` with `browseHomeReadOnly`; route it through an adapter `browse_home` request and dispatcher actuator methods for home navigation and one swipe.
- [ ] Return evidence containing `home_visible`, `browse_performed`, and elapsed time; propagate failure as pacing diagnostics without replaying a completed target.
- [ ] Run `bash android-touch-executor/build.sh` and all Android tests.
- [ ] Commit with `git commit -m "feat: apply bounded mobile session pacing"`.

### Task 4: Regression, Deployment, And Live Gates

**Files:**
- Modify: `docs/superpowers/specs/2026-07-27-deeplink-batch-pacing-design.md` only if implementation evidence requires clarification.
- Use ignored local VMOS/server configuration; do not commit it.

- [ ] Run `uv run pytest -q`, `uv tool run ruff check` and `uv tool run ruff format --check` on touched Python files, `bash android-touch-executor/build.sh`, and `git diff --check`.
- [ ] Build the APK, install it on the selected VMOS canary, visibly re-enable accessibility, and verify health before live work.
- [ ] Create a fresh immutable Deeplink 20-target round using B-strategy ordering; require exact identity, correct post eligibility, and no duplicate action.
- [ ] If the 20-target gate passes, run a fresh 100-target round and then a 30-minute canary.
- [ ] Report measured targets/hour, mean/p90 target duration, visit confirmations, eligible ratio, action confirmed/uncertain counts, retries, pacing overhead, and platform visitor-list comparison when available.
- [ ] Keep all devices paused after the gate until the live result is reviewed.
- [ ] Push the verified commits to `origin/feat/web-lead-conversion`.

