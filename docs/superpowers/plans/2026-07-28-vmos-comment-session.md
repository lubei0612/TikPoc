# VMOS Brand Comment Session Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the desktop discovery service create reviewed employee-persona comment plans and let each autonomous VMOS APK combine relevant browsing with at most 20 visibly confirmed first-level TikTok comments per account per day.

**Architecture:** Extend the existing autonomous HTTPS mobile task plane with a typed `brand_comment` queue rather than creating another transport. Python owns video intake, imported hot-comment evidence, persona drafting, review, quotas, leases, and metrics; Android owns visible navigation, comment submission, interruption classification, recovery, and reconciliation. VMOS Automation is an observed reference only; production work runs in the TikPoc APK.

**Tech Stack:** Python 3.14, FastAPI, SQLite WAL, Android Java 8 AccessibilityService, VMOS OpenAPI, pytest, Ruff, Java build tests.

---

## File Structure

- Create `src/tikpoc/hot_comment_planner.py`: pure evidence scoring, structure clustering, and immutable candidate validation.
- Create `src/tikpoc/comment_sessions.py`: quota, plan state, task leasing, reconciliation, and observations.
- Modify `src/tikpoc/acquisition_db.py`: atomic persistence for comment sessions.
- Modify `src/tikpoc/api.py`, `api_models.py`, and `cli.py`: desktop intake/review and mobile task contracts.
- Create `android-touch-executor/src/com/tikpoc/touch/TikTokInterruptionSemantics.java`: pure surface classification.
- Create `android-touch-executor/src/com/tikpoc/touch/CommentTaskExecutor.java`: typed first-level comment state machine.
- Modify the Android dispatcher, service, runner, protocol, and build script.
- Create `docs/runbooks/vmos-brand-comment-sessions.md`: operation, recovery, and live gates.

## Task 1: Classify TikTok Interruptions Without Acting

**Files:**
- Create: `android-touch-executor/src/com/tikpoc/touch/TikTokInterruptionSemantics.java`
- Create: `android-touch-executor/test/com/tikpoc/touch/TikTokInterruptionSemanticsTest.java`
- Modify: `android-touch-executor/build.sh`

- [ ] **Step 1: Write the failing pure Java tests**

Add fixtures with exact visible text and require:

```java
check(classify(feed()).equals("none"), "feed");
check(classify(text("与好友一起使用 TikTok 会更有趣")).equals("ordinary_dialog"), "friends");
check(classify(text("为什么看到此作品")).equals("long_press_menu"), "menu");
check(classify(text("请完成下列验证后继续:")).equals("verification_required"), "zh verify");
check(classify(text("Verify to continue")).equals("verification_required"), "en verify");
```

- [ ] **Step 2: Run `bash android-touch-executor/build.sh`**

Expected: compilation fails because `TikTokInterruptionSemantics` is absent.

- [ ] **Step 3: Implement the pure classifier**

Inspect only visible, enabled nodes with screen area. Return immutable values `none`, `ordinary_dialog`, `long_press_menu`, or `verification_required`. Match normalized visible phrases and perform no action.

- [ ] **Step 4: Register the source/test and run all Android tests**

Expected: the new test and every existing Java test pass.

- [ ] **Step 5: Commit**

```bash
git add android-touch-executor/src/com/tikpoc/touch/TikTokInterruptionSemantics.java \
  android-touch-executor/test/com/tikpoc/touch/TikTokInterruptionSemanticsTest.java \
  android-touch-executor/build.sh
git commit -m "feat: classify TikTok interruption surfaces"
```

## Task 2: Add Bounded Home Recovery And Verification Pause

**Files:**
- Modify: `android-touch-executor/src/com/tikpoc/touch/Protocol.java`
- Modify: `android-touch-executor/src/com/tikpoc/touch/TouchCommandDispatcher.java`
- Modify: `android-touch-executor/src/com/tikpoc/touch/TikPocAccessibilityService.java`
- Modify: `android-touch-executor/src/com/tikpoc/touch/AccessibilityUiAdapter.java`
- Modify: `android-touch-executor/test/com/tikpoc/touch/TouchCommandDispatcherTest.java`
- Modify: `android-touch-executor/test/com/tikpoc/touch/AccessibilityUiAdapterTest.java`

- [ ] **Step 1: Add red dispatcher tests**

Require `recover_home` to return `verification_required` without actuator calls for a challenge. Require an ordinary dialog to invoke one dismiss/back and one Home activation, succeeding only after a later Home/Recommended snapshot. A failed recovery returns `home_recovery_failed` with no target interaction.

- [ ] **Step 2: Run the Android build**

Expected: unsupported-command assertions fail.

- [ ] **Step 3: Add the typed contract**

Add `observe_interruption` and `recover_home` to the protocol. Extend the actuator:

```java
default boolean dismissOrdinaryInterruption(String kind) throws Exception { return false; }
default boolean returnToHome() throws Exception { return false; }
```

`observe_interruption` returns classification and digest. `recover_home` performs no mutation for verification; otherwise it allows at most one dismiss/back and one Home navigation.

- [ ] **Step 4: Implement Android recovery actions**

Click only exact ordinary dismiss controls (`不允许`, `Not now`, `关闭`, `Close`); otherwise use one global Back. Exit a long-press menu with one Back. Launch TikTok when needed, click exact `首页` or `Home`, and verify the surface. Do not clear application data or loop recovery.

- [ ] **Step 5: Preserve a verification-blocked task**

Propagate `UiException("verification_required")`; keep the task in the local queue and suppress new comment claims.

- [ ] **Step 6: Run Android tests and commit**

```bash
bash android-touch-executor/build.sh
git add android-touch-executor/src android-touch-executor/test
git commit -m "feat: recover bounded TikTok home sessions"
```

## Task 3: Persist Videos, Evidence, Personas, And Plans

**Files:**
- Modify: `src/tikpoc/acquisition_db.py`
- Create: `src/tikpoc/hot_comment_planner.py`
- Create: `src/tikpoc/comment_sessions.py`
- Create: `tests/test_hot_comment_planner.py`
- Create: `tests/test_comment_sessions.py`

- [ ] **Step 1: Write failing tests**

Cover canonical video ID extraction; evidence deduplication by `cid`; ranking by likes, replies, and age; persona assignment; English plus Chinese validation; immutable approval; unique `(account_id, video_id)`; and a local-day quota counting visible-confirmed plus unresolved submissions.

- [ ] **Step 2: Run focused tests**

`uv run pytest tests/test_hot_comment_planner.py tests/test_comment_sessions.py -q`

Expected: missing-module failures.

- [ ] **Step 3: Add tables**

Add `comment_videos`, `comment_evidence`, `comment_personas`, `comment_plans`, `comment_attempts`, and `comment_observations`. Enforce unique `cid`, approved `(account_id, video_id)`, and attempt idempotency key. Keep plan states monotonic.

- [ ] **Step 4: Implement pure types**

```python
@dataclass(frozen=True)
class CommentEvidence:
    cid: str
    text: str
    likes: int
    replies: int
    created_at: int
    language: str

@dataclass(frozen=True)
class CommentCandidate:
    english: str
    chinese: str
    emoji_count: int
    persona_id: str
```

Validate 1..220 characters, English publish text, non-empty Chinese translation, at most two emoji code points, no URL/contact destination, and no exact normalized copy of imported evidence.

- [ ] **Step 5: Implement `CommentSessionService`**

Expose `add_video`, `import_evidence`, `save_candidate`, `approve_plan`, `claim_for_account`, `record_submission`, `record_reconciliation`, and `record_observation`. Use Asia/Shanghai local-day boundaries from an injected clock. Default hard account limit: 20.

- [ ] **Step 6: Verify and commit**

```bash
uv run pytest tests/test_hot_comment_planner.py tests/test_comment_sessions.py tests/test_acquisition_db.py -q
uv tool run ruff check src/tikpoc/hot_comment_planner.py src/tikpoc/comment_sessions.py tests/test_hot_comment_planner.py tests/test_comment_sessions.py
uv tool run ruff format --check src/tikpoc/hot_comment_planner.py src/tikpoc/comment_sessions.py tests/test_hot_comment_planner.py tests/test_comment_sessions.py
git add src/tikpoc/acquisition_db.py src/tikpoc/hot_comment_planner.py src/tikpoc/comment_sessions.py tests/test_hot_comment_planner.py tests/test_comment_sessions.py
git commit -m "feat: persist immutable brand comment plans"
```

## Task 4: Add Desktop Intake, Review, And Mobile Contracts

**Files:**
- Modify: `src/tikpoc/api_models.py`
- Modify: `src/tikpoc/api.py`
- Modify: `src/tikpoc/cli.py`
- Modify: `tests/test_acquisition_api.py`
- Modify: `tests/test_mobile_api.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Add failing API/CLI tests**

Test desktop intake of a canonical video URL, JSON evidence import from `followers`, candidate review/approval, redacted list output, mobile claim of `task_kind="brand_comment"`, replay, quota exhaustion, and verification preserving the lease.

- [ ] **Step 2: Run focused tests**

```bash
uv run pytest tests/test_acquisition_api.py tests/test_mobile_api.py tests/test_cli.py -q -k 'comment or mobile'
```

Expected: route and command failures.

- [ ] **Step 3: Add local operator endpoints**

```text
POST /api/comment-videos
POST /api/comment-videos/{video_id}/evidence
POST /api/comment-plans
POST /api/comment-plans/{plan_id}/approve
GET  /api/comment-plans
```

English and Chinese text appear only in the local operator response. Fleet/mobile summaries contain identifiers, states, timings, and counts.

- [ ] **Step 4: Add CLI commands**

Add `comment-video-add`, `comment-evidence-import`, `comment-plan-create`, `comment-plan-approve`, and `comment-plan-status --json`. Mutating commands accept replay-safe `--command-id`.

- [ ] **Step 5: Extend mobile pull/results**

Return immutable video URL, video ID, publish text, plan/attempt IDs, lease, and session epoch. Accept phases `video_opening`, `video_verified`, `comment_submitting`, and `comment_reconciling`. A verification result suppresses further claims for that account.

- [ ] **Step 6: Verify and commit**

```bash
uv run pytest tests/test_acquisition_api.py tests/test_mobile_api.py tests/test_cli.py tests/test_comment_sessions.py -q
uv tool run ruff check src/tikpoc/api.py src/tikpoc/api_models.py src/tikpoc/cli.py tests/test_acquisition_api.py tests/test_mobile_api.py tests/test_cli.py
git add src/tikpoc/api.py src/tikpoc/api_models.py src/tikpoc/cli.py tests/test_acquisition_api.py tests/test_mobile_api.py tests/test_cli.py
git commit -m "feat: expose reviewed brand comment tasks"
```

## Task 5: Execute And Reconcile One First-Level Comment

**Files:**
- Create: `android-touch-executor/src/com/tikpoc/touch/CommentTaskExecutor.java`
- Modify: `android-touch-executor/src/com/tikpoc/touch/Protocol.java`
- Modify: `android-touch-executor/src/com/tikpoc/touch/TouchCommandDispatcher.java`
- Modify: `android-touch-executor/src/com/tikpoc/touch/TikPocAccessibilityService.java`
- Modify: `android-touch-executor/src/com/tikpoc/touch/AutonomousTaskExecutor.java`
- Modify: `android-touch-executor/src/com/tikpoc/touch/AutonomousTaskRunner.java`
- Create: `android-touch-executor/test/com/tikpoc/touch/CommentTaskExecutorTest.java`
- Modify: `android-touch-executor/test/com/tikpoc/touch/TouchCommandDispatcherTest.java`
- Modify: `android-touch-executor/test/com/tikpoc/touch/AutonomousTaskExecutorTest.java`
- Modify: `android-touch-executor/build.sh`

- [ ] **Step 1: Write failing state-machine tests**

Require exact video verification before composer access, exactly one submit, visible text confirmation, checkpoint before submit, no repeat after transport loss, read-only uncertain reconciliation, and verification pause from every phase.

- [ ] **Step 2: Run the Android build**

Expected: missing command/executor failures.

- [ ] **Step 3: Add commands**

Add `open_comment_video`, `observe_comment_video`, `submit_first_level_comment`, and `observe_submitted_comment`. Open only canonical TikTok video routes, verify target evidence, open the first-level composer, set immutable text, and invoke one visible Post/Send control.

- [ ] **Step 4: Implement `CommentTaskExecutor`**

Persist `video_verified` before composer access and `comment_submitting` before the single submit. A post-submit transport loss becomes `uncertain`; the next run only observes. Exact visible text/account evidence becomes `visible_confirmed`.

- [ ] **Step 5: Combine relevant browsing**

When no comment is due, use existing `browse_home`. After visible confirmation, return Home and perform one bounded read-only browse. Do not add random follows, messages, or mandatory likes.

- [ ] **Step 6: Verify and commit**

```bash
bash android-touch-executor/build.sh
git add android-touch-executor/src android-touch-executor/test android-touch-executor/build.sh
git commit -m "feat: execute verified first-level comments"
```

## Task 6: Add Recovery Operations, Metrics, And Runbook

**Files:**
- Modify: `src/tikpoc/api.py`
- Modify: `src/tikpoc/acquisition_db.py`
- Modify: `src/tikpoc/cli.py`
- Modify: `tests/test_acquisition_api.py`
- Modify: `tests/test_cli.py`
- Create: `docs/runbooks/vmos-brand-comment-sessions.md`

- [ ] **Step 1: Add red recovery tests**

Require operator acknowledgement, unresolved-attempt quota retention, resume only after a fresh stable-Home heartbeat, and independence of other devices.

- [ ] **Step 2: Add recovery acknowledgement and metrics**

The acknowledgement records an operator event and requests bounded Home recovery. Expose planned, submitted, visible-confirmed, uncertain, verification-required, observed likes/replies, profile visits, follows, inbound messages, and qualified leads per account.

- [ ] **Step 3: Write the runbook**

Document desktop Chrome discovery, canonical URL intake, followers evidence import, English/Chinese review, approval, provisioning, the 20/day quota, verification handling, Home recovery, rollback, and redacted evidence retention.

- [ ] **Step 4: Verify and commit**

```bash
uv run pytest tests/test_acquisition_api.py tests/test_cli.py tests/test_comment_sessions.py -q
git add src/tikpoc/api.py src/tikpoc/acquisition_db.py src/tikpoc/cli.py tests/test_acquisition_api.py tests/test_cli.py docs/runbooks/vmos-brand-comment-sessions.md
git commit -m "feat: operate and observe VMOS comment sessions"
```

## Task 7: Full Verification And Single-Device Gates

**Files:**
- Modify: `docs/superpowers/specs/2026-07-28-vmos-comment-session-design.md` only for evidence-backed corrections.
- Modify: `AGENTS.md` only when the accepted business rule differs from its current mission.
- Use ignored local VMOS/server/provider configuration for live evidence.

- [ ] **Step 1: Run complete automated verification**

```bash
uv run pytest -q
uv tool run ruff check src tests
uv tool run ruff format --check src tests
bash android-touch-executor/build.sh
node --test chrome-event-bridge/*.test.js
git diff --check
```

- [ ] **Step 2: Install one VMOS canary**

Use ADB only for install/version verification, visibly re-enable Accessibility, provision HTTPS, confirm heartbeat, then close ADB. Do not run VMOS enhanced maintenance and TikPoc execution simultaneously.

- [ ] **Step 3: Run recovery gates**

Verify ordinary dialog dismissal, long-press exit, verification detection with zero challenge interaction, operator acknowledgement, TikTok relaunch, stable Home evidence, and checkpoint preservation.

- [ ] **Step 4: Run comment gates**

Use desktop Chrome to choose one video and import 100 comments from `followers`. Review English/Chinese, approve one plan, and require exact-video plus visible-comment evidence. Then run a five-comment session and one full 20-comment local day on one account.

- [ ] **Step 5: Report measured results**

Report submissions, visible confirmations, uncertainty, verification events, recovery success, comment likes/replies at 2/24 hours, profile visits, follows, inbound messages, and qualified leads. Promote to six accounts only with zero duplicates and complete visible evidence.

- [ ] **Step 6: Commit evidence and push**

```bash
git add docs/superpowers/specs/2026-07-28-vmos-comment-session-design.md AGENTS.md
git commit -m "docs: record VMOS comment session acceptance"
git push origin feat/web-lead-conversion
```
